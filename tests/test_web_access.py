import pytest

import london_monitor.web_access as web_access
from london_monitor.models import ScrapeQuery, WebSearchQuery


class Response:
    def __init__(self, data, status_code=200, headers=None):
        self._data = data
        self.status_code = status_code
        self.headers = headers or {}
        self.content = b""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def public_dns(monkeypatch):
    monkeypatch.setattr(
        web_access.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 0))],
    )


def test_search_trusted_first_then_broadens(monkeypatch):
    calls = []
    responses = [
        Response({"success": True, "data": {}}),
        Response({
            "success": True,
            "data": {"web": [{"url": "https://cbre.co.uk/x", "title": "trusted"}]},
        }),
    ]
    monkeypatch.setattr(
        web_access.httpx,
        "post",
        lambda url, **kwargs: calls.append(kwargs["json"]) or responses.pop(0),
    )
    hits = web_access.FirecrawlClient().search(WebSearchQuery(query="prime rents"))
    assert hits[0].trusted is True
    assert len(calls) == 2
    assert "site:cbre.co.uk" in calls[0]["query"]


def test_scrape_rejects_private_resolution_and_reserved_target(monkeypatch):
    monkeypatch.setattr(
        web_access.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 0))],
    )
    with pytest.raises(ValueError, match="private"):
        web_access.FirecrawlClient().scrape(ScrapeQuery(url="https://example.com"))



def test_scrape_requires_success_and_markdown(monkeypatch):
    public_dns(monkeypatch)
    monkeypatch.setattr(
        web_access.httpx, "post", lambda *args, **kwargs: Response({"success": False})
    )
    with pytest.raises(ValueError, match="failed"):
        web_access.FirecrawlClient().scrape(ScrapeQuery(url="https://example.com"))
