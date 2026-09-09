import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field

from london_monitor.models import Model


class Settings(Model):
    mode: str = Field(default="live", pattern="^(live|demo)$")
    data_dir: Path = Path(".runtime")
    qdrant_url: str = "http://localhost:6333"
    firecrawl_url: str = "http://localhost:3002"
    embedding_cache: Path = Path(".runtime/embeddings")

    @classmethod
    def from_env(cls):
        load_dotenv(override=False)
        return cls(
            mode=os.getenv("LONDON_MODE", "live"),
            data_dir=Path(os.getenv("LONDON_DATA_DIR", ".runtime")),
            qdrant_url=os.getenv("QDRANT_URL") or "http://localhost:6333",
            firecrawl_url=os.getenv("FIRECRAWL_URL") or "http://localhost:3002",
            embedding_cache=Path(os.getenv("EMBEDDING_CACHE", ".runtime/embeddings")),
        )
