import time
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from london_monitor.agent.graph import build_graph, run_graph, validate_answer
from london_monitor.changes import (
    change_evidence,
    metric_changes,
    source_snapshot,
    summarize_signals,
)
from london_monitor.db import Database
from london_monitor.models import (
    AnswerClaim,
    ChangeQuery,
    ChatRequest,
    Evidence,
    Metric,
    ModelTurn,
    RefreshRequest,
    RefreshResult,
    ResearchAnswer,
    Source,
    ToolCall,
)
from london_monitor.refresh import refresh
from london_monitor.service import MarketService


def observation(source, value, period="2026-Q2", **kwargs):
    return Metric(
        metric="prime_rent",
        value=value,
        unit="GBP/sq ft",
        period=period,
        submarket="City",
        source_id=source,
        definition="prime headline rent",
        **kwargs,
    )


def test_period_movements_thresholds_zero_and_provenance():
    a, b = observation("a", 100, "2026-Q1"), observation("b", 105)
    query = ChangeQuery()
    evidence = metric_changes([], [a, b, b.model_copy(update={"source_id": "c"})], query)
    assert len(evidence) == 1
    assert evidence[0].source_ids == ["a", "b", "c"]
    assert "difference +5 GBP/sq ft; +5.00% relative change; Significant" in evidence[0].excerpt
    assert (
        "Below materiality"
        in metric_changes([], [a, b], ChangeQuery(relative_threshold_pct=6))[0].excerpt
    )
    assert (
        "zero baseline"
        in metric_changes([], [a.model_copy(update={"value": 0}), b], query)[0].excerpt
    )
    rates = [
        m.model_copy(update={"metric": "vacancy", "unit": "%", "value": v})
        for m, v in ((a, 5), (b, 5.5))
    ]
    text = metric_changes([], rates, query)[0].excerpt
    assert "difference +0.5 percentage points; +10.00%" in text
    assert "Significant movement" in text
    assert "-5.00%" in metric_changes([], [b.model_copy(update={"value": 95}), a], query)[0].excerpt


def test_conflicts_gaps_definitions_revisions_and_backfilled_periods():
    a, b = observation("a", 100, "2026-Q1"), observation("b", 105)
    query = ChangeQuery()
    for prior in (
        a.model_copy(update={"period": "2025-Q4"}),
        a.model_copy(update={"definition": "average rent"}),
        a.model_copy(update={"unit": "GBP/sq ft/year"}),
    ):
        evidence = metric_changes([], [prior, b], query)
        assert not any(e.id.startswith("calc:") for e in evidence)
    conflict = a.model_copy(update={"source_id": "conflict", "value": 99})
    assert metric_changes([], [a, b, conflict], query)[0].excerpt.startswith("Conflict:")
    revision = a.model_copy(update={"value": 101, "source_id": "revision"})
    assert (
        "Revision:" in metric_changes([a], [revision], ChangeQuery(basis="last_update"))[0].excerpt
    )
    # Older observations delivered together must still be compared quarter to quarter.
    changes = metric_changes([], [a, b], ChangeQuery(basis="last_update"))
    assert any("Period change:" in e.excerpt and "+5.00%" in e.excerpt for e in changes)
    assert metric_changes([a, b], [a, b], ChangeQuery(basis="last_update")) == []


def stored_service():
    service = MarketService.__new__(MarketService)
    service.store = Database()
    old = Source(
        id="old",
        title="Prior report",
        publisher="Broker A",
        checksum="old",
        canonical_url="https://example.test/report",
        published_at=date(2026, 3, 1),
        retrieved_at=datetime(2026, 3, 2, tzinfo=UTC),
        submarket="City",
    )
    service.store.save_document(old, "City supply constraints remain a leasing risk.")
    service.store.add_metrics([observation("old", 100, "2026-Q1")])
    snapshot = RefreshResult(
        run_id="prior",
        started_at=datetime(2026, 4, 1, tzinfo=UTC),
        completed_at=datetime(2026, 4, 1, tzinfo=UTC),
        status="complete",
        baseline=True,
        briefing="Baseline",
        source_snapshot=source_snapshot([old]),
        metric_snapshot=[observation("old", 100, "2026-Q1")],
    )
    service.store.save_refresh(snapshot)
    new = old.model_copy(
        update={
            "id": "new",
            "checksum": "new",
            "published_at": date(2026, 6, 1),
            "retrieved_at": datetime(2026, 6, 2, tzinfo=UTC),
        }
    )
    service.store.save_document(new, "City supply constraints have intensified as projects slip.")
    service.store.add_metrics([observation("new", 105)])
    return service


def test_document_windows_superseded_baselines_and_full_metric_snapshot():
    service = stored_service()
    try:
        report, refs = change_evidence(service, ChangeQuery(basis="last_update"))
        assert report["new_source_ids"] == ["new"]
        assert report["previous_boundary"] == "2026-04-01T00:00:00+00:00"
        assert {e.comparison_role for e in refs} >= {"previous", "new"}
        report, refs = change_evidence(service, ChangeQuery(period="2026-Q2"))
        assert report["previous_boundary"] == "2026-Q1"
        assert {e.source_id for e in refs if e.comparison_role} == {"old", "new"}
        assert any("+5.00%" in e.excerpt for e in refs)  # Superseded prior metric from snapshot.
        # An old publication newly ingested is new evidence, not a new publication.
        late = Source(
            id="late",
            title="Late",
            publisher="B",
            checksum="late",
            published_at=date(2020, 1, 1),
            submarket="City",
        )
        service.store.save_document(late, "Historical office demand softened.")
        report, refs = change_evidence(service, ChangeQuery(basis="last_update"))
        assert "late" in report["new_source_ids"]
        assert "2020-01-01" in next(e.excerpt for e in refs if e.source_id == "late")
        report, _ = change_evidence(service, ChangeQuery(period="2026-Q2"))
        assert "late" not in report["new_source_ids"]
        service.store.add_metrics(
            [
                observation("new", 105).model_copy(update={"definition": f"series {i}"})
                for i in range(205)
            ]
        )
        report, refs = change_evidence(service, ChangeQuery())
        assert len([e for e in refs
                    if e.id.startswith(("calc:change:", "observation:change:"))]) == 206
        assert any(e.id.startswith("mismatch:") for e in refs)
        service.store.connection.execute("UPDATE sources SET published_at=NULL WHERE id='old'")
        report, refs = change_evidence(service, ChangeQuery(period="2026-Q2"))
        assert not report["baseline"]  # A numerical baseline exists despite missing text dates.
        assert not any(e.comparison_role == "previous" for e in refs)
    finally:
        service.store.close()


def signal_refs():
    return {
        name: Evidence(
            id=name,
            source_id=name,
            excerpt="Supply risk is changing.",
            category="supply",
            submarket="City",
            comparison_role=role,
            publisher=publisher,
        )
        for name, role, publisher in (
            ("old", "previous", "A"),
            ("new", "new", "A"),
            ("other", "new", "B"),
        )
    }


@pytest.mark.parametrize(
    "status,ids,valid",
    [
        ("new", ["new"], True),
        ("strengthened", ["old", "new"], True),
        ("weakened", ["new"], False),
        ("weakened", ["old", "new"], True),
        ("contradictory", ["old", "new"], True),
        ("contradictory", ["new"], False),
        ("emerging", ["new", "other"], True),
        ("emerging", ["old", "new"], False),
    ],
)
def test_signal_validation_requires_comparison_and_independent_publishers(status, ids, valid):
    answer = ResearchAnswer(
        claims=[
            AnswerClaim(
                text="Supply risk is changing.",
                kind="interpretation",
                change_status=status,
                evidence_ids=ids,
            )
        ]
    )
    assert (not validate_answer(answer, signal_refs())) == valid


def test_change_tool_runs_through_graph_and_preserves_both_citations():
    service = stored_service()
    try:
        service.retriever = SimpleNamespace(search=lambda query: [])
        report, evidence = service.market_changes(ChangeQuery(basis="last_update"))
        calc = next(e for e in evidence if e.id.startswith("calc:"))
        turns = iter(
            [
                ModelTurn(
                    tool_calls=[
                        ToolCall(
                            id="changes",
                            name="compare_market_changes",
                            arguments='{"basis":"last_update"}',
                        )
                    ]
                ),
                ModelTurn(
                    content=ResearchAnswer(
                        conclusion="Significant rental movement",
                        claims=[
                            AnswerClaim(
                                text=calc.excerpt, kind="calculation", evidence_ids=[calc.id]
                            )
                        ],
                    ).model_dump_json()
                ),
            ]
        )
        provider = SimpleNamespace(complete=lambda *args, **kwargs: next(turns))
        response = run_graph(
            build_graph(service, provider),
            ChatRequest(question="What changed since the last update?"),
        )
        assert not response.incomplete
        assert {c.source.id for c in response.citations} == {"old", "new"}
        assert "+5.00%" in response.answer
        assert "compare_market_changes" in response.trace.tools
    finally:
        service.store.close()


def test_refresh_persists_signal_changes_and_handles_analysis_failure():
    service = stored_service()
    try:
        service.web = SimpleNamespace(search=lambda *args, **kwargs: [])
        report, refs = service.market_changes(ChangeQuery(basis="last_update"))
        ids = [e.id for e in refs if e.comparison_role]
        claim = AnswerClaim(
            text="Supply risk appears stronger.",
            kind="interpretation",
            change_status="strengthened",
            evidence_ids=ids,
        )
        service.provider = SimpleNamespace(
            complete=lambda *args, **kwargs: ModelTurn(
                content=ResearchAnswer(claims=[claim]).model_dump_json()
            )
        )
        result = refresh(service, RefreshRequest())
        assert result.updated_sources == ["new"]
        assert result.evidence_changes[0].source_ids == ["old", "new"]
        assert service.latest_refresh().evidence_changes[0].change_status == "strengthened"
        assert "Interpretation — strengthened" in result.briefing
        assert "+5.00%" in result.briefing
        unchanged = refresh(service, RefreshRequest())
        assert unchanged.evidence_changes == []
        assert unchanged.identified_signals == result.evidence_changes
        service.provider = SimpleNamespace(
            complete=lambda *args, **kwargs: ModelTurn(content="bad")
        )
        with pytest.raises(ValueError):
            summarize_signals(service, report, refs, time.monotonic() + 1)
        service.store.connection.execute("DELETE FROM refreshes WHERE run_id != 'prior'")
        failed = refresh(service, RefreshRequest())
        assert failed.status == "partial"
        assert any("Evidence change analysis unavailable" in f for f in failed.failures)
        assert failed.evidence_changes == []
        assert failed.metric_changes
    finally:
        service.store.close()
