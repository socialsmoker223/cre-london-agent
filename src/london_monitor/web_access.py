import ipaddress
import socket
from datetime import date
from urllib.parse import urlsplit

import httpx
from ddgs import DDGS

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



class WebResearchClient:
    def __init__(self, base_url: str, api_token: str, backend: str = "auto") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.backend = backend

    def search(self, query: WebSearchQuery, timeout: float = 30) -> list[WebHit]:
        rows = DDGS(timeout=timeout).text(
            query.query, region="uk-en", backend=self.backend, max_results=query.limit * 2
        )
        hits = [
            WebHit(
                url=row["href"], title=row.get("title", ""),
                description=row.get("body", ""), trusted=_is_trusted(row["href"]),
            )
            for row in rows if row.get("href")
        ]
        if query.trusted_first:
            hits.sort(key=lambda hit: not hit.trusted)
        return hits[:query.limit]

    def scrape(self, query: ScrapeQuery, timeout: float = 60) -> ScrapedPage:
        url = _public_url(str(query.url))
        is_pdf = urlsplit(url).path.lower().endswith(".pdf")
        params = {
            "cache_mode": {"type": "CacheMode", "params": "bypass"},
            "page_timeout": max(1, int(timeout * 1000)),
            "wait_until": "domcontentloaded",
        }
        if is_pdf:
            params["scraping_strategy"] = {
                "type": "PDFContentScrapingStrategy",
                "params": {"max_pdf_bytes": 20_000_000, "max_pdf_pages": 100},
            }
        response = httpx.post(
            f"{self.base_url}/crawl",
            headers={"Authorization": f"Bearer {self.api_token}"},
            json={
                "urls": [url],
                "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
                "crawler_config": {"type": "CrawlerRunConfig", "params": params},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        if not data.get("success") or not results:
            raise ValueError("Crawl4AI returned no crawl result")
        result = results[0]
        if not result.get("success"):
            reason = result.get("error_message") or "unknown error"
            raise ValueError(f"Crawl4AI failed: {reason[:240]}")
        markdown = result.get("markdown") or {}
        text = markdown.get("raw_markdown", "") if isinstance(markdown, dict) else markdown
        if not text.strip() or any(term in text.lower() for term in (
            "subscribe to read", "verify you are human", "just a moment..."
        )):
            raise ValueError("Crawl4AI returned blocked or unreadable content")
        metadata = result.get("metadata") or {}
        published = metadata.get("article:published_time") or metadata.get("published_time")
        try:
            published_at = date.fromisoformat(published[:10]) if published else None
        except (TypeError, ValueError):
            published_at = None
        final_url = _public_url(result.get("redirected_url") or result.get("url") or url)
        return ScrapedPage(
            url=final_url, title=metadata.get("title") or url,
            publisher=metadata.get("og:site_name") or urlsplit(final_url).hostname,
            text=text, published_at=published_at, trusted=_is_trusted(final_url),
            source_type="pdf" if is_pdf else "html",
        )
