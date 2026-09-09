import asyncio
import json
from collections import OrderedDict
from threading import Event, RLock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from london_monitor.agent.graph import build_graph
from london_monitor.api import create_app
from london_monitor.models import Evidence, ModelTurn, Source, ToolCall
from london_monitor.provider import ProviderUnavailable
from london_monitor.service import MarketService


def streaming_service():
    source = Source(id="report", title="Market report", publisher="Test", checksum="abc")
    evidence = Evidence(
        id="report:1", source_id="report", excerpt="City prime rent is £95 psf.",
        category="rents", submarket="City",
    )
    service = MarketService.__new__(MarketService)
    service.lock = RLock()
    service.conversations = OrderedDict()
    service.store = SimpleNamespace(list_sources=lambda: [source])
    service.retriever = SimpleNamespace(search=lambda query: [evidence])
    turns = iter([
        ModelTurn(tool_calls=[ToolCall(
            id="lookup", name="search_market_evidence", arguments='{"query":"City rents"}',
        )], reasoning_content="PRIVATE_REASONING"),
        ModelTurn(content=json.dumps({
            "conclusion": "Rental evidence", "claims": [
                {"text": "City prime rent is £95 psf.", "evidence_ids": ["report:1"]}
            ],
        }), reasoning_content="PRIVATE_REASONING"),
    ])
    service.provider = SimpleNamespace(complete=lambda *args, **kwargs: next(turns))
    service.graph = build_graph(service, service.provider)
    return service


def test_stream_events_are_ordered_grounded_and_do_not_expose_reasoning():
    service = streaming_service()
    with TestClient(create_app(service)) as client:
        response = client.post('/api/chat/stream', json={"question": "City rents"})
        assert response.headers['content-type'].startswith('text/event-stream')
        assert response.headers['x-accel-buffering'] == 'no'
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith('data: ')]
        assert all(event['type'] == 'activity' for event in events[:-1])
        messages = [event['message'] for event in events[:-1]]
        assert messages.index('Searching collected source excerpts') < messages.index(
            'Checking claims, numbers and source citations'
        )
        result = events[-1]
        assert result['type'] == 'result'
        assert '£95 psf. [1]' in result['data']['answer']
        assert result['data']['citations'][0]['source']['id'] == 'report'
        assert result['data']['conversation_id'] in service.conversations
        assert 'PRIVATE_REASONING' not in response.text
        assert client.post('/api/chat/stream', json={"question": "x"}).status_code == 422
    with TestClient(create_app(streaming_service())) as client:
        assert client.post('/api/chat', json={"question": "City rents"}).json()['citations']


@pytest.mark.parametrize('failure,public', [
    (ProviderUnavailable('The configured model timed out. Please retry.'), 'timed out'),
    (RuntimeError('SECRET_INTERNAL_DETAIL'), 'could not complete'),
])
def test_stream_errors_are_terminal_and_safe(failure, public):
    def chat(request, emit):
        emit({"type": "activity", "message": "Checking sources"})
        raise failure

    with TestClient(create_app(SimpleNamespace(chat=chat))) as client:
        response = client.post('/api/chat/stream', json={"question": "City rents"})
        event = json.loads(response.text.strip().split('data: ')[-1])
        assert event['type'] == 'error'
        assert public in event['message']
        assert 'SECRET_INTERNAL_DETAIL' not in response.text


def test_activity_delivered_before_completion_and_disconnect_stops_next_step():
    async def run():
        release = Event()
        cancelled = Event()
        activity_delivered = asyncio.Event()

        def chat(request, emit):
            emit({"type": "activity", "message": "Working"})
            assert release.wait(3)
            try:
                emit({"type": "activity", "message": "Must not arrive"})
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError('Disconnected research continued')

        app = create_app(SimpleNamespace(chat=chat))
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b'{"question":"City rents"}'})
        bodies = []

        async def send(message):
            if message['type'] == 'http.response.body':
                bodies.append(message.get('body', b''))
                if b'Working' in bodies[-1]:
                    activity_delivered.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
            "method": "POST", "path": "/api/chat/stream", "query_string": b'',
            "headers": [(b'content-type', b'application/json')],
        }
        async with app.router.lifespan_context(app):
            task = asyncio.create_task(app(scope, incoming.get, send))
            try:
                await asyncio.wait_for(activity_delivered.wait(), 3)
                assert not release.is_set()  # Streamed while service is still blocked.
                incoming.put_nowait({"type": "http.disconnect"})
                await asyncio.wait_for(task, 3)
            finally:
                release.set()
            assert await asyncio.to_thread(cancelled.wait, 3)
            assert b'Must not arrive' not in b''.join(bodies)

    asyncio.run(run())
