import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    ChatRequest,
    ChatResponse,
    IngestRequest,
    IngestResult,
    Metric,
    MetricName,
    MetricQuery,
    RefreshRequest,
    RefreshResult,
    Source,
    Submarket,
)
from .provider import ProviderUnavailable

WEB_DIR = Path(__file__).with_name("web")


def _service_error() -> HTTPException:
    return HTTPException(
        status_code=500, detail="The market service could not complete the request."
    )


def create_app(service: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = service is None
        if owned:
            from .service import MarketService

            app.state.service = MarketService()
        else:
            app.state.service = service
        try:
            yield
        finally:
            if owned:
                close = getattr(app.state.service, "close", None)
                if close:
                    close()

    app = FastAPI(title="London Market Monitor", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        try:
            return request.app.state.service.chat(payload)
        except ProviderUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise _service_error() from exc

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatRequest, request: Request):
        async def events():
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()
            stopped = Event()

            def emit(event):
                # Cancellation is checked between bounded model/tool calls.
                if stopped.is_set():
                    raise asyncio.CancelledError()
                loop.call_soon_threadsafe(queue.put_nowait, event)

            def run():
                try:
                    result = request.app.state.service.chat(payload, emit=emit)
                    emit({"type": "result", "data": result.model_dump(mode="json")})
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    if not stopped.is_set():
                        emit({
                            "type": "error",
                            "message": str(exc) if isinstance(exc, ProviderUnavailable)
                            else "The market service could not complete the request.",
                        })

            task = asyncio.create_task(asyncio.to_thread(run))
            try:
                yield 'data: {"type":"activity","message":"Starting research"}\n\n'
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=10)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event["type"] in {"result", "error"}:
                        break
            finally:
                stopped.set()
                task.cancel()

        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/sources", response_model=list[Source])
    def sources(request: Request) -> list[Source]:
        try:
            return request.app.state.service.sources()
        except Exception as exc:
            raise _service_error() from exc

    @app.delete("/api/sources/{source_id}")
    def remove_source(source_id: str, request: Request):
        try:
            if not request.app.state.service.remove_source(source_id):
                raise HTTPException(status_code=404, detail="Source not found.")
            return {"removed": True}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise _service_error() from exc

    @app.post("/api/ingest", response_model=IngestResult)
    def ingest(payload: IngestRequest, request: Request) -> IngestResult:
        try:
            return request.app.state.service.ingest(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise _service_error() from exc

    @app.get("/api/metrics")
    def metrics(
        request: Request,
        submarkets: Annotated[list[Submarket] | None, Query(max_length=5)] = None,
        metric: Annotated[list[MetricName] | None, Query(alias="metrics", max_length=11)] = None,
        period: Annotated[str | None, Query(pattern=r"^\d{4}-Q[1-4]$")] = None,
        latest: bool = True,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> list[Metric]:
        try:
            query = MetricQuery(
                submarkets=submarkets or [],
                metrics=metric or [],
                period=period,
                latest=latest,
                limit=limit,
            )
            return request.app.state.service.metrics(query)
        except HTTPException:
            raise
        except Exception as exc:
            raise _service_error() from exc

    @app.get("/api/status")
    def status(request: Request):
        return request.app.state.service.status()

    @app.get("/api/refresh", response_model=RefreshResult | None)
    def latest_refresh(request: Request):
        return request.app.state.service.latest_refresh()

    @app.post("/api/refresh", response_model=RefreshResult)
    def refresh(payload: RefreshRequest, request: Request):
        try:
            return request.app.state.service.refresh(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise _service_error() from exc

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


app = create_app()
