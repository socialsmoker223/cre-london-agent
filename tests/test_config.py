import pytest

import london_monitor.config as config
from london_monitor.models import ChatRequest


def test_only_explicit_live_provider_configuration_is_accepted(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda **kwargs: None)
    values = {
        "LLM_PROVIDER": "z.ai",
        "ZAI_API_KEY": "configuration-test-key",
        "LLM_MODEL": "glm-5.3-flash",
        "ZAI_API_BASE": "https://api.z.ai/api/coding/paas/v4",
        "LONDON_MODE": "live",
        "CRAWL4AI_API_TOKEN": "configuration-test-token",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    settings = config.Settings.from_env()
    assert settings.model == values["LLM_MODEL"]
    assert values["ZAI_API_KEY"] not in repr(settings)
    for key in ("LLM_PROVIDER", "ZAI_API_KEY", "LLM_MODEL", "ZAI_API_BASE"):
        monkeypatch.setenv(key, " ")
        with pytest.raises(ValueError, match=key):
            config.Settings.from_env()
        monkeypatch.setenv(key, values[key])
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        config.Settings.from_env()
    monkeypatch.setenv("LLM_PROVIDER", "z.ai")
    monkeypatch.setenv("LONDON_MODE", "demo")
    with pytest.raises(ValueError, match="Only live mode"):
        config.Settings.from_env()


@pytest.mark.parametrize("provider", config.PROVIDERS)
def test_provider_configuration_and_request_selection(provider, monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda **kwargs: None)
    prefix = "ZAI" if provider == "z.ai" else provider.upper()
    for key, value in {
        "LONDON_MODE": "live", "LLM_PROVIDER": provider, "LLM_MODEL": "primary-model",
        "CRAWL4AI_API_TOKEN": "test-token", f"{prefix}_API_KEY": "test-key",
        f"{prefix}_API_BASE": "https://provider.test/v1", f"{prefix}_MODEL": "secondary-model",
    }.items():
        monkeypatch.setenv(key, value)
    assert config.Settings.from_env().model == "primary-model"
    assert ChatRequest(question="City rents", provider=provider).provider == provider
    monkeypatch.setenv("LLM_PROVIDER", "openai" if provider != "openai" else "z.ai")
    assert config.Settings.from_env(provider).model == "secondary-model"
    for suffix in ("API_KEY", "API_BASE", "MODEL"):
        key = f"{prefix}_{suffix}"
        with monkeypatch.context() as patch:
            patch.setenv(key, " ")
            with pytest.raises(ValueError, match=key):
                config.Settings.from_env(provider)
