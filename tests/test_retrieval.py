from datetime import date

from london_monitor.db import Database
from london_monitor.ingestion import ingest
from london_monitor.models import IngestRequest


def test_ingestion_is_duplicate_safe_and_retries_after_index_failure(tmp_path):
    class FailingOnce:
        def __init__(self):
            self.failed = False

        def index(self, *args):
            if not self.failed:
                self.failed = True
                raise RuntimeError("temporary failure")
            return 1

    request = IngestRequest(
        title="Market note",
        publisher="Demo",
        text="  Office   demand\n remains resilient.  ",
        published_at=date(2025, 1, 1),
    )
    store = Database(tmp_path / "market.sqlite")
    retriever = FailingOnce()
    try:
        ingest(request, store, retriever)
    except RuntimeError:
        pass
    assert store.list_sources() == []
    first = ingest(request, store, retriever)
    second = ingest(request, store, retriever)
    assert first.duplicate is False
    assert second.duplicate is True
    assert first.source.id == second.source.id
    assert first.source.checksum == second.source.checksum
    assert store.get_document(first.source.id).text
    store.close()


def test_recrawl_revalidates_retained_metrics_and_drops_removed_quotes(tmp_path):
    from types import SimpleNamespace

    from london_monitor.models import Metric, MetricQuery

    store = Database(tmp_path / "versions.sqlite")
    retriever = SimpleNamespace(index=lambda *args: 1)
    quote = "City prime rent in Q2 2026 was £95 psf."
    request = IngestRequest(title="Report", publisher="Broker", text=quote,
                            url="https://example.com/report")
    try:
        original = ingest(request, store, retriever)
        store.add_metrics([Metric(metric="prime_rent", value=95, unit="GBP/sq ft",
                                  period="2026-Q2", submarket="City", quotation=quote,
                                  source_id=original.source.id, definition="prime headline rent")])
        revised = ingest(request.model_copy(update={"text": quote + "\n\nNew footer"}),
                         store, retriever)
        rows = store.query_metrics(MetricQuery())
        assert len(rows) == 1 and rows[0].source_id == revised.source.id
        assert rows[0].quotation == quote
        ingest(request.model_copy(update={"text": "City prime rent in Q2 2026 was £96 psf."}),
               store, retriever)
        assert store.query_metrics(MetricQuery()) == []
    finally:
        store.close()
