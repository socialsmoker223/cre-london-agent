"""Source normalization and idempotent local ingestion."""

import hashlib
import json
import re

from .models import IngestRequest, IngestResult, Retriever, Source, Store


def ingest(request: IngestRequest, store: Store, retriever: Retriever) -> IngestResult:
    text = re.sub(r"\s+", " ", request.text).strip()
    metadata = {
        "title": request.title,
        "publisher": request.publisher,
        "published_at": request.published_at.isoformat(),
        "url": str(request.url) if request.url else None,
        "submarket": request.submarket,
        "category": request.category,
        "demo": request.demo,
    }
    checksum = hashlib.sha256(
        json.dumps({"text": text, "metadata": metadata}, sort_keys=True).encode()
    ).hexdigest()
    existing = next(
        (source for source in store.list_sources() if source.checksum == checksum), None
    )
    if existing:
        return IngestResult(source=existing, chunks=0, duplicate=True)

    source = Source(
        id=f"source-{checksum[:24]}",
        title=request.title,
        publisher=request.publisher,
        url=request.url,
        published_at=request.published_at,
        checksum=checksum,
        demo=request.demo,
    )
    chunks = retriever.index(source, text, request.submarket, request.category)
    if not store.add_source(source):
        existing = next(
            (item for item in store.list_sources() if item.checksum == checksum), source
        )
        return IngestResult(source=existing, chunks=chunks, duplicate=True)
    return IngestResult(source=source, chunks=chunks, duplicate=False)
