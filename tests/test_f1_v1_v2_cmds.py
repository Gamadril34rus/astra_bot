"""F1: V1/V2 exit visibility + emergency cmd names match apply_pending (owner 22.10)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astra_bot.decision import tg_live_commands as tlc
from astra_bot.decision.live_commands import VALID, load_commands


def test_emergency_button_and_slash_enqueue_valid_cmds(tmp_path, monkeypatch):
    """All 4 emergency paths enqueue names that apply_pending understands."""
    path = tmp_path / "cmds.json"
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: None)
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")
    cases = [
        (tlc.BTN_RESET_KILL, "reset_kill"),
        (tlc.BTN_STOP_ENTRIES, "stop"),
        (tlc.BTN_RESUME_ENTRIES, "resume"),
        ("/reset_kill", "reset_kill"),
        ("/stop_entries", "stop"),
        ("/resume_entries", "resume"),
        ("/stop", "stop"),
        ("/resume", "resume"),
    ]
    for text, expect in cases:
        # fresh queue each time
        if path.exists():
            path.unlink()
        upd = {
            "update_id": 10,
            "message": {"text": text, "from": {"id": 1}, "chat": {"id": 1}},
        }
        cmd = tlc.process_update(upd, commands_path=path, token="t", admin_id="1")
        assert cmd == expect, text
        assert expect in VALID
        rows = load_commands(path)
        assert rows and rows[-1]["cmd"] == expect


def test_v1_http_401_warns_and_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(tlc, "HTTP_WARN_FLAG", tmp_path / "warn.json")
    menu = tmp_path / "menu.json"
    menu.write_text("{}")  # already pushed — skip menu noise
    monkeypatch.setattr(tlc, "MENU_FLAG_PATH", menu)
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"offset": 0}), encoding="utf-8")
    sent = []
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: sent.append(a[2] if len(a) > 2 else "") or True)
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")

    def boom(*a, **k):
        raise __import__("urllib").error.HTTPError("u", 401, "no", {}, None)

    monkeypatch.setattr(tlc, "_api", boom)
    with pytest.raises(SystemExit) as ei:
        tlc.poll_once("tok", offset_path=off, timeout_sec=1)
    assert ei.value.code == 1
    assert sent and "401" in sent[0] and "⚠️" in sent[0]


def test_v1_http_409_silent_return_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(tlc, "HTTP_WARN_FLAG", tmp_path / "warn.json")
    menu = tmp_path / "menu.json"
    menu.write_text("{}")
    monkeypatch.setattr(tlc, "MENU_FLAG_PATH", menu)
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"offset": 0}), encoding="utf-8")
    sent = []
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: sent.append("x"))
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")

    def boom(*a, **k):
        raise __import__("urllib").error.HTTPError("u", 409, "conflict", {}, None)

    monkeypatch.setattr(tlc, "_api", boom)
    n = tlc.poll_once("tok", offset_path=off, timeout_sec=1)
    assert n == 0
    assert not sent


def test_v1_dedup_second_401_no_duplicate_warn(tmp_path, monkeypatch):
    warn = tmp_path / "warn.json"
    monkeypatch.setattr(tlc, "HTTP_WARN_FLAG", warn)
    menu = tmp_path / "menu.json"
    menu.write_text("{}")
    monkeypatch.setattr(tlc, "MENU_FLAG_PATH", menu)
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"offset": 0}), encoding="utf-8")
    sent = []
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: sent.append(a[2] if len(a) > 2 else "") or True)
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")

    def boom(*a, **k):
        raise __import__("urllib").error.HTTPError("u", 403, "no", {}, None)

    monkeypatch.setattr(tlc, "_api", boom)
    with pytest.raises(SystemExit):
        tlc.poll_once("tok", offset_path=off, timeout_sec=1)
    assert len(sent) == 1
    with pytest.raises(SystemExit):
        tlc.poll_once("tok", offset_path=off, timeout_sec=1)
    assert len(sent) == 1  # dedup within 24h


def test_v2_yml_has_tg_warn_and_exit_1():
    yml = Path(".github/workflows/zeus-live-clock.yml").read_text(encoding="utf-8")
    assert "save lost race after 5 pushes" in yml
    assert "exit 1" in yml
    assert "sendMessage" in yml
    assert "live-clock: сохранение состояния" in yml or "журнал под угрозой" in yml
