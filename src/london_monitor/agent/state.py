from typing import TypedDict

from london_monitor.models import (
    ChatRequest,
    ChatResponse,
    Claim,
    Draft,
    Evidence,
    Metric,
    MetricQuery,
    SearchQuery,
    Skill,
    Trace,
)


class AgentState(TypedDict, total=False):
    request: ChatRequest
    question: str
    supported: bool
    skills: list[Skill]
    metric_query: MetricQuery
    search_query: SearchQuery
    metrics: list[Metric]
    evidence: list[Evidence]
    facts: list[Claim]
    draft: Draft
    warnings: list[str]
    trace: Trace
    response: ChatResponse
