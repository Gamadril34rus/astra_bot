"""F2: flat confirm-only, send_reply bool flags, balance freshness, Save env."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from astra_bot.decision import tg_live_commands as tlc
from astra_bot.decision.live_commands import load_commands


def test_save_step_has_telegram_env():
    yml = Path(".github/workflows/zeus-live-clock.yml").read_text(encoding="utf-8")
    idx = yml.find("name: Save live commands state")
    assert idx >= 0
    chunk = yml[idx : idx + 600]
    assert "TELEGRAM_LIVE_BOT_TOKEN" in chunk.split("run:")[0]
    assert "sendMessage" in yml
    assert "exit 1" in yml


def test_flat_slash_requires_confirm_no_enqueue(tmp_path, monkeypatch):
    path = tmp_path / "cmds.json"
    sent = []
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: sent.append(a[2] if len(a) > 2 else "") or True)
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")
    for text in ("/flat", "стоп всех сделок", tlc.BTN_FLAT):
        if path.exists():
            path.unlink()
        upd = {"update_id": 1, "message": {"text": text, "from": {"id": 1}, "chat": {"id": 1}}}
        cmd = tlc.process_update(upd, commands_path=path, token="t", admin_id="1")
        assert cmd is None
        assert load_commands(path) == []
        assert any("Закрыть" in s for s in sent)


def test_flat_yes_enqueues(tmp_path, monkeypatch):
    path = tmp_path / "cmds.json"
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: True)
    monkeypatch.setattr(tlc, "_api", lambda *a, **k: {"ok": True})
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")
    upd = {
        "callback_query": {
            "id": "1",
            "data": "live_cmd:flat_yes",
            "from": {"id": 1},
            "message": {"chat": {"id": 1}},
        }
    }
    cmd = tlc.process_update(upd, commands_path=path, token="t", admin_id="1")
    assert cmd == "flat"
    assert load_commands(path)[-1]["cmd"] == "flat"


def test_flat_no_empty_queue(tmp_path, monkeypatch):
    path = tmp_path / "cmds.json"
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: True)
    monkeypatch.setattr(tlc, "_api", lambda *a, **k: {"ok": True})
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")
    upd = {
        "callback_query": {
            "id": "1",
            "data": "live_cmd:flat_no",
            "from": {"id": 1},
            "message": {"chat": {"id": 1}},
        }
    }
    tlc.process_update(upd, commands_path=path, token="t", admin_id="1")
    assert load_commands(path) == []


def test_menu_flag_only_on_send_success(tmp_path, monkeypatch):
    flag = tmp_path / "menu.json"
    monkeypatch.setattr(tlc, "MENU_FLAG_PATH", flag)
    off = tmp_path / "off.json"
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: False)
    tlc.maybe_push_menu("tok", offset_path=off)
    assert not flag.exists()
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: True)
    tlc.maybe_push_menu("tok", offset_path=off)
    assert flag.exists()


def test_v1_warn_flag_only_on_send_success(tmp_path, monkeypatch):
    warn = tmp_path / "warn.json"
    menu = tmp_path / "menu.json"
    menu.write_text("{}")
    monkeypatch.setattr(tlc, "HTTP_WARN_FLAG", warn)
    monkeypatch.setattr(tlc, "MENU_FLAG_PATH", menu)
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"offset": 0}))
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")

    def boom(*a, **k):
        raise __import__("urllib").error.HTTPError("u", 401, "no", {}, None)

    monkeypatch.setattr(tlc, "_api", boom)
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: False)
    with pytest.raises(SystemExit):
        tlc.poll_once("tok", offset_path=off, timeout_sec=1)
    assert not warn.exists()

    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: True)
    with pytest.raises(SystemExit):
        tlc.poll_once("tok", offset_path=off, timeout_sec=1)
    assert warn.exists()


def test_balance_stale_marker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    old_ts = time.time() - 20 * 60
    (models / "zeus_live_positions.json").write_text(
        json.dumps({"equity": 1000.0, "ts": old_ts, "BTC-USDT": {"qty": 1}}),
        encoding="utf-8",
    )
    # path relative: code uses Path("models/...")
    text = tlc._fmt_balance()
    assert "устарели" in text or "⚠️" in text
    assert "МСК" in text


def test_balance_fresh_has_time(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / "zeus_live_positions.json").write_text(
        json.dumps({"equity": 2000.0, "ts": time.time(), "ETH-USDT": {"qty": 1}}),
        encoding="utf-8",
    )
    text = tlc._fmt_balance()
    assert "2000.00$" in text
    assert "МСК" in text
    assert "устарели" not in text
