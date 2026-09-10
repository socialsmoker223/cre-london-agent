"""Offline wire-contract checks; no credentials or live provider calls."""

import json
import time

import httpx
import pytest

from london_monitor.agent.graph import build_graph
from london_monitor.config import PROVIDERS, Settings
from london_monitor.models import Trace
from london_monitor.provider import OpenAICompatibleProvider, ProviderUnavailable


@pytest.mark.parametrize("name", PROVIDERS)
def test_provider_tool_roundtrip_and_final_json(name):
    provider = OpenAICompatibleProvider(Settings(
        provider=name, api_key="test-key", api_base="https://provider.test/v1",
        model="selected-model", crawl4ai_token="test-token",
    ))
    signature = {"google": {"thought_signature": "opaque-signature"}}
    reasoning_details = [{"type": "reasoning.encrypted", "data": "opaque-reasoning",
                          "id": "reasoning-1", "format": "openai-responses-v1", "index": 0}]
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url == "https://provider.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        assert body["model"] == "selected-model"
        token_key = "max_completion_tokens" if name == "openai" else "max_tokens"
        assert body[token_key] == 8192
        if len(requests) == 1:
            message = {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "search_web", "arguments": '{"query":"City"}'},
                    **({"extra_content": signature} if name == "gemini" else {}),
                }],
                **({"reasoning_content": "private reasoning"} if name == "deepseek" else {}),
                **({"reasoning_details": reasoning_details, "reasoning": "private reasoning"}
                   if name == "openrouter" else {}),
            }
        else:
            message = {"role": "assistant", "content": '{"insufficient_evidence":true}'}
        return httpx.Response(200, json={
            "id": "response-1", "object": "chat.completion", "created": 0,
            "model": "selected-model",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        })

    provider.client._client.close()
    provider.client._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        # Exercise the graph's actual transcript conversion before the next SDK request.
        trace = Trace(run_id="test", intent="test")
        result = build_graph(None, provider).nodes["agent"].bound.invoke({
            "messages": [{"role": "user", "content": "Return JSON about City rents"}],
            "trace": trace, "deadline": time.monotonic() + 60, "rounds": 0, "warnings": [],
        })
        assistant = result["messages"][-1]
        assert assistant["tool_calls"][0]["id"] == "call-1"
        if name == "gemini":
            assert assistant["tool_calls"][0]["extra_content"] == signature
        if name in {"deepseek", "openrouter"}:
            assert assistant["reasoning_content"] == "private reasoning"
        if name == "openrouter":
            assert assistant["reasoning_details"] == reasoning_details
            assert "opaque-reasoning" not in trace.model_dump_json()
        assert "private reasoning" not in trace.model_dump_json()
        result["messages"].append({"role": "tool", "tool_call_id": "call-1", "content": "[]"})
        turn = provider.complete(result["messages"], [], timeout=30)
        assert json.loads(turn.content) == {"insufficient_evidence": True}
        assert turn.usage == {"input_tokens": 12, "output_tokens": 4}
        assert requests[1]["messages"][-2] == assistant
        assert ("response_format" in requests[0]) == (name not in {"anthropic", "gemini"})
        assert ("response_format" in requests[1]) == (name != "anthropic")
        assert "tools" not in requests[1]
        assert ("reasoning_effort" in requests[0]) == (name == "z.ai")
        for body in requests:
            if name == "openrouter":
                assert body["provider"] == {"require_parameters": True, "allow_fallbacks": False}
            else:
                assert "provider" not in body
        with pytest.raises(ValueError, match="positive"):
            provider.complete([], [], timeout=0)
    finally:
        provider.client.close()


@pytest.mark.parametrize("status, message", [(401, "key was rejected"), (429, "rate limited"),
                                           (500, "model is unavailable")])
def test_provider_errors_are_safe_and_do_not_retry(status, message):
    provider = OpenAICompatibleProvider(Settings(
        provider="anthropic", api_key="test-key", api_base="https://provider.test/v1",
        model="selected-model", crawl4ai_token="test-token",
    ))
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "private account details"}})

    provider.client._client.close()
    provider.client._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ProviderUnavailable, match=message) as error:
            provider.complete([], [], timeout=30)
        assert "private account" not in str(error.value)
        assert len(calls) == 1
    finally:
        provider.client.close()
