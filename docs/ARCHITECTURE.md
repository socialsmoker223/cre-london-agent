# Live research architecture

For setup and the user workflow, see [README](../README.md). The
[six-slide executive narrative](SLIDES.md) summarizes the same system for business reviewers.

Sources → Retrieve/Filter → Compare/Reason → Verify → Cited Brief describes the logical flow.
The actual graph can loop through retrieval and tools before verification, as described below.

## Contracts

`models.py` owns all typed contracts. `Settings.from_env()` loads .env without overriding
exported variables and requires LLM_PROVIDER (z.ai or openai), its API_KEY/API_BASE variables, LLM_MODEL and CRAWL4AI_API_TOKEN.
`MarketService(data_dir=None)` constructs the configured provider, remote Qdrant retrieval
and WebResearchClient. SQLite lives in data_dir/live. There is no demo/offline runtime or
provider injection at the service boundary. Legacy synthetic sources are excluded.

Live graph: START → retrieve → agent → tools → agent (bounded loop) → verify → END.
Initial retrieval supplies actual stored evidence even when the configured endpoint ignores tool
choice. Follow-ups reuse the saved evidence and complete prior transcript. `AgentProvider.complete(messages, tools, timeout)->ModelTurn` preserves assistant tool-call
IDs and keeps provider reasoning only in private conversation state, never API traces. Live provider is `OpenAICompatibleProvider()` using the selected server configuration. Missing credentials fail explicitly; no model or
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
latest_refresh()->RefreshResult|None; remove_source(source_id)->bool; status()->dict; close().
GET /api/status exposes refresh progress and provider/model; GET /api/refresh exposes the saved briefing; POST /api/refresh runs
a bounded synchronous refresh. Chat retains JSON response and adds mode/incomplete/evidence/
conversation_id/freshness. UI continues the prior conversation by default after a successful answer, never puts secrets in responses.

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
Tools are compare_market_changes, query_market_metrics, search_market_evidence, search_web,
scrape_source.
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
three complete preceding turns. Restarts clear conversations; SQLite documents and
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
is bounded to 12 documents and 18,000 characters per document, with explicit coverage warnings.
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
Refresh JSON stores these fields alongside numerical results.


## Business output and investigation

ChatRequest accepts an optional workflow (auto, monitor, investigate, prepare). Common business
questions select the relevant workflow deterministically; the model handles other wording.
Monitor/Prepare prepare change evidence before synthesis. Investigate prepares metric observations
and matched cross-market period growth, then retrieves qualitative support and counterevidence.
Higher rent levels alone do not prove outperformance. The metric vocabulary distinguishes
availability, Grade A/secondary vacancy, completions, future pipeline and prelet share; strict
quoted extraction still applies. Definitions describe series, not their current value or date.

ResearchAnswer and ChatResponse expose a hypothesis verdict, evidence gaps, and sectioned claims:
summary, what_changed, key_metrics, emerging_signals, risks, opportunities, watchlist and disagreements.
Every claim retains evidence IDs through to the dashboard. Structured evidence carries original
observations (including definition, reporting period and quotation) beside deterministic results.
The verifier checks factual numbers against quoted source content and requires calculated numbers
in calculation excerpts. Implications and watchlists must be interpretations. Disagreement claims
must retain both sources. A cited historical observation never clears an insufficient forecast
verdict. After one repair, a partly valid answer retains only claims that individually pass all checks,
with incomplete status and an insufficient verdict when an investigation verdict applies; the rejected conclusion is discarded.
If no claims survive, the response returns source excerpts with an explicit insufficiency notice.
These checks enforce provenance and output contracts, not full semantic entailment or causality.

Publication-month comparisons use explicit YYYY-MM windows (default: current UTC month), with the
previous calendar month as boundary. Unknown dates and newly ingested older publications are not
this month's news. Quarterly deltas published in the month keep their reporting periods. Text
coverage is bounded and disclosed. Last-checked questions use the saved refresh as an explicit
proxy baseline; personal visit timestamps are not tracked.

Material movements are screened at the configured magnitude thresholds, followed by disagreements
and new evidence; ties use newest publication and then distinct publishers. The rule and ordered
evidence IDs are returned by the tool. Publisher counts do not prove independence. Below-threshold
movements remain accessible for investigation. No opaque model materiality score is introduced.

The opening summary precedes the findings; the what_changed section contains at most five
developments. Remaining sections and per-claim
verification are expandable; key metrics and disagreements are expanded. Missing sections are not
filled with invented claims. Copy includes the numbered source list. Conversation memory retains
three complete turns and the current answer's cited evidence; sessions remain bounded and volatile.

Generated briefs lead with an “In brief” summary: up to two cited claims and 80 words, using
section=summary in the existing claim contract. The summary passes the same number, publisher
and evidence checks as detailed claims, appears before detailed sections in the dashboard and
copied/CLI text, and is discarded if the complete synthesis fails verification. Partial answers
show a scope limitation instead of retaining a potentially misleading executive conclusion.

If the provider omits its summary or repeats a detail verbatim, one bounded model call writes
a distinct synthesis from the verified findings and their evidence. It uses the remaining research
budget (at most 30 seconds), passes the existing citation/number checks and rejects copied details.
Failures leave the verified details intact with a summary-unavailable warning; no detail is promoted
into a summary. Verification failures never receive this synthesis step.

Offline smoke and eval are repository test entry points requiring the dev dependencies, explicitly
separate from the live-only application. Live eval runs the six product scenarios and optionally
saves full responses for human assessment; a structurally valid answer is not a business-quality
acceptance result by itself.


Publication metadata can be recovered during ingestion from an explicit “Published on” label or
the observed Savills publication/report heading wrapper or JLL dated Insight header. Event dates, related-article links and
conflicting dates are excluded. Reingesting unchanged content fills only a previously unknown
publication date and reindexes current-version metadata; historical versions are not promoted; source identity and existing known dates remain
unchanged. Comparison passages start at a recognizable article heading when available, avoiding
navigation-heavy prefixes while preserving the original stored document. Known publisher aliases
(e.g. Savills UK and savills.com) share one corroboration key; this still does not establish that
reports from distinct publishers are independent.

Recrawling a canonical URL revalidates its current observations against the new text before
attaching them to the new source version. Removed or changed quotations are not carried forward;
historical observations remain stored. This avoids emptying the dashboard merely because a
chat scrape created a new version without running model extraction.

Metric extraction and comparison use the article body after observed navigation wrappers, with
author/related-research footers excluded. Comparison windows retain up to 18,000 characters per
article; longer coverage remains explicitly partial. Raw documents remain available in storage.
Extraction logs distinguish candidate count from accepted count. Implicit quarters, units and
geography still fail the structured contract; source-reported figures can remain citeable in text.

Answer repair receives the draft, question and evidence once, with claim-specific rejection
reasons. Named preferred publishers must appear in the cited source metadata or passage; matching
numbers alone cannot validate a misattributed publisher. Publisher-reported growth is a fact;
only calculations from our tools use the calculation
kind. If repair fails, the partial brief retains verified claims but drops verdict statements,
marks the conclusion unavailable, and lists omissions separately from missing source coverage.
The dashboard no longer suggests that refreshing data will repair a citation error.


## Dashboard workspace

The source sidebar removes a source through DELETE /api/sources/{id}, including all versions
of its canonical URL, Qdrant excerpts, SQL metrics/projects/documents, saved refresh briefs and
conversation memory. A persisted removed_sources identity prevents automatic re-ingestion.
Removal is unavailable during refresh; ingest/removal share the service lock. If sources change
during a chat, that chat cannot return or save stale context. A failed vector deletion leaves SQL
intact so removal can be retried. There is no cross-store transaction or restore UI.

ChatRequest accepts optional provider and model fields. GET /api/status lists only configured
providers and default model IDs, never keys or endpoints. The browser allows a custom model ID;
availability is checked by the provider when used. Selection is scoped to the request; refresh
continues using the server default. z.ai and one OpenAI-compatible endpoint are supported by the
existing SDK. Configure the secondary provider with ZAI_MODEL or OPENAI_MODEL plus the matching
API_KEY/API_BASE environment variables; the primary uses LLM_MODEL. Endpoints must support
chat completions, tool calls and JSON object responses. Choices last for the current page session.
