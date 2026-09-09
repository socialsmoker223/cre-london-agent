from fastapi.testclient import TestClient

from london_monitor.api import create_app
from london_monitor.models import ChatResponse, IngestRequest, MetricQuery, RefreshResult, Trace


class StubService:
    def chat(self, request):
        return ChatResponse(
            answer=request.question,
            claims=[],
            citations=[],
            metrics=[],
            warnings=[],
            trace=Trace(run_id="test", intent="test"),
            demo=True,
        )

    def sources(self):
        return []

    def metrics(self, query: MetricQuery):
        self.query = query
        return []

    def ingest(self, request: IngestRequest):
        return {"chunks": 1, "duplicate": False}

    def status(self):
        return {"mode": "live", "status": "ok"}

    def latest_refresh(self):
        return None

    def refresh(self, request):
        return RefreshResult(
            run_id="r1", started_at="2026-09-09T00:00:00Z", completed_at="2026-09-09T00:00:01Z",
            status="complete", baseline=False, briefing="Synthetic refresh",
        )


def test_injected_service_and_static_page():
    service = StubService()
    with TestClient(create_app(service)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert (
            client.post("/api/chat", json={"question": "What is vacancy?"}).json()["answer"]
            == "What is vacancy?"
        )
        response = client.get("/api/metrics?submarkets=City&metrics=vacancy&period=2024-Q1")
        assert response.status_code == 200
        assert service.query.submarkets == ["City"]
        assert client.get("/").status_code == 200
        assert client.get("/api/status").json()["mode"] == "live"
        assert client.get("/api/refresh").json() is None
        assert client.post("/api/refresh", json={}).json()["status"] == "complete"


def test_validation_and_safe_errors():
    with TestClient(create_app(StubService())) as client:
        assert client.post("/api/chat", json={"question": "x"}).status_code == 422


def test_real_api_ingestion_and_safe_failure(tmp_path, monkeypatch):
    from london_monitor.service import MarketService

    service = MarketService(tmp_path)
    try:
        with TestClient(create_app(service)) as client:
            payload = {
                "title": "Synthetic user note",
                "publisher": "Assessment",
                "published_at": "2026-09-01",
                "demo": True,
                "text": "Office refurbishment can improve energy performance and tenant appeal.",
            }
            first = client.post("/api/ingest", json=payload)
            assert first.status_code == 200 and first.json()["chunks"] > 0
            second = client.post("/api/ingest", json=payload)
            assert second.json()["duplicate"]
            assert second.json()["source"]["id"] == first.json()["source"]["id"]
            chat = client.post("/api/chat", json={"question": "Compare City and West End rents"})
            assert chat.status_code == 200 and chat.json()["citations"]
            assert client.get("/api/metrics?limit=201").status_code == 422

            def fail(_request):
                raise RuntimeError("sensitive internal detail")

            monkeypatch.setattr(service, "chat", fail)
            failed = client.post("/api/chat", json={"question": "City rent"})
            assert failed.status_code == 500 and "sensitive" not in failed.text
    finally:
        service.close()
