# Live research architecture

## Contracts

`models.py` owns all typed contracts. `Settings.from_env()` loads .env without overriding
exported variables. `MarketService(data_dir=None, provider=None, *, mode=None, retriever=None,
web=None)` chooses live by default; explicit demo uses isolated data_dir/demo and the old
deterministic graph. Live uses data_dir/live, real provider and semantic Qdrant.

Live graph: START → agent → tools → agent (bounded loop) → verify → END. `AgentProvider.complete(messages, tools, timeout)->ModelTurn` preserves assistant tool-call
IDs and keeps provider reasoning only in private conversation state, never API traces. Live provider is `ZaiProvider()` using
ZAI_API_KEY/ZAI_API_BASE/LLM_MODEL. Missing credentials fail explicitly; no model or
billing-route fallback. `FirecrawlClient(base_url)` implements WebClient.

`VectorIndex(path=None, *, url=None, embedder=None, cache_dir=None)` uses FastEmbed
BAAI/bge-small-en-v1.5, 384 dimensions, a new named collection, validates model identity.
An injected embedder has name, dimension and embed(texts:list[str])->list[list[float]].
`DemoVectorIndex(path)` preserves old lexical implementation for explicit offline mode.
`chunk_text(text)->list[tuple[str,str]]` returns (location, excerpt) preserving paragraphs.
Ingestion `ingest(request, store, retriever)` indexes and saves the source text atomically
as far as two stores permit, idempotently by canonical URL + content checksum.
Unknown publication dates remain None. No URL fetching inside ingestion.

Database retains existing Store methods and adds:
- save_document(source:Source,text:str)->bool; get_document(id)->Document|None
- list_documents()->list[Document] (all versions), document identity is Source.id
- save_refresh(result:RefreshResult)->None; latest_refresh()->RefreshResult|None
- latest_successful_refresh()->RefreshResult|None
Database uses its own RLock for thread-safe statements/transactions; never hold it during
network/model calls. Legacy sources without stored text remain readable. Source metadata
and Metric definition/quotation must roundtrip. Live rows cannot reference demo sources.
Metrics from previous versions of the same URL are superseded in queries, not erased.

Public service: chat(ChatRequest)->ChatResponse; sources(); metrics(MetricQuery);
ingest(IngestRequest); refresh(RefreshRequest)->RefreshResult;
latest_refresh()->RefreshResult|None; status()->dict; close().
GET /api/status and GET /api/refresh expose status/latest briefing; POST /api/refresh runs
a bounded synchronous refresh. Chat retains JSON response and adds mode/incomplete/evidence/
conversation_id/freshness. UI opts into prior conversation, never puts secrets in responses.

## Trust and budgets

Live chat permits six tool rounds, three searches, six scrapes, 180 seconds total.
Tools are query_market_metrics, search_market_evidence, search_web, scrape_source.
Search snippets cannot ground final facts; full extracted content must be ingested first.
Source URLs must be public HTTP(S), without credentials or unsafe redirects. Firecrawl
is a trusted localhost service but fetched websites are untrusted. Its browser/fetch paths
block private-network destinations using the pinned upstream safeFetch and Playwright DNS/socket guards.
The app only resolves target addresses; all downloads and redirect handling run in Firecrawl,
including native text PDF parsing. There is no second direct HTTP download path. Interpretation must be distinguished from
facts and deterministic calculations. IDs and numerical provenance are checked; these
checks do not prove that a source is true. One malformed answer repair is allowed.


## Deliberate limits

One process owns refresh coordination and up to 50 in-memory conversations, each retaining
one complete preceding tool transcript. Restarts clear conversations; SQLite documents and
briefings persist. Multiple app workers require shared coordination/session storage.
Strict metric extraction may omit valid tables when geography, quarter or units are implicit;
those figures remain source text, never invented SQL observations. Comparisons require exact
matching metric/unit/definition and observation period, and retain conflicting source values.
Numerical checks validate token presence and deterministic comparison provenance, not full
semantic entailment. Citation checks are not a source-fact audit.
