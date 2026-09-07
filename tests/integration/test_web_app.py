"""Integration tests for the FastAPI web entry point (main.py)."""

import os
import sys
from pathlib import Path

import pytest

# Main находится в корне репозитория, а не в пакете astra_bot.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Изолируемся от локальной БД и реальных биржевых ключей.
os.environ.setdefault("ASTRA_CONFIG", str(ROOT / "config" / "settings.yaml"))


def _load_app():
    import importlib

    import main as web_main

    return importlib.reload(web_main).app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Симулятор рынка: веб-тесты не должны ждать сетевых таймаутов BingX.
    # Задаём через monkeypatch (setdefault на уровне модуля протекает
    # в другие тесты — conftest теперь подчищает такие утечки).
    monkeypatch.setenv("ASTRA_SIMULATE", "1")
    monkeypatch.setenv("ASTRA_STATE_DIR", str(tmp_path / "webtest"))
    from fastapi.testclient import TestClient

    app = _load_app()
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert "timestamp" in payload


def test_status_endpoint_reports_ready(client):
    response = client.get("/status")
    assert response.status_code == 200
    payload = response.json()
    # Контракт настоящего движка (не легаси-заглушки):
    assert payload["running"] is False  # цикл стартует только при ASTRA_CONTINUOUS=1
    assert payload["exchange"] is not None
    assert payload["strategies_loaded"] >= 7  # 16 стратегий пайплайна
    assert "equity" in payload
    assert payload["simulated"] is True


def test_tick_endpoint_runs_real_engine_step(client):
    response = client.get("/tick")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["engine"] is True


def test_metrics_endpoint_exposes_prometheus_format(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert b"astra_" in response.content
