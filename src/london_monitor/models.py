from datetime import UTC, date, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Submarket = Literal["City", "West End", "Canary Wharf", "Midtown / Fringe", "London"]
MetricName = Literal["prime_rent", "grade_a_rent", "vacancy", "take_up", "bank_rate"]
Skill = Literal["market_pulse", "comparison", "supply", "macro", "evidence", "metrics"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(Model):
    id: str
    title: str
    publisher: str
    url: HttpUrl | None = None
    published_at: date
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_type: str = "text"
    checksum: str
    demo: bool = True


class Metric(Model):
    metric: MetricName
    value: float = Field(allow_inf_nan=False)
    unit: str
    period: str = Field(pattern=r"^\d{4}-Q[1-4]$")
    submarket: Submarket
    source_id: str


class Project(Model):
    name: str
    submarket: Submarket
    status: str
    completion_date: date
    size_sq_ft: float = Field(gt=0)
    prelet_status: str
    source_id: str


class Evidence(Model):
    id: str
    source_id: str
    excerpt: str
    category: str
    submarket: Submarket
    published_at: date
    score: float = 0


class MetricQuery(Model):
    submarkets: list[Submarket] = Field(default_factory=list, max_length=5)
    metrics: list[MetricName] = Field(default_factory=list, max_length=5)
    period: str | None = Field(default=None, pattern=r"^\d{4}-Q[1-4]$")
    latest: bool = True
    limit: int = Field(default=100, ge=1, le=200)


class SearchQuery(Model):
    query: str = Field(min_length=1, max_length=4000)
    submarkets: list[Submarket] = Field(default_factory=list)
    category: str | None = None
    as_of: date | None = None
    current: bool = False
    limit: int = Field(default=6, ge=1, le=20)


class IngestRequest(Model):
    title: str = Field(min_length=1, max_length=200)
    publisher: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=20, max_length=100000)
    published_at: date
    url: HttpUrl | None = None
    submarket: Submarket = "London"
    category: str = Field(default="commentary", max_length=80)
    demo: bool = False


class IngestResult(Model):
    source: Source
    chunks: int
    duplicate: bool


class ChatRequest(Model):
    question: str = Field(min_length=3, max_length=2000)
    previous_question: str | None = Field(default=None, max_length=2000)


class Citation(Model):
    number: int
    source: Source
    excerpt: str


class Claim(Model):
    text: str
    kind: Literal["fact", "calculation", "interpretation"] = "fact"
    source_ids: list[str] = Field(min_length=1)


class Draft(Model):
    claims: list[Claim] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)


class Trace(Model):
    run_id: str
    intent: str
    skills: list[Skill] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    nodes: list[str] = Field(default_factory=list)
    retrieval_count: int = 0
    duration_ms: float = 0
    failures: list[str] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)


class ChatResponse(Model):
    answer: str
    claims: list[Claim]
    citations: list[Citation]
    metrics: list[Metric]
    warnings: list[str]
    trace: Trace
    demo: bool
    insufficient_evidence: bool = False


class Store(Protocol):
    def add_source(self, source: Source) -> bool: ...
    def list_sources(self) -> list[Source]: ...
    def add_metrics(self, metrics: list[Metric]) -> None: ...
    def query_metrics(self, query: MetricQuery) -> list[Metric]: ...
    def add_projects(self, projects: list[Project]) -> None: ...
    def get_projects(self, submarkets: list[Submarket]) -> list[Project]: ...


class Retriever(Protocol):
    def index(self, source: Source, text: str, submarket: Submarket, category: str) -> int: ...
    def search(self, query: SearchQuery) -> list[Evidence]: ...
    def close(self) -> None: ...


class Synthesizer(Protocol):
    def synthesize(self, question: str, facts: list[Claim]) -> Draft: ...
