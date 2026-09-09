"""Opt-in live smoke check; requires configured z.ai, Qdrant, and network access."""

import html
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

from london_monitor.config import Settings
from london_monitor.models import ChatRequest, IngestRequest
from london_monitor.retrieval import VectorIndex
from london_monitor.service import MarketService


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif tag in {"p", "h1", "h2", "h3", "li"} and self.skip == 0:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)
        elif tag in {"p", "h1", "h2", "h3", "li"} and self.skip == 0:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip == 0:
            self.parts.append(data)


def main() -> None:
    Settings.from_env()
    url = "https://www.jll.com/en-uk/insights/market-dynamics/central-london-office"
    response = httpx.get(url, timeout=20, follow_redirects=True)
    response.raise_for_status()
    parser = TextParser()
    parser.feed(response.text)
    text = html.unescape("".join(parser.parts))
    text = "\n\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
    if len(text) < 200:
        raise RuntimeError("direct report fetch returned too little text")
    local_vector = "--local-vector-check" in sys.argv
    retriever = (
        VectorIndex(
            Path("/tmp/london-local-vectors"),
            cache_dir=Settings.from_env().embedding_cache,
        )
        if local_vector
        else None
    )
    service = MarketService(Path("/tmp/london-live-run"), mode="live", retriever=retriever)
    try:
        service.ingest(IngestRequest(
            title="JLL UK real estate research", publisher="JLL", url=url,
            text=text[:100000], demo=False,
        ))
        result = service.chat(ChatRequest(
            question=(
                "What does this report say about office demand and what could it imply "
                "for leasing decisions?"
            )
        ))
        if not result.citations or result.insufficient_evidence:
            raise RuntimeError("live answer had no grounded citations")
        Path("deliverables/live-run.json").write_text(json.dumps({
            "method": "direct httpx report fetch for setup; live answer via MarketService",
            "firecrawl_used": False,
            "vector_backend": (
                "local temporary Qdrant" if local_vector else "configured server Qdrant"
            ),
            "source": {"url": url, "publisher": "JLL", "published_at": None},
            "response": result.model_dump(mode="json"),
        }, indent=2) + "\n")
        print(json.dumps({"citations": len(result.citations), "answer": result.answer}))
    finally:
        service.close()


if __name__ == "__main__":
    main()
