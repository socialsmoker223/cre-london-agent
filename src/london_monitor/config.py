import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr

from london_monitor.models import Model


class Settings(Model):
    provider: Literal["z.ai"]
    api_key: SecretStr = Field(min_length=1)
    model: str = Field(min_length=1)
    api_base: str = Field(min_length=1)
    data_dir: Path = Path(".runtime")
    qdrant_url: str = "http://localhost:6333"
    firecrawl_url: str = "http://localhost:3002"
    embedding_cache: Path = Path(".runtime/embeddings")

    @classmethod
    def from_env(cls):
        load_dotenv(override=False)
        if os.getenv("LONDON_MODE", "live") != "live":
            raise ValueError("Only live mode is supported; remove LONDON_MODE or set it to live.")
        required = ("LLM_PROVIDER", "ZAI_API_KEY", "LLM_MODEL", "ZAI_API_BASE")
        missing = [name for name in required if not os.getenv(name, "").strip()]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        if os.environ["LLM_PROVIDER"].strip() != "z.ai":
            raise ValueError("Unsupported LLM_PROVIDER; this application supports z.ai only.")
        return cls(
            provider=os.environ["LLM_PROVIDER"].strip(),
            api_key=os.environ["ZAI_API_KEY"].strip(),
            model=os.environ["LLM_MODEL"].strip(),
            api_base=os.environ["ZAI_API_BASE"].strip(),
            data_dir=Path(os.getenv("LONDON_DATA_DIR", ".runtime")),
            qdrant_url=os.getenv("QDRANT_URL") or "http://localhost:6333",
            firecrawl_url=os.getenv("FIRECRAWL_URL") or "http://localhost:3002",
            embedding_cache=Path(os.getenv("EMBEDDING_CACHE", ".runtime/embeddings")),
        )
