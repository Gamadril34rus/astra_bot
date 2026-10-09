"""Tests for minimal Zeus Telegram status (exits + daily reports)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

# Import helpers from the script under test
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import zeus_telegram_status as tg  # noqa: E402

MSK = timezone(timedelta(hours=3))


def test_sum_pnl_for_trade_id_parts():
    trades = [
        {"id": "abc", "pnl": 1.5, "symbol": "TIA-USDT"},
        {"id": "abc", "pnl": 1.11, "symbol": "TIA-USDT"},
        {"id": "xyz", "pnl": -9.0, "symbol": "BTC-USDT"},
    ]
    assert tg.sum_pnl_for_trade_id(trades, "abc") == pytest.approx(2.61)
    assert tg.sum_pnl_for_trade_id(trades, "xyz") == pytest.approx(-9.0)
    assert tg.sum_pnl_for_trade_id(trades, "missing") is None


def test_format_exit_line_money_and_reason():
    line = tg.format_exit_line("TIA-USDT", "long", 2.61, "tp2")
    assert line == "🏁 TIA ЛОНГ: закрыта +2,61$ (тейк2)"
    line2 = tg.format_exit_line("TIA-USDT", "short", -5.66, "stop_loss")
    assert line2 == "🏁 TIA ШОРТ: закрыта −5,66$ (стоп)"
    line3 = tg.format_exit_line("ETH-USDT", "long", None, "stop_loss")
    assert "+? (сверка утром)" in line3
    assert tg._money(None) == "+?"


def test_daily_report_zero_trades():
    body = tg.format_daily_report(
        label="За ночь",
        window_parts=[],
        equity=2000.0,
        window_pnl=0.0,
        audit_line="нет данных",
        incidents=[],
    )
    assert "За ночь: 0 сделок" in body
    assert "Поломок нет." in body
    assert "нет данных" in body


def test_due_report_no_duplicate_both_windows():
    state: dict = {}
    # 09:30 MSK = 06:30 UTC → morning due
    morning_now = datetime(2026, 10, 9, 6, 30, tzinfo=UTC)
    assert tg.due_report_kind(morning_now, state) == "morning"
    tg.mark_report_sent(state, morning_now, "morning")
    # same morning again → not due
    assert tg.due_report_kind(morning_now + timedelta(minutes=5), state) is None
    # 21:30 MSK = 18:30 UTC → evening due
    evening_now = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)
    assert tg.due_report_kind(evening_now, state) == "evening"
    tg.mark_report_sent(state, evening_now, "evening")
    assert tg.due_report_kind(evening_now + timedelta(minutes=5), state) is None


def test_old_push_types_not_collected(tmp_path, monkeypatch):
    """entry / structure_state / reject / ltf_impulse / stop_adjust must not push."""
    journal = [
        {"event": "entry", "symbol": "BTC-USDT", "direction": "long", "ts": 1_000},
        {"event": "structure_state", "symbol": "ETH-USDT", "snapshot": {"would_signal": True}, "ts": 1_001},
        {"event": "reject", "stage": "retest", "reason": "rr_too_low", "ts": 1_002},
        {"event": "ltf_impulse", "symbol": "SOL-USDT", "near_structure": True, "ts": 1_003},
        {"event": "stop_adjust", "symbol": "BNB-USDT", "old_stop": 1, "new_stop": 2, "ts": 1_004},
        {"event": "exit", "symbol": "TIA-USDT", "direction": "long", "reason": "tp2", "ts": 2_000},
    ]
    trades = [
        {"id": "t1", "symbol": "TIA-USDT", "direction": "long", "pnl": 2.61, "exit_reason": "tp2", "closed_at": 2_000},
    ]
    exits = tg.collect_exit_pushes(journal, trades, since_ts=0)
    incidents = tg.collect_incident_pushes(journal, since_ts=0)
    assert len(exits) == 1
    assert exits[0].startswith("🏁 TIA")
    assert incidents == []
    # ensure no accidental inclusion of old event types
    blob = "\n".join(exits + incidents)
    for banned in ("🟢", "📌", "⏸", "⚡", "🔧", "тихо, сделок нет"):
        assert banned not in blob
