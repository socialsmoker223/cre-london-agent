import ipaddress
import os
import socket
import time
from datetime import date
from urllib.parse import urlsplit

import httpx

from london_monitor.models import ScrapedPage, ScrapeQuery, WebHit, WebSearchQuery

TRUSTED_DOMAINS = (
    "cbre.co.uk",
    "jll.co.uk",
    "savills.co.uk",
    "knightfrank.co.uk",
    "cbre.com",
    "jll.com",
    "savills.com",
    "knightfrank.com",
    "bankofengland.co.uk",
    "ons.gov.uk",
    "landsec.com",
    "britishland.com",
    "canarywharf.com",
)


def _is_trusted(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in TRUSTED_DOMAINS)


def _public_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or parts.username
        or parts.password
        or not parts.hostname
    ):
        raise ValueError("URL must be public HTTP(S) without credentials")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parts.hostname, parts.port, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise ValueError("URL hostname could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("URL resolves to a private or reserved address")
    return url


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("web request deadline exceeded")
    return remaining


def _parse_hits(data: dict) -> list[WebHit]:
    payload = data.get("data", data)
    hits = payload.get("web", []) if isinstance(payload, dict) else payload
    if not isinstance(hits, list):
        raise ValueError("Firecrawl returned no search results")
    return [
        WebHit(
            url=item["url"],
            title=item.get("title", ""),
            description=item.get("description", item.get("snippet", "")),
            trusted=_is_trusted(item["url"]),
        )
        for item in hits
        if isinstance(item, dict) and item.get("url")
    ]


class FirecrawlClient:
    def __init__(self, base_url: str = "http://localhost:3002", api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("FIRECRAWL_API_KEY")

    def _request(self, path: str, payload: dict, timeout: float) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = httpx.post(
            f"{self.base_url}{path}", json=payload, headers=headers, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("success") is False:
            raise ValueError("Firecrawl request failed")
        return data

    def search(self, query: WebSearchQuery, timeout: float = 30) -> list[WebHit]:
        deadline = time.monotonic() + timeout
        sites = " OR ".join(f"site:{domain}" for domain in TRUSTED_DOMAINS)
        trusted_query = f"({sites}) ({query.query})"
        first = (
            _parse_hits(
                self._request(
                    "/v2/search",
                    {"query": trusted_query, "limit": query.limit},
                    _remaining(deadline),
                )
            )
            if query.trusted_first
            else []
        )
        if query.trusted_first and any(hit.trusted for hit in first):
            return [hit for hit in first if hit.trusted] + [hit for hit in first if not hit.trusted]
        return _parse_hits(
            self._request(
                "/v2/search", {"query": query.query, "limit": query.limit}, _remaining(deadline)
            )
        )

    def scrape(self, query: ScrapeQuery, timeout: float = 60) -> ScrapedPage:
        deadline = time.monotonic() + timeout
        url = _public_url(str(query.url))
        data = self._request(
            "/v2/scrape", {"url": url, "formats": ["markdown"]}, _remaining(deadline)
        )
        payload = data.get("data", data)
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        metadata = metadata or {}
        text = payload.get("markdown") if isinstance(payload, dict) else None
        title = (
            metadata.get("title")
            or (payload.get("title") if isinstance(payload, dict) else None)
            or url
        )
        publisher = metadata.get("ogSiteName") or (urlsplit(url).hostname or "")
        if not text or any(term in text.lower() for term in ("paywall", "subscribe to read")):
            raise ValueError("Firecrawl returned blocked or unreadable content")
        published = metadata.get("publishedTime") or metadata.get("published_at")
        try:
            parsed_date = date.fromisoformat(published[:10]) if published else None
        except (TypeError, ValueError):
            parsed_date = None
        final_url = _public_url(metadata.get("url") or metadata.get("sourceURL") or url)
        return ScrapedPage(
            url=final_url,
            title=title,
            publisher=publisher,
            text=text,
            published_at=parsed_date,
            trusted=_is_trusted(final_url),
        )
