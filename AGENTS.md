# Repository Guidelines

## Project Structure & Module Organization

`src/london_monitor/` contains the Python application: `models.py` defines Pydantic contracts, `service.py` owns storage and orchestration, and `agent/graph.py` implements the grounded-answer workflow. FastAPI and CLI entry points are `api.py` and `cli.py`; static dashboard assets live in `web/`. Tests are in `tests/`, and architecture documentation in `docs/ARCHITECTURE.md`. `scripts/build_slides.py` generates the presentation in `deliverables/`.

## Build, Test, and Development Commands

Use Python 3.12+ and project-local dependencies managed by `uv`.

- `uv sync --frozen` — install dependencies from `uv.lock`.
- `uv run london-monitor serve --port 8000` — run the API and dashboard.
- `uv run london-monitor ask "What is happening to prime rents in the City?"` — query from the CLI.
- `uv run ruff check .` — run CI lint checks.
- `uv run pytest` — run the test suite.
- `uv run london-monitor smoke` — check the storage-to-answer flow.
- `uv run london-monitor eval` — run grounded-answer evaluation cases.
- `uv run london-monitor refresh` — crawl and ingest documents (baseline or incremental).
- `docker build -t london-monitor .` — build the container.
- `docker compose up` — run app, Qdrant, and crawl4ai together; requires `LLM_PROVIDER`, `LLM_MODEL`,
  the selected provider's API key/base, and `CRAWL4AI_API_TOKEN` in `.env` or the shell.

## Coding Style & Naming Conventions

Use four-space Python indentation, snake_case functions and modules, and PascalCase classes. Follow existing type annotations and shared Pydantic contracts. Ruff enforces a 100-character line limit, import sorting, and configured correctness and modernization rules. Match the dashboard's two-space JavaScript indentation and camelCase names. Reuse existing service and retrieval boundaries.

## Testing Guidelines

Use pytest with `tests/test_*.py` files and `test_*` functions. Isolate storage with `tmp_path` and close `MarketService` after use. Add focused regression checks for changed behavior, especially grounding, historical periods, and provider failures. Run all four CI checks: Ruff, pytest, smoke, and eval, with `LLM_PROVIDER=offline`. No numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

The existing history uses `feat(agent): ...`; follow that scoped Conventional Commit style. Keep changes focused. PRs should describe behavior changes, link relevant issues, report validation, and include screenshots for dashboard changes.

## Configuration & Data Integrity

Keep credentials and `.runtime/` untracked. `.env` loads automatically; exported variables take
precedence. The live service uses remote Qdrant. `LONDON_DATA_DIR` isolates SQLite, not the remote
vector collection; use a separate Qdrant dataset for fully isolated runs. Use one app process and
stop it before CLI operations on the same dataset. Synthetic sources are rejected by the live
service; offline smoke/eval use explicit test fixtures. Preserve source citations and definitions;
SQLite remains authoritative for numerical metrics.
