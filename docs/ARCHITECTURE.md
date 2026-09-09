# Live research architecture

## Contracts

`models.py` owns all typed contracts. `Settings.from_env()` loads .env without overriding
exported variables and requires LLM_PROVIDER=z.ai, ZAI_API_KEY, ZAI_API_BASE and LLM_MODEL.
`MarketService(data_dir=None)` constructs the configured provider, remote Qdrant retrieval
and WebResearchClient. SQLite lives in data_dir/live. There is no demo/offline runtime or
provider injection at the service boundary. Legacy synthetic sources are excluded.

Live graph: START → retrieve → agent → tools → agent (bounded loop) → verify → END.
Initial retrieval supplies actual stored evidence even when the configured endpoint ignores tool
choice. Follow-ups reuse the saved evidence and complete prior transcript. `AgentProvider.complete(messages, tools, timeout)->ModelTurn` preserves assistant tool-call
IDs and keeps provider reasoning only in private conversation state, never API traces. Live provider is `ZaiProvider()` using
ZAI_API_KEY/ZAI_API_BASE/LLM_MODEL. Missing credentials fail explicitly; no model or
billing-route fallback. `WebResearchClient(base_url, api_token)` implements WebClient: `ddgs` with configurable DDGS_BACKEND (default auto) for discovery and authenticated Crawl4AI /crawl for HTML/PDF extraction.

`VectorIndex(path=None, *, url=None, embedder=None, cache_dir=None)` uses FastEmbed
BAAI/bge-small-en-v1.5, 384 dimensions, a new named collection, validates model identity.
Embedding calls use a shared lock, batch_size=16 and two inference threads to bound memory.
`chunk_text(text)->list[tuple[str,str]]` returns (location, excerpt) preserving paragraphs.
The container runs as UID 10001 without a home directory. Set `HF_HOME` to
`/app/.runtime/huggingface` so Hugging Face/Xet downloads use the writable persistent
volume; `EMBEDDING_CACHE` alone does not relocate Xet's cache from `/.cache`.
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
GET /api/status exposes refresh progress and provider/model; GET /api/refresh exposes the saved briefing; POST /api/refresh runs
a bounded synchronous refresh. Chat retains JSON response and adds mode/incomplete/evidence/
conversation_id/freshness. UI opts into prior conversation, never puts secrets in responses.

`POST /api/chat/stream` accepts the same ChatRequest and emits SSE JSON events: `activity`
(message), then exactly one terminal `result` (ChatResponse in data) or `error` (safe message).
The graph emits activity before retrieval, model rounds, tool calls and verification, with
completion/failure updates. Reasoning and unverified model output stay private; the grounded
answer arrives after verification. The synchronous JSON endpoint remains available.
A worker thread runs research while the async response sends events and ten-second keepalives;
proxy buffering is disabled. Disconnect/Stop prevents subsequent graph steps at the next
progress callback; an already-running model or tool call finishes under its existing timeout.
The browser incrementally decodes UTF-8 frames, shows elapsed time and an expandable activity
log, and retains the last answer on failure, disconnect or Stop. Truncated streams are errors.


## Trust and budgets

Live chat permits six tool rounds, three searches, six scrapes, 180 seconds total.
Tools are query_market_metrics, search_market_evidence, search_web, scrape_source.
Search snippets cannot ground final facts; full extracted content must be ingested first.
Source URLs must be public HTTP(S), without credentials. The app checks addresses before each
crawl. Pinned Crawl4AI 0.9.3 applies its DNS-pinning egress broker to Chromium and validates
PDF redirect destinations and connected peers. Custom hooks and LLM extraction are not used.
Crawl4AI runs separately with its own API token; the configured model's key stays in the app.
DuckDuckGo SDK errors propagate to the tool/refresh failure records; auto mode selects available engines; DDGS_BACKEND=duckduckgo restricts it to DuckDuckGo. Source pages remain untrusted data.
Interpretation must be distinguished from
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

Failed chat requests preserve the previous browser answer. Refresh failures expose their reasons
and retry guidance. Metric rows expose source URLs, publication/retrieval dates, definitions
and verbatim quotations. A duplicate document without metrics retries extraction.


Chat collection calls `collect(..., with_metrics=False)`: source text is sufficient for cited
reported facts. Refresh performs metric extraction for SQL comparisons; this avoids spending
the chat deadline on a separate extraction call for every newly read page.

## Market change detection

`compare_market_changes(ChangeQuery)` is the chat tool for the primary change workflow.
`basis=reporting_period` compares the requested (or latest validated) quarter with its immediate
predecessor; `basis=last_update` compares current observations and document identities with the
last saved refresh, including documents ingested by chat or direct ingestion between refreshes.
Missing baselines, same-period revisions, conflicting reports and quarter gaps remain explicit.
Comparisons require matching metric, geography, unit and definition. Both input sources are cited;
percent change is `(current - previous) / abs(previous) * 100`, undefined for zero baselines.
Default screening thresholds are 5% relative change, or 0.5 percentage points for rates, inclusive.
The tool accepts `relative_threshold_pct` and `rate_threshold_pp` overrides. These are transparent
screening rules, not statistical significance or calibrated investment recommendations.
Refresh snapshots and change queries read all validated metrics, independent of the public query
row limit. Regular metric evidence shares the same deterministic period calculation.

Text comparisons pair stored previous/current excerpts, keeping publication and ingestion dates
separate. Reporting-period text windows use publication quarters, not inferred observation dates;
undated publications are excluded from these windows but included in ingestion comparisons.
Historical document versions remain available for the preceding publication quarter. Each window
is bounded to 12 documents and 5,000 characters per document, with explicit coverage warnings.
The model must check event dates, geography and independent reporting before interpreting signals.
Strengthened/weakened risks require citations to both windows; silence does not mean weakening.
Emerging themes require multiple current publishers; repeated chunks and syndicated reports do not
establish independent corroboration. Status-labelled claims are interpretations, not measured facts.
Grounding checks enforce window/publisher references and numerical provenance, not semantic truth.

Refresh adds one bounded model call to summarize changed signals, stores cited `evidence_changes`,
and retains up to 30 `identified_signals` hypotheses for later reassessment, including across
unchanged refreshes. First collection establishes a baseline without claiming text signal changes.
Analysis failures preserve numerical results and mark the refresh incomplete. Saved briefing text
and chat responses prioritize movements and changed signals over a full market-state recap.
No new dependencies or storage tables are required; existing refresh JSON accepts the added fields.
