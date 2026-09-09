# Architecture

One Python process owns a SQLite database and Qdrant local collection. FastAPI serves a
static browser UI and typed endpoints. No network or credentials are needed in demo mode.

## Contracts and ownership

`models.py` is the shared contract. SQLite `Database(path)` implements Store;
`VectorIndex(path)` implements Retriever using deterministic hashed word embeddings in
local demo mode. Ingestion `ingest(request, store, retriever)` normalizes text, hashes
content plus metadata, indexes idempotently and preserves source provenance.
`demo.seed(store, retriever)` imports bundled synthetic metrics/projects/commentary.
`service.MarketService(data_dir, provider=None)` owns storage, seeds demo, exposes
`chat(ChatRequest)->ChatResponse`, `sources()->list[Source]`,
`metrics(MetricQuery)->list[Metric]`, `ingest(IngestRequest)->IngestResult`, `close()`.

## Workflow

START → understand_request → route_skills → execute_tools → combine_evidence →
generate_answer → verify_grounding → END. Typed graph state records artifacts, never
private reasoning. Deterministic routing selects multiple business skills. SQLite is
authoritative for numbers; Qdrant supplies excerpts. SQL is parameterized and bounded.
Latest means latest stored period, not live data; historical requests retain older rows.

## Grounding and model boundary

Deterministic tools produce atomic Claim objects with source IDs. Default synthesis
formats those claims offline. Supply and macro routes add labelled, rule-based risk
interpretations from relevant evidence; these are separate from facts and calculations. Optional OpenAI-compatible synthesis may only select
provided claims. It cannot add new facts or interpretation. Verification rejects unknown
citations and modified factual claims, falling back to verified evidence. Source text is
untrusted data. No model executes SQL or fetches source URLs. Text/Markdown ingestion is
supported; URL is metadata only, avoiding SSRF and scraping dependencies.

## Temporal and failure semantics

Metric periods describe observations; publication dates describe evidence availability.
Quarter queries filter SQL exactly and exclude later commentary. Latest queries use
latest stored rows, not the wall clock. Conflicting values are not averaged and prevent
ambiguous deltas. Calculations use matching units, periods and submarkets. A missing
requested quarter returns insufficient evidence. Publications older than ninety days
trigger a freshness warning.

Source text is preserved as an excerpt, never executed. Numeric excerpts are withheld
from answers because text ingestion does not validate metrics. Failed retrieval leaves
structured evidence available; failed model calls fall back to tool facts. Unknown or
modified claims and empty model selections fall back to verified evidence. One service
lock serializes SQLite/Qdrant operations; OpenAI calls have a twenty-second timeout.

## Deliberate limits

Synthetic data is prominently labelled. Hashed lexical vectors demonstrate offline vector
retrieval, not production semantic quality. One worker owns local Qdrant; production needs
server mode, stronger embeddings, curated source connectors, freshness SLAs and access control.
