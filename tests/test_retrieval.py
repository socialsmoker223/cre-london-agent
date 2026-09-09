from datetime import date

from london_monitor.db import Database
from london_monitor.ingestion import ingest
from london_monitor.models import IngestRequest, SearchQuery, Source
from london_monitor.retrieval import DemoVectorIndex, VectorIndex


def test_retrieval_filters_and_provenance(tmp_path):
    index = DemoVectorIndex(tmp_path / "qdrant")
    index.index(
        Source(id="city", title="City", publisher="p", published_at=date(2024, 1, 1), checksum="1"),
        "City offices see stronger leasing demand from financial firms.",
        "City",
        "market",
    )
    index.index(
        Source(
            id="wide", title="London", publisher="p", published_at=date(2025, 1, 1), checksum="2"
        ),
        "London offices see stronger leasing demand.",
        "London",
        "market",
    )
    index.index(
        Source(
            id="macro", title="Rates", publisher="p", published_at=date(2023, 1, 1), checksum="3"
        ),
        "Bank rates remain elevated.",
        "London",
        "macro",
    )

    results = index.search(SearchQuery(query="office leasing demand", submarkets=["City"]))
    assert {item.source_id for item in results} == {"city", "wide"}
    assert all(item.score > 0 for item in results)
    assert index.search(SearchQuery(query="office leasing", category="macro")) == []
    assert {
        item.source_id
        for item in index.search(SearchQuery(query="office", as_of=date(2024, 12, 31)))
    } == {"city"}
    index.close()


def test_as_of_excludes_undated_sources(tmp_path):
    index = DemoVectorIndex(tmp_path / "qdrant")
    index.index(
        Source(id="undated", title="Undated", publisher="p", checksum="u"),
        "Sustainable offices reduce energy consumption.", "London", "market"
    )
    assert index.search(SearchQuery(query="sustainable offices", as_of=date(2025, 1, 1))) == []
    index.close()


def test_current_search_excludes_superseded_url_version(tmp_path):
    class Embedder:
        dimension = 2
        name = "test"

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    index = VectorIndex(tmp_path / "qdrant", embedder=Embedder())
    for source_id in ("old", "new"):
        index.index(
            Source(id=source_id, title=source_id, publisher="p", published_at=date(2026, 1, 1),
                   checksum=source_id, canonical_url="https://example.test/report"),
            f"{source_id} sustainable workplace report.", "London", "market"
        )
    results = index.search(SearchQuery(query="sustainable workplace", current=True, limit=10))
    assert {item.source_id for item in results} == {"new"}
    index.close()


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
