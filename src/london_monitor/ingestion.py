"""Source normalization and idempotent local ingestion."""

import hashlib
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import IngestRequest, IngestResult, MetricQuery, Retriever, Source, Store


def article_text(text):
    """Remove observed site navigation wrappers, retaining the original article wording."""
    start = re.search(
        r"(?m)^## (?:Publication|Research article)\n|^[^\n]+\n={3,}\n", text
    )
    if not start:
        return text
    article = text[start.start():]
    end = re.search(
        r"(?m)^\*\*EXPLORE MORE INSIGHT|^Authors\s*$|^## Related Research\s*$", article
    )
    return article[:end.start()].strip() if end else article


def publication_date(text):
    """Read publication labels and the observed Savills office-report date wrapper."""
    date_text = r"(\d{1,2} [A-Za-z]+ \d{4}|[A-Za-z]+ \d{1,2}, \d{4}|\d{4}-\d{2}-\d{2})"
    matches = re.findall(r"(?im)^Published(?: on)?[: ]+" + date_text + r"\s*$", text)
    matches += re.findall(r"\bInsight\s+" + date_text + r"\s+# ", text)
    matches += re.findall(
        r"(?im)^(?:## (?:Publication|Research article)\n# [^\n]+\n|"
        r"[^\n]*Office Market[^\n]*\n={3,}\n)\s*" + date_text + r"\s*$", text
    )
    dates = set()
    for value in matches:
        for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%Y-%m-%d"):
            try:
                dates.add(datetime.strptime(value, fmt).date())
                break
            except ValueError:
                continue
    return next(iter(dates)) if len(dates) == 1 else None


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
    published_at = request.published_at or publication_date(text)
    sources = store.list_sources()
    existing = next(
        (
            s
            for s in sources
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
        if existing.published_at is None and published_at:
            existing = existing.model_copy(update={"published_at": published_at})
            current = not canonical or not any(
                s.canonical_url == canonical and s.retrieved_at > existing.retrieved_at
                for s in sources
            )
            # Enrich historical SQL provenance without promoting an old version in retrieval.
            chunks = retriever.index(existing, text, existing.submarket, existing.category) \
                if current else 0
            store.add_source(existing)
            return IngestResult(source=existing, chunks=chunks, duplicate=True)
        return IngestResult(source=existing, chunks=0, duplicate=True)
    identity = canonical or f"{request.publisher}\n{request.title}"
    source = Source(
        id=f"source-{hashlib.sha256((identity + checksum).encode()).hexdigest()[:24]}",
        title=request.title,
        publisher=request.publisher,
        url=request.url,
        published_at=published_at,
        checksum=checksum,
        demo=request.demo,
        submarket=request.submarket,
        category=request.category,
        trusted=request.trusted,
        source_type=request.source_type,
        canonical_url=canonical,
    )
    chunks = retriever.index(source, text, request.submarket, request.category)
    # A fresh crawl must not erase observations still quoted verbatim in the new version.
    prior_ids = {s.id for s in sources if canonical and s.canonical_url == canonical}
    prior_metrics = [m for m in store.query_metrics(MetricQuery(latest=False), unlimited=True)
                     if m.source_id in prior_ids]
    store.save_document(source, text)
    if prior_metrics:
        from .service import validated_metrics

        store.add_metrics(validated_metrics(
            [m.model_dump() for m in prior_metrics], source.id, text
        ))
    return IngestResult(source=source, chunks=chunks, duplicate=False)
