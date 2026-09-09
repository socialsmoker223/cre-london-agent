import time
from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from london_monitor.models import (
    ChangeQuery,
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
    from london_monitor.changes import metric_changes

    return [e.excerpt for e in metric_changes(
        previous, current, ChangeQuery(basis="last_update")
    )]


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
    current_sources = service.sources()
    live_ids = {s.id for s in current_sources}
    current_metrics = [
        m for m in service.store.query_metrics(MetricQuery(latest=False), unlimited=True)
        if m.source_id in live_ids
    ]
    changes = _metric_changes(old_metrics, current_metrics)
    from london_monitor.changes import change_evidence, source_snapshot, summarize_signals

    snapshot = source_snapshot(current_sources)
    new_sources = [sid for key, sid in snapshot.items() if key not in before]
    updated_sources = [
        sid for key, sid in snapshot.items() if key in before and before[key] != sid
    ]
    report, evidence = change_evidence(service, ChangeQuery(basis="last_update"))
    signals = []
    try:
        service.refresh_progress = {
            "stage": "Comparing risks and themes", "documents": len(visited)
        }
        signals = summarize_signals(service, report, evidence, deadline)
    except Exception as exc:
        failures.append(f"Evidence change analysis unavailable ({type(exc).__name__}).")
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
    if signals:
        briefing += "\n\n" + "\n".join(
            f"Interpretation — {c.change_status}: {c.text} [{', '.join(c.source_ids)}]"
            for c in signals
        )
    else:
        briefing += "\nNo supported text signal changes identified; this does not prove stability."
    if report["warnings"]:
        briefing += "\n" + "\n".join(report["warnings"])
    prior_signals = (previous.identified_signals or previous.evidence_changes) if previous else []
    # ponytail: retain 30 signal hypotheses; use a risk register if longer histories matter.
    identified_signals = list({c.text: c for c in [*prior_signals, *signals]}.values())[-30:]
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
        metric_snapshot=current_metrics,
        evidence_changes=signals,
        identified_signals=identified_signals,
        comparison_warnings=report["warnings"],
    )
    service.store.save_refresh(result)
    return result
