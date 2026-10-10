"""Tests for TG stop-button handlers (enqueue only)."""
from __future__ import annotations

from pathlib import Path

from astra_bot.decision.live_commands import load_commands
from astra_bot.decision.tg_live_commands import parse_command, process_update


def test_parse_command_variants():
    assert parse_command("/stop") == "stop"
    assert parse_command("/flat@MyBot") == "flat"
    assert parse_command("  /resume extra") == "resume"
    assert parse_command("/unknown") is None
    assert parse_command(None) is None
    assert parse_command("hello") is None


def test_process_update_enqueues(tmp_path: Path):
    path = tmp_path / "cmds.json"
    upd = {
        "update_id": 1,
        "message": {"text": "/stop", "chat": {"id": 42}},
    }
    assert process_update(upd, commands_path=path, token=None) == "stop"
    cmds = load_commands(path)
    pending = [c for c in cmds if c.get("status") == "pending"]
    assert len(pending) == 1
    assert pending[0]["cmd"] == "stop"
    assert pending[0]["source"] == "tg"


def test_process_update_flat_resume(tmp_path: Path):
    path = tmp_path / "cmds.json"
    assert process_update(
        {"update_id": 2, "message": {"text": "/flat"}},
        commands_path=path,
    ) == "flat"
    assert process_update(
        {"update_id": 3, "message": {"text": "/resume@bot"}},
        commands_path=path,
    ) == "resume"
    cmds = load_commands(path)
    assert [c["cmd"] for c in cmds if c.get("status") == "pending"] == ["flat", "resume"]
