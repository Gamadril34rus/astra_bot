"""PF kill sliding window (owner 20.10)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from astra_bot.decision.live_risk import (
    check_day_week_limits,
    check_pf_kill,
    kill_pf_window_size,
    load_kill_epoch,
    profit_factor,
)


def _write_exits(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_window_only_after_epoch(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_KILL_PF_WINDOW", "20")
    trades = tmp_path / "t.jsonl"
    epoch_path = tmp_path / "epoch.json"
    epoch_path.write_text(json.dumps({"kill_epoch_ts": 1000.0}), encoding="utf-8")
    monkeypatch.setattr("astra_bot.decision.live_risk.KILL_EPOCH_PATH", epoch_path)
    rows = [{"event": "exit", "pnl": 100, "ts": 100 + i} for i in range(5)]
    rows += [{"event": "exit", "pnl": -50, "ts": 2000 + i} for i in range(5)]
    _write_exits(trades, rows)
    pf, n = profit_factor(trades, after_ts=1000.0, window=20)
    assert n == 5 and pf is not None and pf < 1
    reason = check_pf_kill(path=trades, state_path=tmp_path / "kill.json")
    assert reason is not None and "window=20" in reason


def test_n_less_than_5_no_kill(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_KILL_PF_WINDOW", "20")
    trades = tmp_path / "t.jsonl"
    epoch_path = tmp_path / "epoch.json"
    epoch_path.write_text(json.dumps({"kill_epoch_ts": 1.0}), encoding="utf-8")
    monkeypatch.setattr("astra_bot.decision.live_risk.KILL_EPOCH_PATH", epoch_path)
    _write_exits(trades, [{"event": "exit", "pnl": -10, "ts": 10 + i} for i in range(4)])
    assert check_pf_kill(path=trades, state_path=tmp_path / "k.json") is None


def test_flag_off_uses_cumulative(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEUS_KILL_PF_WINDOW", raising=False)
    trades = tmp_path / "t.jsonl"
    rows = [{"event": "exit", "pnl": 10, "ts": i} for i in range(3)]
    rows += [{"event": "exit", "pnl": -20, "ts": 100 + i} for i in range(5)]
    _write_exits(trades, rows)
    assert kill_pf_window_size() == 0
    reason = check_pf_kill(path=trades, state_path=tmp_path / "k.json")
    assert reason is not None and "cumulative" in reason


def test_epoch_missing_stays_cumulative(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_KILL_PF_WINDOW", "20")
    trades = tmp_path / "t.jsonl"
    monkeypatch.setattr("astra_bot.decision.live_risk.KILL_EPOCH_PATH", tmp_path / "nope.json")
    _write_exits(trades, [{"event": "exit", "pnl": -5, "ts": i} for i in range(6)])
    reason = check_pf_kill(path=trades, state_path=tmp_path / "k.json")
    assert reason is not None and "cumulative" in reason


def test_epoch_stable_read(tmp_path, monkeypatch):
    p = tmp_path / "epoch.json"
    p.write_text(json.dumps({"kill_epoch_ts": 42.5}), encoding="utf-8")
    monkeypatch.setattr("astra_bot.decision.live_risk.KILL_EPOCH_PATH", p)
    assert load_kill_epoch() == 42.5
    assert load_kill_epoch() == 42.5


def test_pre_epoch_excluded(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_KILL_PF_WINDOW", "20")
    trades = tmp_path / "t.jsonl"
    epoch_path = tmp_path / "epoch.json"
    epoch_path.write_text(json.dumps({"kill_epoch_ts": 500.0}), encoding="utf-8")
    monkeypatch.setattr("astra_bot.decision.live_risk.KILL_EPOCH_PATH", epoch_path)
    rows = [{"event": "exit", "pnl": -100, "ts": i} for i in range(10)]
    rows += [{"event": "exit", "pnl": -100, "ts": 600 + i} for i in range(3)]
    _write_exits(trades, rows)
    assert check_pf_kill(path=trades, state_path=tmp_path / "k.json") is None
    _, n = profit_factor(trades, after_ts=500.0, window=20)
    assert n == 3


def test_day_week_limits_untouched(tmp_path):
    import time
    trades = tmp_path / "t.jsonl"
    _write_exits(trades, [{"event": "exit", "pnl": -500, "ts": time.time()}])
    reason = check_day_week_limits(Decimal("10000"), path=trades)
    assert reason is not None and "day_loss" in reason
