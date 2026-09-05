from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def pmd_page() -> str:
    """The real PMD pollen page, captured 2026-09-05.

    Kept byte-for-byte on purpose. The moment we tidy it up it stops being
    evidence of what PMD actually serves, and the parser tests start passing
    against a page that does not exist.
    """
    return (FIXTURES / "pmd_pollen_2026-09-05.html").read_text(
        encoding="utf-8", errors="replace"
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway SQLite database, isolated per test."""
    from hawa import settings_store
    from hawa.config import get_config
    from hawa.db import init_db, reset_engine

    monkeypatch.setenv("HAWA_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("HAWA_ENABLE_SCHEDULER", "false")
    monkeypatch.delenv("HAWA_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HAWA_PURPLEAIR_API_KEY", raising=False)
    monkeypatch.delenv("HAWA_OPENAQ_API_KEY", raising=False)

    get_config.cache_clear()
    reset_engine()
    settings_store.reset_cache()
    init_db()
    yield
    reset_engine()
    get_config.cache_clear()
    settings_store.reset_cache()


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from hawa.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _no_accidental_network(monkeypatch, request):
    """Fail loudly if a test makes a real HTTP call.

    Tests that legitimately exercise the network layer opt in with
    ``@pytest.mark.allow_network`` and mock the transport themselves.
    """
    if request.node.get_closest_marker("allow_network"):
        return
    import httpx

    def blocked(*args, **kwargs):
        raise AssertionError(
            "a test tried to make a real HTTP request; mock it or mark it allow_network"
        )

    # Patch the real-socket transport, not httpx.Client itself -- FastAPI's
    # TestClient is an httpx.Client over an in-process ASGI transport, and
    # blocking the client would block every API test instead of the network.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_network: test may use a mocked HTTP transport")


os.environ.setdefault("HAWA_ENABLE_SCHEDULER", "false")
