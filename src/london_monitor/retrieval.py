"""Offline lexical retrieval backed by a local Qdrant collection."""

import hashlib
import re
import threading
import uuid
from datetime import date
from pathlib import Path

from qdrant_client import QdrantClient, models

from .models import Evidence, Retriever, SearchQuery, Source, Submarket

_DIMENSION = 384
_CHUNK_WORDS = 120
_CHUNK_OVERLAP = 20
_COLLECTION = "evidence"
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_STOPWORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}


def _vector(text: str) -> list[float]:
    values = [0.0] * _DIMENSION
    for token in _terms(text):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % _DIMENSION
        values[index] += -1.0 if digest[4] & 1 else 1.0
    norm = sum(value * value for value in values) ** 0.5
    return [value / norm for value in values] if norm else values


def _terms(text: str) -> set[str]:
    return {
        token[:-1] if token.endswith("s") and len(token) > 3 else token
        for token in _TOKEN.findall(text.casefold())
        if token not in _STOPWORDS
    }


def _chunks(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = _CHUNK_WORDS - _CHUNK_OVERLAP
    return [" ".join(words[start : start + _CHUNK_WORDS]) for start in range(0, len(words), step)]


class VectorIndex(Retriever):
    """Deterministic hashed-word retrieval for the local/demo deployment."""

    def __init__(self, path: str | Path) -> None:
        self._client = QdrantClient(path=str(path))
        self._lock = threading.RLock()
        with self._lock:
            if not self._client.collection_exists(_COLLECTION):
                self._client.create_collection(
                    collection_name=_COLLECTION,
                    vectors_config=models.VectorParams(
                        size=_DIMENSION, distance=models.Distance.COSINE
                    ),
                )

    def index(self, source: Source, text: str, submarket: Submarket, category: str) -> int:
        chunks = _chunks(text)
        points = []
        for number, excerpt in enumerate(chunks):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source.id}:{number}"))
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=_vector(excerpt),
                    payload={
                        "source_id": source.id,
                        "excerpt": excerpt,
                        "category": category,
                        "submarket": submarket,
                        "published_at": source.published_at.isoformat(),
                        "published_ordinal": source.published_at.toordinal(),
                        "title": source.title,
                        "publisher": source.publisher,
                        "url": str(source.url) if source.url else None,
                        "retrieved_at": source.retrieved_at.isoformat(),
                        "demo": source.demo,
                        "terms": sorted(_terms(excerpt)),
                    },
                )
            )
        with self._lock:
            if points:
                self._client.upsert(collection_name=_COLLECTION, points=points, wait=True)
        return len(points)

    def search(self, query: SearchQuery) -> list[Evidence]:
        if not query.query.strip():
            return []
        must: list[models.FieldCondition] = []
        if query.category:
            must.append(
                models.FieldCondition(key="category", match=models.MatchValue(value=query.category))
            )
        if query.as_of:
            must.append(
                models.FieldCondition(
                    key="published_ordinal", range=models.Range(lte=query.as_of.toordinal())
                )
            )
        if query.submarkets:
            must.append(
                models.FieldCondition(
                    key="submarket",
                    match=models.MatchAny(any=[*query.submarkets, "London"]),
                )
            )
        query_filter = models.Filter(must=must) if must else None
        with self._lock:
            response = self._client.query_points(
                collection_name=_COLLECTION,
                query=_vector(query.query),
                query_filter=query_filter,
                limit=max(query.limit * 3, query.limit),
                with_payload=True,
                score_threshold=0.000001,
            )
        results = []
        query_terms = _terms(query.query)
        for point in response.points:
            if point.score <= 0:
                continue
            payload = point.payload or {}
            if query_terms and not query_terms.intersection(payload.get("terms", [])):
                continue
            results.append(
                Evidence(
                    id=str(point.id),
                    source_id=str(payload["source_id"]),
                    excerpt=str(payload["excerpt"]),
                    category=str(payload["category"]),
                    submarket=payload["submarket"],
                    published_at=date.fromisoformat(str(payload["published_at"])),
                    score=float(point.score),
                )
            )
        if query.current:
            dates = [item.published_at.toordinal() for item in results]
            oldest, newest = min(dates, default=0), max(dates, default=0)
            span = max(newest - oldest, 1)
            results.sort(
                key=lambda item: (
                    item.score + 0.05 * (item.published_at.toordinal() - oldest) / span
                ),
                reverse=True,
            )
        return results[: query.limit]

    def close(self) -> None:
        with self._lock:
            self._client.close()
