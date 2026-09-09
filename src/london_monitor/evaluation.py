"""Small deterministic acceptance evaluation for the offline agent."""

import re

from .models import ChatRequest


def _grounded(response) -> bool:
    citation_ids = {citation.source.id for citation in response.citations}
    if not all(set(claim.source_ids) <= citation_ids for claim in response.claims):
        return False
    numbers = {citation.number for citation in response.citations}
    inline = {int(number) for number in re.findall(r"\[(\d+)\]", response.answer)}
    return bool(response.claims) == bool(response.citations) and (
        not response.claims or numbers == inline
    )


def _metrics(response, expected: set[tuple[str, str, float, str]]) -> bool:
    actual = {(m.submarket, m.metric, m.value, m.period) for m in response.metrics}
    return expected <= actual


def evaluate(service) -> dict:
    cases = [
        (
            "latest_metric",
            "What is the latest City prime rent?",
            lambda r: (
                _metrics(r, {("City", "prime_rent", 85.0, "2026-Q1")})
                and "metrics" in r.trace.skills
                and "query_market_metrics" in r.trace.tools
            ),
        ),
        (
            "comparison",
            "Compare City and West End prime rents.",
            lambda r: (
                _metrics(
                    r,
                    {
                        ("City", "prime_rent", 85.0, "2026-Q1"),
                        ("West End", "prime_rent", 121.0, "2026-Q1"),
                    },
                )
                and "comparison" in r.trace.skills
            ),
        ),
        (
            "supply",
            "What is in the office supply pipeline for the City?",
            lambda r: (
                "supply" in r.trace.skills and any("Rivergate House" in c.text for c in r.claims)
            ),
        ),
        (
            "macro",
            "What is the current interest rate outlook for London offices?",
            lambda r: (
                _metrics(r, {("London", "bank_rate", 4.0, "2026-Q1")}) and "macro" in r.trace.skills
            ),
        ),
        (
            "qualitative",
            "What does ESG and hybrid demand mean for office quality?",
            lambda r: bool(r.claims) and "evidence" in r.trace.skills,
        ),
        (
            "disagreement",
            "What is the City vacancy rate and do sources disagree?",
            lambda r: "Sources disagree" in " ".join(r.warnings) and len(r.metrics) >= 3,
        ),
        (
            "temporal_calculation",
            "How did City prime rent change over time?",
            lambda r: any(c.kind == "calculation" and "+3" in c.text for c in r.claims),
        ),
        (
            "historical",
            "What was City vacancy in 2025-Q4?",
            lambda r: (
                _metrics(r, {("City", "vacancy", 8.4, "2025-Q4")})
                and all(m.period == "2025-Q4" for m in r.metrics)
            ),
        ),
        (
            "absent_period",
            "What was City prime rent in 2024-Q1?",
            lambda r: r.insufficient_evidence and not r.metrics,
        ),
        (
            "unsupported",
            "What is the weather forecast in Paris?",
            lambda r: r.insufficient_evidence and r.trace.intent == "unsupported",
        ),
        (
            "trace",
            "Give me the latest London office market pulse.",
            lambda r: (
                r.trace.nodes
                == [
                    "understand_request",
                    "route_skills",
                    "execute_tools",
                    "combine_evidence",
                    "generate_answer",
                    "verify_grounding",
                ]
            ),
        ),
        ("grounding", "Give me the latest London office market pulse.", _grounded),
    ]
    results = []
    for name, question, check in cases:
        try:
            response = service.chat(ChatRequest(question=question))
            passed = bool(check(response)) and _grounded(response)
            detail = "ok" if passed else "assertion failed"
        except Exception as exc:  # evaluation should report all cases, not stop at one
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        results.append({"name": name, "passed": passed, "detail": detail})
    return {
        "passed": sum(item["passed"] for item in results),
        "total": len(results),
        "cases": results,
    }
