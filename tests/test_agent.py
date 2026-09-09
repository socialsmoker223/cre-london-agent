from pathlib import Path

from london_monitor.models import ChatRequest, Claim, Draft
from london_monitor.service import MarketService


def test_evaluation_passes(tmp_path: Path):
    service = MarketService(tmp_path)
    try:
        from london_monitor.evaluation import evaluate

        result = evaluate(service)
        assert result["passed"] == result["total"] == 12, result
        assert service.sources()
        assert all(source.demo for source in service.sources())
    finally:
        service.close()


def test_provider_failure_falls_back_to_verified_evidence(tmp_path: Path):
    class BrokenProvider:
        def synthesize(self, question, facts):
            raise RuntimeError("offline")

    service = MarketService(tmp_path, provider=BrokenProvider())
    try:
        response = service.chat(ChatRequest(question="What is the latest City prime rent?"))
        assert response.claims
        assert "Model unavailable" in " ".join(response.warnings)
    finally:
        service.close()


def test_fabricated_provider_claim_is_rejected(tmp_path: Path):
    class Fabricator:
        def synthesize(self, question, facts):
            return Draft(claims=[Claim(text="Fabricated", source_ids=["unknown"])])

    service = MarketService(tmp_path, provider=Fabricator())
    try:
        from london_monitor.models import ChatRequest

        response = service.chat(ChatRequest(question="What is the latest City prime rent?"))
        assert "Unverified model output rejected" in " ".join(response.warnings)
        assert all("Fabricated" not in claim.text for claim in response.claims)
    finally:
        service.close()


def test_empty_retrieval_reports_tool_failure_and_fallback(tmp_path: Path):
    service = MarketService(tmp_path)
    try:
        service.retriever.search = lambda query: []
        response = service.chat(ChatRequest(question="What evidence is available?"))
        assert response.insufficient_evidence
        assert response.trace.retrieval_count == 0
    finally:
        service.close()


def test_retrieval_failure_is_reported_and_metrics_survive(tmp_path: Path):
    service = MarketService(tmp_path)
    try:

        def fail(query):
            raise RuntimeError("qdrant unavailable")

        service.retriever.search = fail
        response = service.chat(ChatRequest(question="What is the latest City prime rent?"))
        assert response.metrics
        assert "search_market_evidence unavailable" in " ".join(response.warnings)
    finally:
        service.close()


def test_historical_query_excludes_later_evidence(tmp_path: Path):
    service = MarketService(tmp_path)
    try:
        response = service.chat(ChatRequest(question="What was City vacancy in 2025-Q4?"))
        assert response.metrics
        assert all(metric.period == "2025-Q4" for metric in response.metrics)
        assert all("2026-Q1" not in claim.text for claim in response.claims)
    finally:
        service.close()


def test_follow_up_question_keeps_prior_context(tmp_path: Path):
    service = MarketService(tmp_path)
    try:
        response = service.chat(
            ChatRequest(question="How did it change?", previous_question="What is City prime rent?")
        )
        assert response.claims
        assert response.trace.intent != "unsupported"
    finally:
        service.close()


def test_modified_fact_with_valid_source_is_rejected(tmp_path: Path):
    class Modifier:
        def synthesize(self, question, facts):
            claim = Claim(
                text="City prime rent: 999 GBP/sq ft.",
                source_ids=[facts[0].source_ids[0]],
            )
            return Draft(claims=[claim])

    service = MarketService(tmp_path, provider=Modifier())
    try:
        response = service.chat(ChatRequest(question="What is the latest City prime rent?"))
        assert "Unverified model output rejected" in " ".join(response.warnings)
        assert all("999" not in claim.text for claim in response.claims)
    finally:
        service.close()


def test_empty_model_draft_falls_back_to_facts(tmp_path: Path):
    class Empty:
        def synthesize(self, question, facts):
            return Draft()

    service = MarketService(tmp_path, provider=Empty())
    try:
        response = service.chat(ChatRequest(question="What is the latest City prime rent?"))
        assert response.claims
        assert response.citations
    finally:
        service.close()


def test_unrelated_current_question_and_numeric_commentary(tmp_path):
    from datetime import date

    from london_monitor.models import IngestRequest

    service = MarketService(tmp_path)
    try:
        result = service.chat(ChatRequest(question="What is the current weather?"))
        assert result.insufficient_evidence and not result.trace.tools
        service.ingest(
            IngestRequest(
                title="Unverified numerical commentary",
                demo=True,
                publisher="Test",
                text="Office quality rent has reached 9999 GBP per square foot.",
                published_at=date.today(),
            )
        )
        result = service.chat(ChatRequest(question="What evidence supports office quality?"))
        assert "9999" not in result.answer
        assert any("Numerical commentary" in w for w in result.warnings)
    finally:
        service.close()


def test_macro_keeps_london_rate_and_labels_interpretation(tmp_path):
    service = MarketService(tmp_path)
    try:
        result = service.chat(ChatRequest(question="How might interest rates affect City offices?"))
        assert any(m.metric == "bank_rate" and m.submarket == "London" for m in result.metrics)
        assert any(c.kind == "interpretation" for c in result.claims)
        assert "Interpretation:" in result.answer
    finally:
        service.close()
