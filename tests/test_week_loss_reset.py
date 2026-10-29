"""Owner 29.10: week loss reset epoch floors day/week PnL window."""
from __future__ import annotations

import json
from decimal import Decimal

from astra_bot.decision.live_risk import (
    check_day_week_limits,
    load_week_loss_reset,
    write_week_loss_reset,
)


def test_week_reset_drops_pre_reset_losses(tmp_path, monkeypatch):
    trades = tmp_path / "trades.jsonl"
    # -6000 before reset, -100 after — without reset both count
    rows = [
        {"event": "exit", "ts": 1000.0, "pnl": -6000.0},
        {"event": "exit", "ts": 2000.0, "pnl": -100.0},
    ]
    trades.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    reset_path = tmp_path / "week_reset.json"
    monkeypatch.setattr("astra_bot.decision.live_risk.WEEK_LOSS_RESET_PATH", reset_path)
    monkeypatch.setattr("astra_bot.decision.live_risk.utc_week_start_ts", lambda now=None: 0.0)
    monkeypatch.setattr("astra_bot.decision.live_risk.utc_day_start_ts", lambda now=None: 0.0)

    from datetime import datetime, timezone
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    eq = Decimal("100000")

    reason = check_day_week_limits(eq, path=trades, now=now)
    assert reason is not None
    assert "loss" in reason  # day_loss or week_loss — both fire on -6100

    write_week_loss_reset(1500.0, path=reset_path)
    assert load_week_loss_reset(reset_path) == 1500.0
    reason2 = check_day_week_limits(eq, path=trades, now=now)
    # only -100 after reset → 0.1% < 4% and < 6%
    assert reason2 is None


def test_write_week_loss_reset_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "w.json"
    monkeypatch.setattr("astra_bot.decision.live_risk.WEEK_LOSS_RESET_PATH", p)
    ts = write_week_loss_reset(12345.5, path=p)
    assert ts == 12345.5
    assert load_week_loss_reset(p) == 12345.5
