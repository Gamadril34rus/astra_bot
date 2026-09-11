"""HTTP API key gate (TZ P0.1)."""

from __future__ import annotations

from astra_bot.core.api_auth import (
    ApiKeyMiddleware,
    authorize,
    is_open_path,
    is_protected_path,
    keys_match,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def test_open_paths():
    assert is_open_path("/")
    assert is_open_path("/health")
    assert is_open_path("/ping")
    assert is_open_path("/telegram/webhook")
    assert not is_protected_path("/health")


def test_protected_paths():
    for path in ("/tick", "/train", "/self_play", "/retrain", "/status", "/metrics"):
        assert is_protected_path(path)


def test_paper_without_key_is_open(monkeypatch):
    monkeypatch.delenv("ASTRA_API_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "paper")
    assert authorize(path="/tick", presented_key="") is None


def test_production_without_key_is_401(monkeypatch):
    monkeypatch.delenv("ASTRA_API_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    status, detail = authorize(path="/tick", presented_key="")
    assert status == 401
    assert "ASTRA_API_KEY" in detail


def test_wrong_key_is_401(monkeypatch):
    monkeypatch.setenv("ASTRA_API_KEY", "secret-key-value")
    monkeypatch.setenv("ENVIRONMENT", "paper")
    status, _ = authorize(path="/status", presented_key="nope")
    assert status == 401


def test_matching_key_ok(monkeypatch):
    monkeypatch.setenv("ASTRA_API_KEY", "secret-key-value")
    assert authorize(path="/tick", presented_key="secret-key-value") is None


def test_health_never_requires_key(monkeypatch):
    monkeypatch.setenv("ASTRA_API_KEY", "secret-key-value")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert authorize(path="/health", presented_key="") is None


def test_keys_match_is_length_safe():
    assert keys_match("ab", "abc") is False
    assert keys_match("abc", "abc") is True


def _app():
    async def tick(_request):
        return JSONResponse({"ok": True})

    async def health(_request):
        return JSONResponse({"status": "healthy"})

    app = Starlette(routes=[Route("/tick", tick), Route("/health", health)])
    app.add_middleware(ApiKeyMiddleware)
    return app


def test_middleware_rejects_tick_in_production(monkeypatch):
    monkeypatch.delenv("ASTRA_API_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    client = TestClient(_app())
    assert client.get("/health").status_code == 200
    assert client.get("/tick").status_code == 401


def test_middleware_accepts_header(monkeypatch):
    monkeypatch.setenv("ASTRA_API_KEY", "k" * 16)
    monkeypatch.setenv("ENVIRONMENT", "production")
    client = TestClient(_app())
    r = client.get("/tick", headers={"X-API-Key": "k" * 16})
    assert r.status_code == 200
    r = client.get("/tick", headers={"Authorization": "Bearer " + "k" * 16})
    assert r.status_code == 200
