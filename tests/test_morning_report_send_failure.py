"""Блок C: полный провал отправки отчёта — громкий SystemExit(1)."""

from __future__ import annotations

import asyncio

import pytest
import scripts.morning_report as mr


class _FailBot:
    def __init__(self, token: str = ""):
        pass

    async def send_message(self, **kwargs):
        raise RuntimeError("no network")


class _FlakyBot:
    def __init__(self, token: str = ""):
        pass

    async def send_message(self, **kwargs):
        if kwargs.get("chat_id") == 1:
            return None
        raise RuntimeError("no network")


def test_total_failure_exits_1(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1,2")
    monkeypatch.setattr(mr, "Bot", _FailBot)
    with pytest.raises(SystemExit) as exc:
        asyncio.run(mr.send_to_telegram("hello"))
    assert exc.value.code == 1


def test_partial_send_ok(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1,2")
    monkeypatch.setattr(mr, "Bot", _FlakyBot)
    asyncio.run(mr.send_to_telegram("hello"))  # хоть одно дошло — exit 0
