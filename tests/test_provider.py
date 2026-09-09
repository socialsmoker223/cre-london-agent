import json
from types import SimpleNamespace

import pytest

import london_monitor.provider as provider
from london_monitor.models import Claim

CLAIMS = [
    Claim(text="City vacancy is 8%.", source_ids=["s1"]),
    Claim(text="Take-up rose.", source_ids=["s2"]),
]


class FakeClient:
    def __init__(self, content, usage=None):
        self.kwargs = None
        self.content = content
        self.usage = usage
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.content)))],
            usage=self.usage,
        )


def make_provider(monkeypatch, content, usage=None):
    fake = FakeClient(content, usage)
    monkeypatch.setattr(provider, "OpenAI", lambda **kwargs: fake)
    return provider.OpenAIProvider(), fake


def test_openai_provider_selects_deduplicated_claims_and_reports_usage(monkeypatch):
    client, fake = make_provider(
        monkeypatch,
        {"indices": [1, 0, 1]},
        SimpleNamespace(prompt_tokens=12, completion_tokens=4),
    )
    draft = client.synthesize("What changed?", CLAIMS)
    assert draft.claims == [CLAIMS[1], CLAIMS[0]]
    assert draft.usage == {"input_tokens": 12, "output_tokens": 4}
    assert fake.kwargs["response_format"] == {"type": "json_object"}
    assert json.loads(fake.kwargs["messages"][1]["content"])["question"] == "What changed?"


@pytest.mark.parametrize(
    "content",
    [{}, {"indices": []}, {"indices": [2]}, {"indices": [True]}, {"indices": ["0"]}],
)
def test_openai_provider_rejects_invalid_selections(monkeypatch, content):
    client, _ = make_provider(monkeypatch, content)
    with pytest.raises(ValueError, match="Invalid evidence selection"):
        client.synthesize("Question", CLAIMS)


def test_openai_provider_configures_timeout_and_zero_retries(monkeypatch):
    client = None
    kwargs = {}

    def fake_openai(**values):
        nonlocal client
        kwargs.update(values)
        return FakeClient({"indices": [0]})

    monkeypatch.setattr(provider, "OpenAI", fake_openai)
    client = provider.OpenAIProvider()
    assert kwargs["timeout"] == 20
    assert kwargs["max_retries"] == 0
