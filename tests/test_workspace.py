from collections import OrderedDict
from threading import Lock, RLock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from london_monitor.api import create_app
from london_monitor.db import Database
from london_monitor.models import ChatRequest, IngestRequest, Metric, MetricQuery, SearchQuery
from london_monitor.provider import ProviderUnavailable
from london_monitor.retrieval import VectorIndex
from london_monitor.service import MarketService


def test_remove_source_clears_versions_vectors_metrics_and_blocks_recrawl(tmp_path, monkeypatch):
    service = MarketService.__new__(MarketService)
    service.lock = RLock()
    service.refresh_lock = Lock()
    service.conversations = OrderedDict(old=([], {}))
    service.store = Database(tmp_path / "market.sqlite")
    service.retriever = VectorIndex(
        path=tmp_path / "vectors",
        embedder=SimpleNamespace(
            name="test",
            dimension=2,
            embed=lambda texts, **kwargs: [[1.0, 0.0] for _ in texts],
        ),
    )
    request = IngestRequest(
        title="City report",
        publisher="Test",
        text="City prime rent was £95 psf.",
        url="https://example.com/report",
    )
    try:
        first = service.ingest(request).source
        second = service.ingest(
            request.model_copy(update={"text": request.text + " Updated."})
        ).source
        keep = service.ingest(request.model_copy(update={"url": "https://example.com/keep"})).source
        service.store.add_metrics(
            [
                Metric(
                    metric="prime_rent",
                    value=95,
                    unit="GBP/sq ft",
                    period="2026-Q2",
                    submarket="City",
                    source_id=second.id,
                )
            ]
        )
        with monkeypatch.context() as patch:

            def fail_delete(ids):
                raise RuntimeError("index unavailable")

            patch.setattr(service.retriever, "remove_sources", fail_delete)
            with pytest.raises(RuntimeError, match="index unavailable"):
                service.remove_source(first.id)
            assert service.store.get_document(first.id) is not None
            assert not service.store.source_removed(first.canonical_url)
        with TestClient(create_app(service)) as client:
            service.refresh_lock.acquire()
            try:
                assert client.delete(f"/api/sources/{first.id}").status_code == 409
            finally:
                service.refresh_lock.release()
            assert client.delete(f"/api/sources/{first.id}").json() == {"removed": True}
            assert client.delete(f"/api/sources/{first.id}").status_code == 404
            assert [s["id"] for s in client.get("/api/sources").json()] == [keep.id]
        assert service.store.get_document(first.id) is None
        assert service.store.get_document(second.id) is None
        assert service.metrics(MetricQuery()) == []
        assert service.conversations == {}
        assert {e.source_id for e in service.retriever.search(SearchQuery(query="rent"))} == {
            keep.id
        }
        with pytest.raises(ValueError, match="removed"):
            service.ingest(request)
        # Tombstones survive restarts and reject changed content at the same URL.
        service.store.close()
        service.store = Database(tmp_path / "market.sqlite")
        with pytest.raises(ValueError, match="removed"):
            service.ingest(request.model_copy(update={"text": "A new City report edition."}))
    finally:
        service.close()


def test_model_selection_is_request_scoped_and_validated(monkeypatch):
    import london_monitor.service as module

    service = MarketService.__new__(MarketService)
    service.lock = RLock()
    service.refresh_lock = Lock()
    service.conversations = OrderedDict()
    service.provider = SimpleNamespace(name="z.ai", model="default-model")
    service.providers = {"z.ai": service.provider, "openai": SimpleNamespace(model="other-model")}
    service.graph = "default-graph"
    selected = []

    def build(selected_service, provider):
        assert selected_service.provider is provider
        selected.append(provider)
        return "selected-graph"

    def run(graph, *args):
        assert graph == "selected-graph"
        return SimpleNamespace(
            answer="No evidence", trace=SimpleNamespace(model_dump_json=lambda: "{}")
        )

    monkeypatch.setattr(module, "build_graph", build)
    monkeypatch.setattr(module, "run_graph", run)
    service.chat(ChatRequest(question="City rents", provider="openai", model="chosen-model"))
    assert selected[0].model == "chosen-model"
    assert service.provider.model == "default-model"
    assert service.providers["openai"].model == "other-model"
    del service.providers["openai"]
    with pytest.raises(ProviderUnavailable, match="not configured"):
        service.chat(ChatRequest(question="City rents", provider="openai"))
    with TestClient(create_app(service)) as client:
        for fields in ({"provider": "unknown"}, {"model": ""}, {"model": "   "}):
            assert (
                client.post("/api/chat", json={"question": "City rents", **fields}).status_code
                == 422
            )
