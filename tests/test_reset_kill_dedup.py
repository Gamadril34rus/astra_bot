"""reset_kill spam guard (owner №26): idempotent + enqueue dedup."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.decision.live_commands import (
    DEDUP_WINDOW_MS,
    apply_pending,
    enqueue,
    load_commands,
)


def test_enqueue_dedup_pending(tmp_path):
    path = tmp_path / "cmds.json"
    assert enqueue("reset_kill", path=path) is True
    assert enqueue("reset_kill", path=path) is False
    assert len(load_commands(path)) == 1


def test_enqueue_dedup_done_within_15m(tmp_path):
    path = tmp_path / "cmds.json"
    assert enqueue("stop", path=path) is True
    cmds = load_commands(path)
    cmds[0]["status"] = "done"
    cmds[0]["ts"] = int(time.time() * 1000) - 60_000  # 1 min ago
    path.write_text(
        __import__("json").dumps({"commands": cmds}, indent=2), encoding="utf-8"
    )
    assert enqueue("stop", path=path) is False
    assert len(load_commands(path)) == 1


def test_enqueue_allowed_after_window(tmp_path):
    path = tmp_path / "cmds.json"
    assert enqueue("resume", path=path) is True
    cmds = load_commands(path)
    cmds[0]["status"] = "done"
    cmds[0]["ts"] = int(time.time() * 1000) - (DEDUP_WINDOW_MS + 1000)
    path.write_text(
        __import__("json").dumps({"commands": cmds}, indent=2), encoding="utf-8"
    )
    assert enqueue("resume", path=path) is True
    assert len(load_commands(path)) == 2


def test_reset_kill_already_clear_no_tg(tmp_path, monkeypatch):
    """No kill file → already_clear, no TG."""
    from astra_bot.decision import live_commands as lc
    from astra_bot.decision import live_risk

    kill = tmp_path / "kill_switch_state.json"
    # ensure absent
    if kill.exists():
        kill.unlink()
    monkeypatch.setattr(live_risk, "KILL_STATE_PATH", kill)

    path = tmp_path / "cmds.json"
    enqueue("reset_kill", path=path)
    broker = SimpleNamespace(clear_halt=MagicMock())
    sent = []

    async def tg(msg):
        sent.append(msg)

    lines = asyncio.run(apply_pending(broker, path=path, send_tg=tg))
    assert any("already_clear" in x for x in lines)
    assert sent == []
    cmds = load_commands(path)
    assert cmds[0]["status"] == "done"
    assert cmds[0]["result"] == "already_clear"


def test_reset_kill_with_file_sends_tg(tmp_path, monkeypatch):
    from astra_bot.decision import live_risk

    kill = tmp_path / "kill_switch_state.json"
    kill.write_text('{"reason": "test"}', encoding="utf-8")
    monkeypatch.setattr(live_risk, "KILL_STATE_PATH", kill)

    path = tmp_path / "cmds.json"
    enqueue("reset_kill", path=path)
    broker = SimpleNamespace(clear_halt=MagicMock())
    sent = []

    async def tg(msg):
        sent.append(msg)

    lines = asyncio.run(apply_pending(broker, path=path, send_tg=tg))
    assert any("kill state cleared" in x for x in lines)
    assert any("reset_kill" in s for s in sent)
    assert not kill.exists()
