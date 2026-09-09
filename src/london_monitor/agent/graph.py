import json
import re
import time
from datetime import UTC, datetime
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from london_monitor.agent.skills import SKILLS
from london_monitor.models import (
    AgentProvider,
    ChatRequest,
    ChatResponse,
    Citation,
    Claim,
    Evidence,
    Metric,
    MetricQuery,
    ResearchAnswer,
    ScrapeQuery,
    SearchQuery,
    Trace,
    WebSearchQuery,
)


class LiveState(TypedDict, total=False):
    request: ChatRequest
    messages: list[dict]
    trace: Trace
    evidence: dict[str, Evidence]
    metrics: list[Metric]
    rounds: int
    searches: int
    scrapes: int
    deadline: float
    pending: list
    answer_text: str
    warnings: list[str]
    incomplete: bool
    response: ChatResponse


TOOL_MODELS = {
    "query_market_metrics": (MetricQuery, "Query validated numeric observations and comparisons."),
    "search_market_evidence": (SearchQuery, "Semantic search over ingested full source evidence."),
    "search_web": (
        WebSearchQuery,
        "Discover current market sources; snippets cannot ground answers.",
    ),
    "scrape_source": (
        ScrapeQuery,
        "Read and index a public page or PDF; returns citeable evidence.",
    ),
}
TOOLS = [
    {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": model.model_json_schema()},
    }
    for name, (model, desc) in TOOL_MODELS.items()
]


def numbers(text: str) -> set[str]:
    return {
        value.replace(",", "").rstrip("0").rstrip(".") if "." in value else value.replace(",", "")
        for value in re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    }


def validate_answer(answer: ResearchAnswer, evidence: dict[str, Evidence]) -> list[str]:
    errors = []
    for claim in answer.claims:
        if any(i not in evidence for i in claim.evidence_ids):
            errors.append("Unknown evidence ID")
            continue
        support = " ".join(evidence[i].excerpt for i in claim.evidence_ids)
        if not numbers(claim.text) <= numbers(support):
            errors.append("Unsupported numerical claim")
        if claim.kind == "calculation" and not any(
            i.startswith("calc:") for i in claim.evidence_ids
        ):
            errors.append("Calculation requires deterministic tool evidence")
    if numbers(answer.conclusion):
        errors.append("Keep numerical conclusions in cited claims")
    if not answer.claims and not answer.insufficient_evidence:
        errors.append("Empty answer must identify insufficient evidence")
    return list(dict.fromkeys(errors))


def build_graph(service, provider: AgentProvider):
    def agent(state: LiveState):
        trace = state["trace"]
        trace.nodes.append("agent")
        remaining = state["deadline"] - time.monotonic()
        if remaining <= 0:
            return {
                "pending": [],
                "incomplete": True,
                "warnings": [*state["warnings"], "Research budget exhausted."],
            }
        try:
            turn = provider.complete(
                state["messages"],
                TOOLS if state["rounds"] < 6 else [],
                timeout=min(remaining, 90),
            )
            if state["rounds"] >= 6 and turn.tool_calls:
                raise ValueError("Tool round budget exhausted")
            for key, value in turn.usage.items():
                trace.usage[key] = trace.usage.get(key, 0) + value
            message = {"role": "assistant", "content": turn.content or None}
            if turn.reasoning_content:
                message["reasoning_content"] = turn.reasoning_content
            if turn.tool_calls:
                message["tool_calls"] = [
                    {
                        "id": t.id,
                        "type": "function",
                        "function": {"name": t.name, "arguments": t.arguments},
                    }
                    for t in turn.tool_calls
                ]
            return {
                "messages": [*state["messages"], message],
                "pending": turn.tool_calls,
                "answer_text": turn.content,
            }
        except Exception as exc:
            trace.failures.append(f"model:{type(exc).__name__}")
            return {
                "pending": [],
                "incomplete": True,
                "warnings": [*state["warnings"], "Model request failed; evidence-only fallback."],
            }

    def tools(state: LiveState):
        state["trace"].nodes.append("tools")
        messages = list(state["messages"])
        for call in state["pending"]:
            state["trace"].tools.append(call.name)
            remaining = state["deadline"] - time.monotonic()
            try:
                if remaining <= 0:
                    raise TimeoutError("Research deadline reached")
                if call.name not in TOOL_MODELS:
                    raise ValueError("Unknown tool")
                args = TOOL_MODELS[call.name][0].model_validate_json(call.arguments)
                if call.name == "search_web":
                    if state["searches"] >= 3:
                        raise ValueError("Web search budget exhausted")
                    state["searches"] += 1
                    result = {
                        "leads_only": True,
                        "results": [
                            h.model_dump(mode="json")
                            for h in service.web.search(args, timeout=min(30, remaining))
                        ],
                    }
                elif call.name == "scrape_source":
                    if state["scrapes"] >= 6:
                        raise ValueError("Page scrape budget exhausted")
                    state["scrapes"] += 1
                    result, chunks = service.collect(args, deadline=state["deadline"])
                    state["evidence"].update({e.id: e for e in chunks})
                    if result.get("warning"):
                        state["warnings"].append(result["warning"])
                        state["trace"].failures.append("extract_metrics:unavailable")
                        state["incomplete"] = True
                    for key, value in result.get("usage", {}).items():
                        state["trace"].usage[key] = state["trace"].usage.get(key, 0) + value
                elif call.name == "search_market_evidence":
                    chunks = service.retriever.search(args)
                    chunks = [e for e in chunks if service.is_live_source(e.source_id)]
                    if args.current and not args.as_of:
                        current_ids = service.current_source_ids()
                        chunks = [e for e in chunks if e.source_id in current_ids]
                    state["evidence"].update({e.id: e for e in chunks})
                    result = {"evidence": [e.model_dump(mode="json") for e in chunks]}
                else:
                    rows, chunks = service.metric_evidence(args)
                    state["metrics"] = list(
                        {m.model_dump_json(): m for m in [*state["metrics"], *rows]}.values()
                    )
                    state["evidence"].update({e.id: e for e in chunks})
                    result = {
                        "metrics": [m.model_dump(mode="json") for m in rows],
                        "evidence": [e.model_dump(mode="json") for e in chunks],
                    }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            except Exception as exc:
                state["trace"].failures.append(f"{call.name}:{type(exc).__name__}")
                state["warnings"].append(f"{call.name}: {str(exc)[:180]}")
                state["incomplete"] = True
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps({"error": str(exc)[:180]}),
                    }
                )
        state["trace"].retrieval_count = len(state["evidence"])
        return {**state, "messages": messages, "pending": [], "rounds": state["rounds"] + 1}

    def verify(state: LiveState):
        state["trace"].nodes.append("verify")
        evidence = state["evidence"]
        answer = None
        for attempt in range(2):
            try:
                candidate = ResearchAnswer.model_validate_json(state.get("answer_text", ""))
                errors = validate_answer(candidate, evidence)
                if errors:
                    raise ValueError("; ".join(errors))
                answer = candidate
                break
            except (ValidationError, ValueError) as exc:
                reason = (
                    "Malformed ResearchAnswer JSON"
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
                remaining = state["deadline"] - time.monotonic()
                if attempt or remaining <= 0 or not state.get("answer_text"):
                    break
                try:
                    turn = provider.complete(
                        [
                            *state["messages"],
                            {
                                "role": "user",
                                "content": (
                                    f"The answer was rejected: {reason}. "
                                    "Return corrected ResearchAnswer JSON only. "
                                    "Use a non-numerical heading. Put numbers in cited claims. "
                                    "Use valid evidence IDs. Calculations require tool evidence."
                                ),
                            },
                        ],
                        [],
                        timeout=min(remaining, 45),
                    )
                    state["answer_text"] = turn.content
                    for key, value in turn.usage.items():
                        state["trace"].usage[key] = state["trace"].usage.get(key, 0) + value
                except Exception:
                    break
        sources = {s.id: s for s in service.sources() if not s.demo}
        evidence = {
            i: e
            for i, e in evidence.items()
            if all(s in sources for s in (e.source_ids or [e.source_id]))
        }
        if answer is not None and any(
            i not in evidence for c in answer.claims for i in c.evidence_ids
        ):
            answer = None
        if answer is None:
            state["trace"].failures.append("verification:invalid_answer")
            state["incomplete"] = True
            state["warnings"].append("Answer verification failed; showing source excerpts only.")
            from london_monitor.models import AnswerClaim

            answer = ResearchAnswer(
                conclusion="Evidence-only fallback",
                claims=[
                    AnswerClaim(text=e.excerpt[:1800], evidence_ids=[i])
                    for i, e in list(evidence.items())[:8]
                ],
                insufficient_evidence=not evidence,
            )
        if (
            re.search(r"\b(latest|current|today|now)\b", state["request"].question, re.I)
            and not state["searches"]
        ):
            state["incomplete"] = True
            state["warnings"].append("No live search completed; this answer uses stored evidence.")
        used = list(dict.fromkeys(i for c in answer.claims for i in c.evidence_ids))
        source_ids = list(
            dict.fromkeys(
                s for i in used for s in (evidence[i].source_ids or [evidence[i].source_id])
            )
        )
        state["trace"].source_count = len(source_ids)
        citations = [
            Citation(
                number=n + 1,
                source=sources[s],
                excerpt=next(
                    evidence[i].excerpt
                    for i in used
                    if s in (evidence[i].source_ids or [evidence[i].source_id])
                ),
            )
            for n, s in enumerate(source_ids)
        ]
        claims = [
            Claim(
                text=c.text,
                kind=c.kind,
                source_ids=list(
                    dict.fromkeys(
                        s
                        for i in c.evidence_ids
                        for s in (evidence[i].source_ids or [evidence[i].source_id])
                    )
                ),
            )
            for c in answer.claims
        ]
        body = (
            answer.conclusion
            + "\n\n"
            + "\n\n".join(
                f"{'Interpretation: ' if c.kind == 'interpretation' else ''}{c.text} "
                + " ".join(f"[{source_ids.index(s) + 1}]" for s in c.source_ids)
                for c in claims
            )
        )
        if not claims:
            body = "Insufficient evidence to answer. " + answer.conclusion
        if any(not c.source.trusted for c in citations):
            state["warnings"].append(
                "Some evidence is outside the preferred broker, official and developer sources; "
                "treat its authority as unconfirmed."
            )
        dates = [c.source.published_at for c in citations if c.source.published_at]
        freshness = (
            f"Latest cited publication: {max(dates)}" if dates else "Publication dates unknown"
        )
        if any(c.source.published_at is None for c in citations):
            state["warnings"].append("Some sources have no confirmed publication date.")
        response = ChatResponse(
            answer=body.strip(),
            claims=claims,
            citations=citations,
            metrics=state["metrics"],
            warnings=list(dict.fromkeys(state["warnings"])),
            trace=state["trace"],
            demo=False,
            mode="live",
            incomplete=state["incomplete"],
            insufficient_evidence=not claims,
            evidence=[evidence[i] for i in used],
            conversation_id=state["request"].conversation_id,
            freshness=freshness,
        )
        return {"response": response}

    graph = StateGraph(LiveState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_node("verify", verify)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", lambda s: "tools" if s.get("pending") else "verify")
    graph.add_edge("tools", "agent")
    graph.add_edge("verify", END)
    return graph.compile()


def run_graph(
    graph,
    request: ChatRequest,
    history: list[dict] | None = None,
    known_evidence: dict | None = None,
    transcript: list | None = None,
    saved_evidence: dict | None = None,
) -> ChatResponse:
    start = time.monotonic()
    question = request.question
    if request.previous_question:
        question = f"Previous question: {request.previous_question}\nFollow-up: {question}"
    state = graph.invoke(
        {
            "request": request,
            "messages": [
                {
                    "role": "system",
                    "content": SKILLS + f"\nToday's date: {datetime.now(UTC).date()}",
                },
                *(history or []),
                {"role": "user", "content": question},
            ],
            "trace": Trace(
                run_id=str(uuid4()),
                intent="Live London office research",
                skills=["market_pulse", "comparison", "supply", "macro"],
            ),
            "evidence": dict(known_evidence or {}),
            "metrics": [],
            "rounds": 0,
            "searches": 0,
            "scrapes": 0,
            "deadline": start + 180,
            "pending": [],
            "answer_text": "",
            "warnings": [],
            "incomplete": False,
        },
        config={"recursion_limit": 20},
    )
    if transcript is not None:
        transcript.extend(state["messages"][1 + len(history or []) :])
    if saved_evidence is not None:
        saved_evidence.update(dict(list(state["evidence"].items())[-100:]))
    response = state["response"]
    response.trace.duration_ms = round((time.monotonic() - start) * 1000, 1)
    return response
