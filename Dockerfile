FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 LONDON_DATA_DIR=/app/.runtime UV_CACHE_DIR=/tmp/uv-cache
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY data ./data
RUN uv sync --frozen --no-dev && rm -rf /tmp/uv-cache && mkdir /app/.runtime && chown -R 10001:10001 /app
USER 10001
EXPOSE 8000
CMD ["/app/.venv/bin/london-monitor", "serve"]
