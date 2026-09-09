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
