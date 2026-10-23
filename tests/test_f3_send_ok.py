"""F3: send_reply confirms API ok; V1 flag only after delivery; balance mid-band."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astra_bot.decision import tg_live_commands as tlc


def test_send_reply_ok_false():
    tlc._api = MagicMock(return_value={"ok": False})  # type: ignore[method-assign]
    assert tlc.send_reply("tok", 1, "hi") is False


def test_send_reply_ok_true():
    tlc._api = MagicMock(return_value={"ok": True})  # type: ignore[method-assign]
    assert tlc.send_reply("tok", 1, "hi") is True


def test_send_reply_exception_returns_false():
    def boom(*a, **k):
        raise RuntimeError("net")

    tlc._api = boom  # type: ignore[method-assign]
    assert tlc.send_reply("tok", 1, "hi") is False


def test_v1_no_chat_no_warn_flag(tmp_path, monkeypatch):
    warn = tmp_path / "warn.json"
    menu = tmp_path / "menu.json"
    menu.write_text("{}")
    monkeypatch.setattr(tlc, "HTTP_WARN_FLAG", warn)
    monkeypatch.setattr(tlc, "MENU_FLAG_PATH", menu)
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"offset": 0}))
    monkeypatch.delenv("TELEGRAM_ADMIN_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    def boom(*a, **k):
        raise __import__("urllib").error.HTTPError("u", 401, "no", {}, None)

    monkeypatch.setattr(tlc, "_api", boom)
    with pytest.raises(SystemExit) as ei:
        tlc.poll_once("tok", offset_path=off, timeout_sec=1)
    assert ei.value.code == 1
    assert not warn.exists()


def test_v1_send_fail_no_warn_flag(tmp_path, monkeypatch):
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


def test_balance_mid_stale_band(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / "zeus_live_positions.json").write_text(
        json.dumps({"equity": 1500.0, "ts": time.time() - 7 * 60, "X": {"qty": 1}}),
        encoding="utf-8",
    )
    text = tlc._fmt_balance()
    assert "(несвежие)" in text
    assert "устарели" not in text


def test_balance_very_stale_and_fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / "zeus_live_positions.json").write_text(
        json.dumps({"equity": 1500.0, "ts": time.time() - 20 * 60, "X": {"qty": 1}}),
        encoding="utf-8",
    )
    text = tlc._fmt_balance()
    assert "устарели" in text or "⚠️" in text
    (models / "zeus_live_positions.json").write_text(
        json.dumps({"equity": 1500.0, "ts": time.time(), "X": {"qty": 1}}),
        encoding="utf-8",
    )
    text2 = tlc._fmt_balance()
    assert "(несвежие)" not in text2
    assert "устарели" not in text2
