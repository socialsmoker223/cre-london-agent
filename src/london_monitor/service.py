import os
from pathlib import Path
from threading import RLock

from london_monitor.agent.graph import build_graph, run_graph
from london_monitor.db import Database
from london_monitor.demo import seed
from london_monitor.ingestion import ingest
from london_monitor.models import (
    ChatRequest,
    ChatResponse,
    IngestRequest,
    IngestResult,
    Metric,
    MetricQuery,
    Source,
    Synthesizer,
)
from london_monitor.provider import OfflineProvider, OpenAIProvider
from london_monitor.retrieval import VectorIndex


class MarketService:
    def __init__(self, data_dir: Path, provider: Synthesizer | None = None) -> None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Database(data_dir / "market.sqlite")
        self.retriever = VectorIndex(data_dir / "vectors")
        # ponytail: one process lock; use server stores for multi-worker throughput.
        self.lock = RLock()
        seed(self.store, self.retriever)
        if provider is None:
            name = os.environ.get("LLM_PROVIDER", "offline")
            if name not in {"offline", "openai"}:
                raise ValueError("LLM_PROVIDER must be offline or openai")
            provider = OpenAIProvider() if name == "openai" else OfflineProvider()
        self.graph = build_graph(self.store, self.retriever, provider)

    def chat(self, request: ChatRequest) -> ChatResponse:
        with self.lock:
            return run_graph(self.graph, request)

    def sources(self) -> list[Source]:
        with self.lock:
            return self.store.list_sources()

    def metrics(self, query: MetricQuery) -> list[Metric]:
        with self.lock:
            return self.store.query_metrics(query)

    def ingest(self, request: IngestRequest) -> IngestResult:
        with self.lock:
            return ingest(request, self.store, self.retriever)

    def close(self) -> None:
        self.retriever.close()
        self.store.close()
