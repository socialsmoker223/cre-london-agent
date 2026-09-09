import logging
import re
import time
from calendar import monthrange
from collections import defaultdict
from datetime import date
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from london_monitor.agent.state import AgentState
from london_monitor.models import (
    ChatRequest,
    ChatResponse,
    Citation,
    Claim,
    Draft,
    MetricQuery,
    Retriever,
    SearchQuery,
    Store,
    Synthesizer,
    Trace,
)

LOG = logging.getLogger(__name__)
MARKETS = ["City", "West End", "Canary Wharf", "Midtown / Fringe"]


def build_graph(store: Store, retriever: Retriever, provider: Synthesizer):
    def node(state: AgentState, name: str) -> None:
        state["trace"].nodes.append(name)

    def understand(state: AgentState) -> dict:
        request = state["request"]
        question = request.question
        if request.previous_question:
            question = f"{request.previous_question}\nFollow-up: {question}"
        q = question.lower()
        supported = bool(
            re.search(
                r"\b(office|markets?|rents?|vacancy|take[ -]up|city|west end|canary|midtown|"
                r"supply|pre[ -]?lets?|interest|bank rate|macro|esg|hybrid|"
                r"quality|sources|demand)\b",
                q,
            )
        ) and not re.search(
            r"\b(weather|residential|industrial|logistics|retail|hotels?|bitcoin|paris)\b", q
        )
        trace = Trace(
            run_id=str(uuid4()),
            intent="London office intelligence" if supported else "unsupported",
            nodes=["understand_request"],
        )
        return {"question": question, "supported": supported, "trace": trace, "warnings": []}

    def route(state: AgentState) -> dict:
        node(state, "route_skills")
        q = state["question"].lower()
        markets = [m for m in MARKETS if m.lower() in q]
        if "midtown" in q or "fringe" in q:
            markets = list(dict.fromkeys([*markets, "Midtown / Fringe"]))
        skills = []
        if state["supported"]:
            if any(w in q for w in ("compare", "comparison", "differ")):
                skills.append("comparison")
            if any(w in q for w in ("supply", "pipeline", "pre-let", "prelet", "refurb")):
                skills.append("supply")
            if any(w in q for w in ("interest", "bank rate", "macro", "hybrid", "esg", "demand")):
                skills.append("macro")
            if any(
                w in q
                for w in (
                    "rent",
                    "vacancy",
                    "take-up",
                    "take up",
                    "changed",
                    "previous",
                    "disagree",
                    "conflict",
                )
            ):
                skills.append("metrics")
            if not skills and not any(w in q for w in ("quality", "evidence")):
                skills.append("market_pulse")
            skills.append("evidence")
        names = []
        if re.search(r"\brents?\b", q):
            names = ["grade_a_rent"] if "grade a" in q else ["prime_rent"]
        if "vacancy" in q:
            names.append("vacancy")
        if "take-up" in q or "take up" in q:
            names.append("take_up")
        if "interest" in q or "bank rate" in q:
            names.append("bank_rate")
        period_match = re.search(r"(20\d{2})[- ]?q([1-4])", q)
        period = f"{period_match[1]}-Q{period_match[2]}" if period_match else None
        as_of = None
        if period_match:
            year, month = int(period_match[1]), int(period_match[2]) * 3
            as_of = date(year, month, monthrange(year, month)[1])
        historical = any(w in q for w in ("change", "previous", "over time", "histor"))
        state["trace"].skills = skills
        return {
            "skills": skills,
            "metric_query": MetricQuery(
                submarkets=list(dict.fromkeys([*markets, "London"]))
                if markets and "bank_rate" in names
                else markets,
                metrics=names,
                period=period,
                latest=not historical,
            ),
            "search_query": SearchQuery(
                query=state["question"],
                submarkets=markets,
                as_of=as_of,
                current=any(w in q for w in ("latest", "current")),
            ),
        }

    def execute(state: AgentState) -> dict:
        node(state, "execute_tools")
        metrics, evidence, facts = [], [], []
        trace = state["trace"]
        warnings = state["warnings"]

        def attempt(name, fn, default):
            trace.tools.append(name)
            try:
                return fn()
            except Exception:
                LOG.exception("tool_failed run_id=%s tool=%s", trace.run_id, name)
                trace.failures.append(name)
                warnings.append(f"{name} unavailable; answer may be incomplete.")
                return default

        if set(state["skills"]) & {"market_pulse", "comparison", "metrics", "macro"}:
            metrics = attempt(
                "query_market_metrics", lambda: store.query_metrics(state["metric_query"]), []
            )
        if "evidence" in state["skills"]:
            evidence = attempt(
                "search_market_evidence", lambda: retriever.search(state["search_query"]), []
            )
        if "supply" in state["skills"]:
            projects = attempt(
                "get_supply_pipeline",
                lambda: store.get_projects(state["metric_query"].submarkets),
                [],
            )
            facts.extend(
                Claim(
                    text=(
                        f"{p.name} ({p.submarket}): {p.status}; expected completion "
                        f"{p.completion_date}; {p.size_sq_ft:g} sq ft; "
                        f"{p.prelet_status}."
                    ),
                    source_ids=[p.source_id],
                )
                for p in projects
            )
        trace.retrieval_count = len(evidence)
        return {"metrics": metrics, "evidence": evidence, "facts": facts, "warnings": warnings}

    def combine(state: AgentState) -> dict:
        node(state, "combine_evidence")
        facts = list(state["facts"])
        groups = defaultdict(list)
        for m in state["metrics"]:
            facts.append(
                Claim(
                    text=f"{m.submarket} {m.metric.replace('_', ' ')}: "
                    f"{m.value:g} {m.unit} ({m.period}).",
                    source_ids=[m.source_id],
                )
            )
            groups[(m.submarket, m.metric, m.period, m.unit)].append(m)
        for key, rows in groups.items():
            if len({r.value for r in rows}) > 1:
                state["warnings"].append(
                    f"Sources disagree on {key[0]} {key[1]} in {key[2]}; values shown separately."
                )
        if not state["metric_query"].latest:
            series = defaultdict(list)
            for (market, metric, _period, unit), rows in groups.items():
                if len({r.value for r in rows}) == 1:
                    series[(market, metric, unit)].append(rows[0])
            for (market, metric, unit), rows in series.items():
                ordered = sorted(rows, key=lambda r: r.period)
                if len(ordered) >= 2:
                    old, new = ordered[-2:]
                    delta_unit = "percentage points" if unit == "%" else unit
                    facts.append(
                        Claim(
                            text=f"{market} {metric.replace('_', ' ')} changed "
                            f"{new.value - old.value:+g} {delta_unit} from "
                            f"{old.period} to {new.period}.",
                            kind="calculation",
                            source_ids=list(dict.fromkeys([old.source_id, new.source_id])),
                        )
                    )
        if "comparison" in state["skills"]:
            comparisons = defaultdict(list)
            for (market, metric, period, unit), rows in groups.items():
                if len({r.value for r in rows}) == 1 and market != "London":
                    comparisons[(metric, period, unit)].append(rows[0])
            for (metric, period, unit), rows in comparisons.items():
                if len(rows) == 2:
                    low, high = sorted(rows, key=lambda r: r.value)
                    delta_unit = "percentage points" if unit == "%" else unit
                    facts.insert(
                        0,
                        Claim(
                            text=f"{high.submarket} {metric.replace('_', ' ')} is "
                            f"{high.value - low.value:g} {delta_unit} above {low.submarket} "
                            f"in {period}.",
                            kind="calculation",
                            source_ids=list(dict.fromkeys([low.source_id, high.source_id])),
                        ),
                    )
        # A missing requested period must not be answered with unrelated commentary.
        missing_period = state["metric_query"].period and not state["metrics"]
        if not missing_period:
            for evidence in state["evidence"]:
                if re.search(r"\d", evidence.excerpt):
                    state["warnings"].append(
                        "Numerical commentary was omitted; numbers require structured evidence."
                    )
                    continue
                facts.append(Claim(text=evidence.excerpt, source_ids=[evidence.source_id]))
            for skill, category, implication in [
                (
                    "supply",
                    "supply",
                    "Risk to monitor: pipeline timing and pre-let commitments "
                    "should be assessed together; planned space is not guaranteed availability.",
                ),
                (
                    "macro",
                    "macro",
                    "A lower policy rate alone would not demonstrate stronger "
                    "leasing demand; financing conditions and occupier decisions "
                    "need separate review.",
                ),
            ]:
                supporting = [
                    e.source_id
                    for e in state["evidence"]
                    if e.category == category and not re.search(r"\d", e.excerpt)
                ]
                if skill in state["skills"] and supporting:
                    facts.append(
                        Claim(
                            text=implication,
                            kind="interpretation",
                            source_ids=list(dict.fromkeys(supporting)),
                        )
                    )
        else:
            facts = []
        if not state["metrics"] and set(state["skills"]) & {"metrics", "comparison"}:
            state["warnings"].append("Requested structured metrics are unavailable.")
        return {"facts": facts, "warnings": state["warnings"]}

    def generate(state: AgentState) -> dict:
        node(state, "generate_answer")
        try:
            draft = (
                provider.synthesize(state["question"], state["facts"])
                if state["facts"]
                else Draft()
            )
        except Exception:
            state["trace"].failures.append("synthesis")
            state["warnings"].append("Model unavailable; showing verified evidence directly.")
            draft = Draft(claims=state["facts"])
        return {"draft": draft, "warnings": state["warnings"]}

    def verify(state: AgentState) -> dict:
        node(state, "verify_grounding")
        sources = {s.id: s for s in store.list_sources()}
        allowed = {c.model_dump_json() for c in state["facts"]}
        claims = state["draft"].claims
        if (not claims and state["facts"]) or any(
            c.model_dump_json() not in allowed or any(s not in sources for s in c.source_ids)
            for c in claims
        ):
            state["warnings"].append("Unverified model output rejected; using tool evidence.")
            claims = state["facts"]
        claims = [c for c in claims if all(s in sources for s in c.source_ids)]
        ids = list(dict.fromkeys(s for c in claims for s in c.source_ids))
        citations = [
            Citation(
                number=i + 1,
                source=sources[s],
                excerpt=next(c.text for c in claims if s in c.source_ids),
            )
            for i, s in enumerate(ids)
        ]
        demo = any(c.source.demo for c in citations)
        prefix = "DEMO DATA — synthetic assessment dataset.\n\n" if demo else ""
        if claims:
            periods = sorted({m.period for m in state["metrics"]})
            prefix += (
                f"Evidence snapshot{': ' + ', '.join(periods) if periods else ''}. "
                "This reflects stored evidence, not a live market feed.\n\n"
            )
            labels = {"calculation": "Calculated: ", "interpretation": "Interpretation: "}
            answer = prefix + "\n".join(
                f"• {labels.get(c.kind, '')}"
                f"{c.text} " + " ".join(f"[{ids.index(s) + 1}]" for s in c.source_ids)
                for c in claims
            )
        else:
            answer = "Insufficient evidence to answer this question from the London office dataset."
        if citations:
            newest = max(c.source.published_at for c in citations)
            if (date.today() - newest).days > 90:
                state["warnings"].append(
                    f"Stored evidence is stale: newest cited publication is {newest}."
                )
        state["trace"].usage = state["draft"].usage
        response = ChatResponse(
            answer=answer,
            claims=claims,
            citations=citations,
            metrics=state["metrics"],
            warnings=list(dict.fromkeys(state["warnings"])),
            trace=state["trace"],
            demo=demo,
            insufficient_evidence=not claims,
        )
        return {"response": response}

    graph = StateGraph(AgentState)
    nodes = {
        "understand_request": understand,
        "route_skills": route,
        "execute_tools": execute,
        "combine_evidence": combine,
        "generate_answer": generate,
        "verify_grounding": verify,
    }
    previous = START
    for name, function in nodes.items():
        graph.add_node(name, function)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()


def run_graph(graph, request: ChatRequest) -> ChatResponse:
    start = time.perf_counter()
    response = graph.invoke({"request": request})["response"]
    response.trace.duration_ms = round((time.perf_counter() - start) * 1000, 2)
    LOG.info("agent_run %s", response.trace.model_dump_json())
    return response
