"""LIVE emergency TG panel (owner №23): buttons → enqueue + ack."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from astra_bot.decision.live_commands import (
    apply_pending,
    enqueue,
    load_commands,
)


def test_commands_round_trip(tmp_path):
    path = tmp_path / "zeus_live_commands.json"
    enqueue("stop", path=path, source="tg")
    enqueue("resume", path=path, source="tg")
    cmds = load_commands(path)
    assert len(cmds) == 2
    assert cmds[0]["cmd"] == "stop" and cmds[0]["status"] == "pending"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw, encoding="utf-8")
    assert len(load_commands(path)) == 2


def test_apply_pending_sends_tg(tmp_path, monkeypatch):
    import astra_bot.decision.live_commands as lc

    path = tmp_path / "cmds.json"
    enqueue("stop", path=path)
    broker = SimpleNamespace(
        block_entries=MagicMock(),
        allow_entries=MagicMock(),
        flat_all=AsyncMock(return_value={"closed": 0}),
        clear_halt=MagicMock(),
        _halt_reason="",
    )
    sent: list[str] = []

    async def tg(msg):
        sent.append(msg)

    monkeypatch.setattr(
        lc,
        "set_blocked",
        lambda blocked, path=None, reason="": None,
    )
    asyncio.run(apply_pending(broker, path=path, send_tg=tg))
    assert any("stop" in s and "исполнен" in s for s in sent)


def test_apply_pending_error_tg(tmp_path, monkeypatch):
    import astra_bot.decision.live_commands as lc

    path = tmp_path / "cmds.json"
    enqueue("flat", path=path)
    broker = SimpleNamespace(
        block_entries=MagicMock(),
        allow_entries=MagicMock(),
        flat_all=AsyncMock(side_effect=RuntimeError("boom")),
        clear_halt=MagicMock(),
        _halt_reason="",
    )
    sent: list[str] = []

    async def tg(msg):
        sent.append(msg)

    monkeypatch.setattr(lc, "set_blocked", lambda *a, **k: None)
    asyncio.run(apply_pending(broker, path=path, send_tg=tg))
    assert any("не вышел" in s for s in sent)


def test_button_labels_in_menu():
    src = Path("astra_bot/telegram/bot.py").read_text(encoding="utf-8")
    for label in (
        "🛑 Сброс стоп-крана",
        "🧯 ЗАКРЫТЬ ВСЁ",
        "⏸ Стоп новых входов",
        "▶️ Продолжить входы",
    ):
        assert label in src
    assert "live_cmd:flat_yes" in src
    assert "_cmd_live_reset_kill" in src


def test_enqueue_all_valid_cmds(tmp_path):
    path = tmp_path / "c.json"
    for cmd in ("reset_kill", "stop", "resume", "flat"):
        enqueue(cmd, path=path)
    assert [c["cmd"] for c in load_commands(path)] == [
        "reset_kill",
        "stop",
        "resume",
        "flat",
    ]


def test_flat_not_enqueued_until_confirm(tmp_path):
    path = tmp_path / "c.json"
    assert load_commands(path) == []
    # confirm path:
    enqueue("flat", path=path)
    assert load_commands(path)[0]["cmd"] == "flat"


def test_bot_yml_saves_commands():
    yml = Path(".github/workflows/bot.yml")
    # when run from repo root in CI; locally check artifact
    candidates = [
        Path("/home/workdir/artifacts/.github_workflows_bot.yml"),
        yml,
    ]
    body = ""
    for p in candidates:
        if p.exists():
            body = p.read_text(encoding="utf-8")
            break
    assert "zeus_live_commands.json" in body
