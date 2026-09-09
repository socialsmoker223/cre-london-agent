"""Source normalization and idempotent local ingestion."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import IngestRequest, IngestResult, Retriever, Source, Store


def canonical_url(url):
    if not url:
        return None
    parts = urlsplit(str(url))
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_")
        )
    )
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", query, "")
    )


def ingest(request: IngestRequest, store: Store, retriever: Retriever) -> IngestResult:
    text = re.sub(r"[ \t]+", " ", request.text).strip()
    canonical = canonical_url(request.url)
    checksum = hashlib.sha256(text.encode()).hexdigest()
    existing = next(
        (
            s
            for s in store.list_sources()
            if s.checksum == checksum
            and s.canonical_url == canonical
            and (
                canonical is not None
                or (s.title, s.publisher) == (request.title, request.publisher)
            )
        ),
        None,
    )
    if existing:
        return IngestResult(source=existing, chunks=0, duplicate=True)
    identity = canonical or f"{request.publisher}\n{request.title}"
    source = Source(
        id=f"source-{hashlib.sha256((identity + checksum).encode()).hexdigest()[:24]}",
        title=request.title,
        publisher=request.publisher,
        url=request.url,
        published_at=request.published_at,
        checksum=checksum,
        demo=request.demo,
        submarket=request.submarket,
        category=request.category,
        trusted=request.trusted,
        source_type=request.source_type,
        canonical_url=canonical,
    )
    chunks = retriever.index(source, text, request.submarket, request.category)
    store.save_document(source, text)
    return IngestResult(source=source, chunks=chunks, duplicate=False)
