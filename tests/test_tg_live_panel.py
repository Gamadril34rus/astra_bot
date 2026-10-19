"""LIVE TG panel in tg_live_commands (owner №24)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from astra_bot.decision import tg_live_commands as tlc
from astra_bot.decision.live_commands import load_commands


def test_button_constants_match_bot():
    from astra_bot.telegram import bot as paper_bot

    assert tlc.BTN_RESET_KILL == paper_bot.BTN_RESET_KILL
    assert tlc.BTN_FLAT == paper_bot.BTN_FLAT
    assert tlc.BTN_STOP_ENTRIES == paper_bot.BTN_STOP_ENTRIES
    assert tlc.BTN_RESUME_ENTRIES == paper_bot.BTN_RESUME_ENTRIES


def test_parse_button_to_cmd():
    assert tlc.parse_command(tlc.BTN_RESET_KILL) == "reset_kill"
    assert tlc.parse_command(tlc.BTN_STOP_ENTRIES) == "stop"
    assert tlc.parse_command(tlc.BTN_RESUME_ENTRIES) == "resume"
    assert tlc.parse_command(tlc.BTN_FLAT) is None  # confirm only
    assert tlc.parse_command("/reset_kill") == "reset_kill"


def test_callback_flat_yes_enqueues(tmp_path, monkeypatch):
    path = tmp_path / "cmds.json"
    sent = []
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: sent.append(a[2] if len(a) > 2 else k.get("text")))
    monkeypatch.setattr(tlc, "_api", MagicMock(return_value={"ok": True}))
    upd = {
        "callback_query": {
            "id": "1",
            "data": "live_cmd:flat_yes",
            "from": {"id": 42},
            "message": {"chat": {"id": 42}},
        }
    }
    cmd = tlc.process_callback(upd, commands_path=path, token="t", admin_id="42")
    assert cmd == "flat"
    assert load_commands(path)[0]["cmd"] == "flat"
    assert any("закрою" in str(s).lower() or "Принято" in str(s) for s in sent)


def test_callback_flat_no_no_enqueue(tmp_path, monkeypatch):
    path = tmp_path / "cmds.json"
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: None)
    monkeypatch.setattr(tlc, "_api", MagicMock(return_value={"ok": True}))
    upd = {
        "callback_query": {
            "id": "1",
            "data": "live_cmd:flat_no",
            "from": {"id": 42},
            "message": {"chat": {"id": 42}},
        }
    }
    assert tlc.process_callback(upd, commands_path=path, token="t", admin_id="42") is None
    assert load_commands(path) == []


def test_admin_deny(tmp_path, monkeypatch):
    path = tmp_path / "cmds.json"
    sent = []
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: sent.append("deny"))
    upd = {
        "message": {
            "text": tlc.BTN_RESET_KILL,
            "from": {"id": 99},
            "chat": {"id": 99},
        }
    }
    assert tlc.process_update(upd, commands_path=path, token="t", admin_id="42") is None
    assert load_commands(path) == []


def test_four_buttons_enqueue(tmp_path, monkeypatch):
    path = tmp_path / "cmds.json"
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: None)
    for text, expect in [
        (tlc.BTN_RESET_KILL, "reset_kill"),
        (tlc.BTN_STOP_ENTRIES, "stop"),
        (tlc.BTN_RESUME_ENTRIES, "resume"),
    ]:
        path.write_text('{"commands":[]}') if path.exists() else None
        # fresh path each
        p = tmp_path / f"{expect}.json"
        upd = {"message": {"text": text, "from": {"id": 1}, "chat": {"id": 1}}}
        assert tlc.process_update(upd, commands_path=p, token="t", admin_id="1") == expect
        assert load_commands(p)[0]["cmd"] == expect


def test_flat_button_no_enqueue_until_confirm(tmp_path, monkeypatch):
    path = tmp_path / "cmds.json"
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: None)
    upd = {
        "message": {
            "text": tlc.BTN_FLAT,
            "from": {"id": 1},
            "chat": {"id": 1},
        }
    }
    assert tlc.process_update(upd, commands_path=path, token="t", admin_id="1") is None
    assert load_commands(path) == []


def test_yml_handles_missing_tracked_file():
    yml = Path(".github/workflows/zeus-live-clock.yml")
    candidates = [
        Path("/home/workdir/artifacts/_github_workflows_zeus-live-clock_yml"),
        yml,
    ]
    body = ""
    for p in candidates:
        if p.exists():
            body = p.read_text(encoding="utf-8")
            break
    assert "git rm --cached" in body
    assert "ls-files --error-unmatch" in body
