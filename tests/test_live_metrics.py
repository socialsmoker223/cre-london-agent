from test_live_agent import live as live

from london_monitor.models import IngestRequest, Metric, MetricQuery


def test_comparisons_preserve_both_sources_and_conflicts(live):
    sources = [
        live.ingest(
            IngestRequest(
                title=f"Report {i}",
                publisher="Test",
                text=f"Report {i} on London office leasing markets.",
            )
        ).source
        for i in range(3)
    ]

    def row(i, value, market, unit="%", definition="office vacancy"):
        return Metric(
            metric="vacancy",
            value=value,
            unit=unit,
            period="2026-Q2",
            submarket=market,
            source_id=sources[i].id,
            definition=definition,
        )

    live.store.add_metrics([row(0, 5, "City"), row(1, 8, "West End")])
    _, evidence = live.metric_evidence(MetricQuery(latest=False))
    calculation = next(e for e in evidence if e.category == "calculation")
    assert set(calculation.source_ids) == {sources[0].id, sources[1].id}
    assert "percentage points" in calculation.excerpt
    live.store.add_metrics([row(2, 6, "City")])
    _, evidence = live.metric_evidence(MetricQuery(latest=False))
    assert not any(e.category == "calculation" for e in evidence)
    assert any(e.id.startswith("conflict:") for e in evidence)
    live.store.add_metrics([row(2, 50, "Canary Wharf", "basis points", "availability")])
    _, evidence = live.metric_evidence(MetricQuery(latest=False))
    assert not any(e.category == "calculation" for e in evidence)


def test_ingestion_preserves_trust_and_distinct_unlinked_publishers(live):
    request = IngestRequest(
        title="Report",
        publisher="A",
        text="Office leasing market report text.",
        trusted=True,
        source_type="broker",
    )
    first = live.ingest(request)
    second = live.ingest(request.model_copy(update={"publisher": "B"}))
    assert first.source.trusted and first.source.source_type == "broker"
    assert first.source.id != second.source.id
    assert live.ingest(request).duplicate


def test_followup_retains_tool_ids_and_known_evidence(live):
    from london_monitor.models import ChatRequest

    live.ingest(
        IngestRequest(
            title="Report",
            publisher="Test",
            text="Efficient offices appeal to occupiers seeking lower energy use.",
        )
    )
    first = live.chat(ChatRequest(question="What supports efficient offices?"))
    followup = live.chat(
        ChatRequest(
            question="Explain that evidence further.", conversation_id=first.conversation_id
        )
    )
    assert followup.citations and not followup.incomplete
    assert any(m.get("tool_call_id") == "lookup-1" for m in live.provider.calls[-1])


def test_repair_explains_numeric_heading_rejection(live):
    import json

    from london_monitor.models import ChatRequest, ModelTurn

    original = live.provider.complete
    draft = None
    repairs = []

    def complete(messages, tools, timeout):
        nonlocal draft
        if not tools:
            repairs.append(messages[-1]["content"])
            return ModelTurn(content=json.dumps({**draft, "conclusion": "Office quality"}))
        turn = original(messages, tools, timeout)
        if turn.content:
            draft = json.loads(turn.content)
            draft["conclusion"] = "Office quality in 2026"
            turn.content = json.dumps(draft)
        return turn

    live.provider.complete = complete
    live.ingest(
        IngestRequest(
            title="Report",
            publisher="Test",
            text="Efficient offices attract occupiers seeking lower energy use.",
        )
    )
    response = live.chat(ChatRequest(question="What supports efficient offices?"))
    assert repairs and "Keep numerical conclusions in cited claims" in repairs[0]
    assert not response.incomplete and response.citations
