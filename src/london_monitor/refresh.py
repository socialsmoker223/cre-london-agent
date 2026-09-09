import time
from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from london_monitor.models import (
    MetricQuery,
    RefreshRequest,
    RefreshResult,
    ScrapeQuery,
    WebSearchQuery,
)

QUERIES = [
    ("City", "City London office market latest quarterly rents vacancy take-up report"),
    ("West End", "West End London office market latest quarterly report"),
    ("Canary Wharf", "Canary Wharf offices leasing refurbishment prelets latest"),
    ("Midtown / Fringe", "London Midtown Fringe office market latest supply report"),
    ("London", "Bank of England latest interest rate decision UK employment ONS office demand"),
]


def _metric_changes(previous, current):
    old, new = defaultdict(list), defaultdict(list)
    for rows, groups in ((previous, old), (current, new)):
        for item in rows:
            groups[(item.metric, item.submarket, item.unit, item.definition, item.period)].append(
                item
            )
    changes = []
    for key, items in new.items():
        before = old.get(key, [])
        if {(m.value, m.source_id) for m in before} == {(m.value, m.source_id) for m in items}:
            continue
        values = {m.value for m in items}
        sources = ", ".join(dict.fromkeys(m.source_id for m in items))
        metric, market, unit, definition, period = key
        label = f"{market} {metric} {period} ({definition})"
        if len(values) > 1:
            changes.append(
                f"Conflict: {label} has values "
                f"{', '.join(f'{value:g}' for value in sorted(values))} {unit}; sources {sources}."
            )
            continue
        kind = "Revision"
        if not before:
            older = [k for k in old if k[:4] == key[:4] and k[4] < period]
            before = old[max(older, key=lambda k: k[4])] if older else []
            kind = "Period change"
        old_values = {m.value for m in before}
        value = items[0].value
        if len(old_values) == 1 and (kind == "Period change" or old_values != values):
            prior = before[0]
            delta_unit = "percentage points" if unit == "%" else unit
            changes.append(
                f"{kind}: {label}: {prior.value:g} ({prior.period}) -> {value:g} {unit}; "
                f"difference {value - prior.value:+g} {delta_unit}; "
                f"sources {', '.join(dict.fromkeys(m.source_id for m in before))} -> {sources}."
            )
        elif not before or old_values != values:
            changes.append(
                f"New reported observation: {label} {value:g} {unit}; sources {sources}."
            )
    return changes


def refresh(service, request: RefreshRequest) -> RefreshResult:
    started = datetime.now(UTC)
    deadline = time.monotonic() + 300
    previous = service.store.latest_refresh()
    before = previous.source_snapshot if previous else {}
    old_metrics = previous.metric_snapshot if previous else []
    new_sources, updated_sources, failures = [], [], []
    unchanged = 0
    visited = set()
    candidates = defaultdict(list)
    for market, query in QUERIES:
        if time.monotonic() >= deadline:
            break
        try:
            service.refresh_progress = {"stage": f"Searching {market}", "documents": len(visited)}
            hits = service.web.search(
                WebSearchQuery(query=query, limit=3), timeout=min(30, deadline - time.monotonic())
            )
            if not hits:
                failures.append(f"No search results for {market}")
            candidates[market].extend(hits[:3])
        except Exception as exc:
            failures.append(f"{market}: {type(exc).__name__}: {str(exc)[:120]}")
    # Cover each topic before spending the remaining budget on a second source.
    selected = [
        (market, candidates[market][rank])
        for rank in range(3)
        for market, _ in QUERIES
        if len(candidates[market]) > rank
    ][:request.max_sources]
    for market, hit in selected:
        url = str(hit.url)
        if url in visited or time.monotonic() >= deadline:
            continue
        visited.add(url)
        try:
            service.refresh_progress = {
                "stage": f"Reading {hit.title or url}",
                "documents": len(new_sources) + len(updated_sources) + unchanged,
            }
            result, _ = service.collect(
                ScrapeQuery(
                    url=hit.url,
                    submarket=market,
                    category="macro" if market == "London" else "market_pulse",
                ),
                deadline,
            )
            source = result["source"]
            if source["id"] == before.get(source.get("canonical_url")):
                unchanged += 1
            elif source.get("canonical_url") in before:
                updated_sources.append(source["id"])
            else:
                new_sources.append(source["id"])
            if result.get("warning"):
                failures.append(f"{url}: {result['warning']}")
        except Exception as exc:
            failures.append(f"{url}: {type(exc).__name__}: {str(exc)[:120]}")
    if time.monotonic() >= deadline:
        failures.append("Refresh deadline reached")
    deadline_reached = time.monotonic() >= deadline
    current_metrics = service.metrics(MetricQuery(latest=False))
    changes = _metric_changes(old_metrics, current_metrics)
    covered = {
        market
        for market, _ in QUERIES
        if any(str(hit.url) in visited for hit in candidates[market])
    }
    budget_limited = len(covered) < len(QUERIES) and len(visited) >= request.max_sources
    if budget_limited:
        failures.append("Source budget left some topics unchecked")
    status = (
        "complete"
        if not failures and not budget_limited
        else "partial"
        if new_sources or updated_sources or unchanged or budget_limited or deadline_reached
        else "failed"
    )
    baseline = previous is None
    briefing = (
        "Baseline collection" if baseline else "Changes since the previous refresh"
    )
    briefing += (
        f": {len(new_sources)} new documents, {len(updated_sources)} revised documents, "
        f"{unchanged} unchanged documents.\n"
    )
    if changes:
        briefing += "\n" + "\n".join(changes)
    else:
        briefing += "No new comparable numerical observations were validated."
    if failures:
        briefing += (
            f"\nCollection incomplete: {len(failures)} failures. "
            "Unchecked sources may have changed."
        )
    snapshot = {}
    for source in sorted(service.sources(), key=lambda s: s.retrieved_at):
        if source.url:
            snapshot[source.canonical_url or str(source.url)] = source.id
    result = RefreshResult(
        run_id=str(uuid4()),
        started_at=started,
        completed_at=datetime.now(UTC),
        status=status,
        baseline=baseline,
        new_sources=new_sources,
        updated_sources=updated_sources,
        unchanged=unchanged,
        metric_changes=changes,
        failures=failures,
        briefing=briefing,
        source_snapshot=snapshot,
        metric_snapshot=service.metrics(MetricQuery(latest=False)),
    )
    service.store.save_refresh(result)
    return result
