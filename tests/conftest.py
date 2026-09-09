import pytest


@pytest.fixture(autouse=True)
def offline_default(monkeypatch):
    monkeypatch.setenv("LONDON_MODE", "demo")
