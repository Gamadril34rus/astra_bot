"""Блок J: OI-история переживает перезапуск процесса (иначе oi_change=0)."""

from __future__ import annotations

import json
import time

import astra_bot.strategies.open_interest_divergence as oi


async def test_history_survives_session_restart(tmp_path, monkeypatch):
    fp = tmp_path / "oi_history.json"
    now = time.time()
    fp.write_text(
        json.dumps({"symbols": {"BTC-USDT": [[now - 100, 1000.0], [now - 10, 1010.0]]}}),
        encoding="utf-8",
    )
    # «Новая сессия»: чистый in-memory кэш.
    monkeypatch.setattr(oi, "_OI_HISTORY_FILE", fp)
    monkeypatch.setattr(oi, "_oi_cache", {})
    monkeypatch.setattr(oi, "_OI_HISTORY_LOADED", False)
    # Свежая точка в TTL → без сети, но С историей из файла.
    val = await oi._get_open_interest_val("BTC-USDT")
    assert val == 1010.0
    assert len(oi._oi_cache["BTC-USDT"]) == 2


def test_save_roundtrip(tmp_path, monkeypatch):
    fp = tmp_path / "oi_history.json"
    monkeypatch.setattr(oi, "_OI_HISTORY_FILE", fp)
    monkeypatch.setattr(oi, "_oi_cache", {"ETH-USDT": [(1.0, 500.0)]})
    oi._save_oi_history()
    data = json.loads(fp.read_text(encoding="utf-8"))
    assert data["symbols"]["ETH-USDT"] == [[1.0, 500.0]]
