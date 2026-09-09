from datetime import UTC, date, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Submarket = Literal["City", "West End", "Canary Wharf", "Midtown / Fringe", "London"]
MetricName = Literal["prime_rent", "grade_a_rent", "vacancy", "take_up", "bank_rate"]
Skill = Literal["market_pulse", "comparison", "supply", "macro", "evidence", "metrics"]
ChangeStatus = Literal["new", "strengthened", "weakened", "contradictory", "emerging"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(Model):
    id: str
    title: str
    publisher: str
    url: HttpUrl | None = None
    published_at: date | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_type: str = "text"
    checksum: str
    demo: bool = False
    submarket: Submarket = "London"
    category: str = "commentary"
    trusted: bool = False
    canonical_url: str | None = None


class Metric(Model):
    metric: MetricName
    value: float = Field(allow_inf_nan=False)
    unit: str
    period: str = Field(pattern=r"^\d{4}-Q[1-4]$")
    submarket: Submarket
    source_id: str
    definition: str = ""
    quotation: str = ""


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
    source_title: str = ""
    category: str
    submarket: Submarket
    published_at: date | None = None
    source_ids: list[str] = Field(default_factory=list)
    score: float = 0
    location: str = ""
    comparison_role: Literal["previous", "current", "new"] | None = None
    publisher: str = ""


class MetricQuery(Model):
    submarkets: list[Submarket] = Field(default_factory=list, max_length=5)
    metrics: list[MetricName] = Field(default_factory=list, max_length=5)
    period: str | None = Field(default=None, pattern=r"^\d{4}-Q[1-4]$")
    latest: bool = True
    limit: int = Field(default=100, ge=1, le=200)


class ChangeQuery(Model):
    basis: Literal["reporting_period", "last_update"] = "reporting_period"
    period: str | None = Field(default=None, pattern=r"^\d{4}-Q[1-4]$")
    submarkets: list[Submarket] = Field(default_factory=list, max_length=5)
    relative_threshold_pct: float = Field(default=5, gt=0, allow_inf_nan=False)
    rate_threshold_pp: float = Field(default=0.5, gt=0, allow_inf_nan=False)


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
    published_at: date | None = None
    url: HttpUrl | None = None
    submarket: Submarket = "London"
    category: str = Field(default="commentary", max_length=80)
    demo: bool = False
    trusted: bool = False
    source_type: str = "text"


class IngestResult(Model):
    source: Source
    chunks: int
    duplicate: bool


class ChatRequest(Model):
    question: str = Field(min_length=3, max_length=2000)
    previous_question: str | None = Field(default=None, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


class Citation(Model):
    number: int
    source: Source
    excerpt: str


class Claim(Model):
    text: str
    kind: Literal["fact", "calculation", "interpretation"] = "fact"
    source_ids: list[str] = Field(min_length=1)
    change_status: ChangeStatus | None = None


class Trace(Model):
    run_id: str
    intent: str
    skills: list[Skill] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    nodes: list[str] = Field(default_factory=list)
    retrieval_count: int = 0
    source_count: int = 0
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
    demo: Literal[False] = False
    insufficient_evidence: bool = False
    mode: Literal["live"] = "live"
    incomplete: bool = False
    conversation_id: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    freshness: str = ""


class Store(Protocol):
    def add_source(self, source: Source) -> bool: ...
    def list_sources(self) -> list[Source]: ...
    def add_metrics(self, metrics: list[Metric]) -> None: ...
    def query_metrics(self, query: MetricQuery, *, unlimited: bool = False) -> list[Metric]: ...
    def save_document(self, source: Source, text: str) -> bool: ...
    def get_document(self, source_id: str) -> "Document | None": ...
    def list_documents(self) -> list["Document"]: ...
    def save_refresh(self, result: "RefreshResult") -> None: ...
    def latest_refresh(self) -> "RefreshResult | None": ...
    def latest_successful_refresh(self) -> "RefreshResult | None": ...
    def add_projects(self, projects: list[Project]) -> None: ...
    def get_projects(self, submarkets: list[Submarket]) -> list[Project]: ...


class Retriever(Protocol):
    def index(self, source: Source, text: str, submarket: Submarket, category: str) -> int: ...
    def search(self, query: SearchQuery) -> list[Evidence]: ...
    def close(self) -> None: ...


class Document(Model):
    source: Source
    text: str


class WebSearchQuery(Model):
    query: str = Field(min_length=3, max_length=500)
    trusted_first: bool = True
    limit: int = Field(default=5, ge=1, le=5)


class WebHit(Model):
    url: HttpUrl
    title: str
    description: str = ""
    trusted: bool = False


class ScrapeQuery(Model):
    url: HttpUrl
    submarket: Submarket = "London"
    category: str = Field(default="commentary", max_length=80)


class ScrapedPage(Model):
    url: HttpUrl
    title: str
    publisher: str
    text: str
    published_at: date | None = None
    source_type: str = "web"
    trusted: bool = False


class ToolCall(Model):
    id: str
    name: str
    arguments: str


class ModelTurn(Model):
    content: str = ""
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)


class AnswerClaim(Model):
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["fact", "calculation", "interpretation"] = "fact"
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    change_status: ChangeStatus | None = None


class ResearchAnswer(Model):
    conclusion: str = Field(default="", max_length=2000)
    claims: list[AnswerClaim] = Field(default_factory=list, max_length=30)
    insufficient_evidence: bool = False


class MetricCandidate(Metric):
    definition: str = Field(min_length=3, max_length=300)
    quotation: str = Field(min_length=5, max_length=1200)


class RefreshRequest(Model):
    max_sources: int = Field(default=12, ge=1, le=12)


class RefreshResult(Model):
    run_id: str
    started_at: datetime
    completed_at: datetime
    status: Literal["complete", "partial", "failed"]
    baseline: bool
    new_sources: list[str] = Field(default_factory=list)
    updated_sources: list[str] = Field(default_factory=list)
    unchanged: int = 0
    metric_changes: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    briefing: str
    source_snapshot: dict[str, str] = Field(default_factory=dict)
    metric_snapshot: list[Metric] = Field(default_factory=list)
    evidence_changes: list[Claim] = Field(default_factory=list)
    identified_signals: list[Claim] = Field(default_factory=list)
    comparison_warnings: list[str] = Field(default_factory=list)


class AgentProvider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn: ...


class WebClient(Protocol):
    def search(self, query: WebSearchQuery, timeout: float = 30) -> list[WebHit]: ...
    def scrape(self, query: ScrapeQuery, timeout: float = 60) -> ScrapedPage: ...
