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
def client():
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
    assert payload["paper_engine"] is True
    assert payload["risk_engine"] is True
    assert payload["equity"] == "1000"


def test_tick_endpoint_runs_one_iteration(client):
    response = client.get("/tick")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["iteration"] == "completed"
