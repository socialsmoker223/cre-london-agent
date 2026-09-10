"""Semantic Qdrant retrieval with model identity checks."""

import re
import threading
import uuid
from datetime import date
from pathlib import Path

from qdrant_client import QdrantClient, models

from .models import Evidence, Retriever, SearchQuery, Source, Submarket

_DIMENSION = 384
_COLLECTION = "evidence_bge_small_v1_5"
_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_MODEL_POINT = "00000000-0000-0000-0000-000000000001"


def chunk_text(text: str) -> list[tuple[str, str]]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    result = []
    for i, paragraph in enumerate(paragraphs, 1):
        words = paragraph.split()
        for j in range(0, len(words), 100):
            result.append(
                (
                    f"paragraph {i}"
                    if j == 0
                    else f"paragraph {i}, words {j + 1}-{min(j + 100, len(words))}",
                    " ".join(words[j : j + 100]),
                )
            )
    return result


class VectorIndex(Retriever):
    def __init__(self, path: str | Path | None = None, *, url=None, embedder=None, cache_dir=None):
        self._collection = _COLLECTION
        self._embedder = embedder
        self._model_name = getattr(embedder, "name", _MODEL_NAME)
        if embedder is None:
            from fastembed import TextEmbedding

            self._embedder = TextEmbedding(
                model_name="BAAI/bge-small-en-v1.5",
                cache_dir=str(cache_dir) if cache_dir else None,
                threads=2,
            )
        self._dimension = getattr(self._embedder, "dimension", _DIMENSION)
        self._client = QdrantClient(url=url) if url else QdrantClient(path=str(path))
        self._lock = threading.RLock()
        with self._lock:
            if self._client.collection_exists(self._collection):
                info = self._client.get_collection(self._collection)
                if info.config.params.vectors.size != self._dimension:
                    raise ValueError("incompatible embedding dimension")
                marker = self._client.retrieve(
                    collection_name=self._collection, ids=[_MODEL_POINT], with_payload=True
                )
                if not marker or (marker[0].payload or {}).get("model") != self._model_name:
                    raise ValueError("incompatible embedding model")
            else:
                self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=models.VectorParams(
                        size=self._dimension, distance=models.Distance.COSINE
                    ),
                )
                self._client.upsert(
                    collection_name=self._collection,
                    points=[
                        models.PointStruct(
                            id=_MODEL_POINT,
                            vector=[0.0] * self._dimension,
                            payload={"model": self._model_name, "metadata": True},
                        )
                    ],
                    wait=True,
                )

    def _embed(self, texts):
        # Bound inference memory while refresh and chat share one model instance.
        with self._lock:
            return [list(v) for v in self._embedder.embed(texts, batch_size=16)]

    def index(self, source: Source, text: str, submarket: Submarket, category: str) -> int:
        chunks = chunk_text(text)
        vectors = self._embed([x[1] for x in chunks])
        points = []
        for n, ((location, excerpt), vector) in enumerate(zip(chunks, vectors, strict=True)):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source.id}:{n}"))
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "source_id": source.id,
                        "excerpt": excerpt,
                        "location": location,
                        "category": category,
                        "submarket": submarket,
                        "published_at": source.published_at.isoformat()
                        if source.published_at
                        else None,
                        "published_ordinal": source.published_at.toordinal()
                        if source.published_at
                        else 0,
                        "title": source.title,
                        "publisher": source.publisher,
                        "url": str(source.url) if source.url else None,
                        "canonical_url": source.canonical_url,
                        "current": True,
                        "retrieved_at": source.retrieved_at.isoformat(),
                        "demo": source.demo,
                        "checksum": source.checksum,
                        "trusted": source.trusted,
                        "source_type": source.source_type,
                    },
                )
            )
        with self._lock:
            if points:
                self._client.upsert(collection_name=self._collection, points=points, wait=True)
                if source.canonical_url:
                    self._client.set_payload(
                        collection_name=self._collection,
                        payload={"current": False},
                        points=models.FilterSelector(
                            filter=models.Filter(
                                must=[models.FieldCondition(
                                    key="canonical_url",
                                    match=models.MatchValue(value=source.canonical_url),
                                )],
                                must_not=[models.FieldCondition(
                                    key="source_id",
                                    match=models.MatchValue(value=source.id),
                                )],
                            )
                        ),
                        wait=True,
                    )
        return len(points)

    def remove_sources(self, source_ids: list[str]) -> None:
        with self._lock:
            self._client.delete(
                collection_name=self._collection,
                points_selector=models.FilterSelector(filter=models.Filter(must=[
                    models.FieldCondition(key="source_id", match=models.MatchAny(any=source_ids))
                ])),
                wait=True,
            )

    def search(self, query: SearchQuery) -> list[Evidence]:
        must = []
        if query.category:
            must.append(
                models.FieldCondition(key="category", match=models.MatchValue(value=query.category))
            )
        if query.as_of:
            must.append(
                models.FieldCondition(
                    key="published_ordinal",
                    range=models.Range(gte=1, lte=query.as_of.toordinal()),
                )
            )
        if query.submarkets:
            must.append(
                models.FieldCondition(
                    key="submarket", match=models.MatchAny(any=[*query.submarkets, "London"])
                )
            )
        current_filter = []
        if query.current and not query.as_of:
            current_filter.append(
                models.FieldCondition(key="current", match=models.MatchValue(value=False))
            )
        with self._lock:
            response = self._client.query_points(
                collection_name=self._collection,
                query=self._embed([query.query])[0],
                query_filter=models.Filter(
                    must=must,
                    must_not=[
                        models.FieldCondition(key="metadata", match=models.MatchValue(value=True)),
                        *current_filter,
                    ],
                ),
                limit=query.limit,
                with_payload=True,
            )
        results = []
        for point in response.points:
            p = point.payload or {}
            results.append(
                Evidence(
                    id=str(point.id),
                    source_id=str(p["source_id"]),
                    excerpt=str(p["excerpt"]),
                    source_title=str(p.get("title", "")),
                    category=str(p["category"]),
                    submarket=p["submarket"],
                    published_at=date.fromisoformat(p["published_at"])
                    if p.get("published_at")
                    else None,
                    score=float(point.score),
                    location=str(p.get("location", "")),
                )
            )
        return results

    def close(self):
        with self._lock:
            self._client.close()
