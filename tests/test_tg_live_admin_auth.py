"""A-001: only TELEGRAM_ADMIN_ID may enqueue TG live commands."""
from __future__ import annotations

from astra_bot.decision.tg_live_commands import process_update


def test_non_admin_ignored() -> None:
    upd = {"update_id": 1, "message": {"text": "/stop", "chat": {"id": 999}}}
    assert process_update(upd, admin_id="111") is None


def test_missing_admin_ignored() -> None:
    upd = {"update_id": 1, "message": {"text": "/stop", "chat": {"id": 111}}}
    assert process_update(upd, admin_id="") is None


def test_admin_returns_cmd(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_enqueue(cmd: str, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr(
        "astra_bot.decision.tg_live_commands.enqueue", _fake_enqueue
    )
    upd = {"update_id": 2, "message": {"text": "/flat", "chat": {"id": 42}}}
    assert process_update(upd, admin_id="42") == "flat"
    assert calls == ["flat"]
