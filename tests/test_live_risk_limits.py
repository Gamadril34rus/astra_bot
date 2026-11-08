"""A-002/A-004 live day/week/PF limits from trades.jsonl."""
from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

from astra_bot.decision.live_risk import (
    check_day_week_limits,
    check_pf_kill,
    profit_factor,
)


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_day_limit_4pct(tmp_path: Path) -> None:
    p = tmp_path / "trades.jsonl"
    now = time.time()
    _write(
        p,
        [
            {"event": "exit", "pnl": -5.0, "ts": now - 60},
            {"event": "exit", "pnl": -4.0, "ts": now - 30},
        ],
    )
    # equity 100 → 9% day loss > 4%
    reason = check_day_week_limits(Decimal("100"), path=p)
    assert reason is not None
    assert "day_loss" in reason


def test_day_under_limit(tmp_path: Path) -> None:
    p = tmp_path / "trades.jsonl"
    now = time.time()
    _write(p, [{"event": "exit", "pnl": -3.0, "ts": now - 10}])
    assert check_day_week_limits(Decimal("100"), path=p) is None


def test_pf_kill_n5(tmp_path: Path) -> None:
    p = tmp_path / "trades.jsonl"
    state = tmp_path / "kill.json"
    now = time.time()
    rows = []
    for i, pnl in enumerate([1.0, -2.0, -2.0, -2.0, -2.0]):
        rows.append({"event": "exit", "pnl": pnl, "ts": now - i})
    _write(p, rows)
    pf, n = profit_factor(p)
    assert n == 5
    assert pf is not None and pf < 1
    reason = check_pf_kill(path=p, state_path=state)
    assert reason is not None
    assert state.exists()


def test_pf_insufficient_n(tmp_path: Path) -> None:
    p = tmp_path / "trades.jsonl"
    now = time.time()
    _write(p, [{"event": "exit", "pnl": -1.0, "ts": now}])
    pf, n = profit_factor(p)
    assert pf is None and n == 1


def test_pf_ignores_partial_income_and_deduplicates_final_exit(tmp_path: Path) -> None:
    p = tmp_path / "trades.jsonl"
    _write(
        p,
        [
            {"event": "partial_fill", "income_id": "income-1", "pnl": 100.0, "ts": 1},
            {"event": "exit", "trade_id": "trade-1", "pnl": -50.0, "ts": 2},
            {"event": "exit", "trade_id": "trade-1", "pnl": -40.0, "ts": 3},
            {"event": "exit", "trade_id": "trade-2", "pnl": 80.0, "ts": 4},
            {"event": "exit", "trade_id": "trade-3", "pnl": 0.0, "ts": 5},
            {"event": "exit", "trade_id": "trade-4", "pnl": 0.0, "ts": 6},
            {"event": "exit", "trade_id": "trade-5", "pnl": 0.0, "ts": 7},
        ],
    )
    pf, n = profit_factor(p)
    assert n == 5
    assert pf is not None
    assert abs(float(pf) - 2.0) < 1e-9


def test_day_loss_deduplicates_repeated_exit(tmp_path: Path) -> None:
    p = tmp_path / "trades.jsonl"
    now = time.time()
    _write(
        p,
        [
            {"event": "exit", "trade_id": "trade-1", "pnl": -3.0, "ts": now - 20},
            {"event": "exit", "trade_id": "trade-1", "pnl": -3.0, "ts": now - 10},
        ],
    )
    assert check_day_week_limits(Decimal("100"), path=p) is None


def test_vst_performance_halt_override_is_endpoint_scoped(monkeypatch) -> None:
    from astra_bot.decision.live_risk import should_disable_vst_performance_halt

    monkeypatch.setenv("ZEUS_VST", "1")
    monkeypatch.setenv("ZEUS_MAINNET", "0")
    monkeypatch.setenv("ZEUS_VST_DISABLE_PERFORMANCE_HALT", "1")
    monkeypatch.setenv("ZEUS_VST_ENDPOINT_CONFIRMED", "1")
    assert should_disable_vst_performance_halt()

    monkeypatch.setenv("ZEUS_VST_ENDPOINT_CONFIRMED", "0")
    assert not should_disable_vst_performance_halt()

    monkeypatch.setenv("ZEUS_VST_ENDPOINT_CONFIRMED", "1")
    monkeypatch.setenv("ZEUS_MAINNET", "1")
    assert not should_disable_vst_performance_halt()



def test_vst_override_ignores_persisted_performance_halt(monkeypatch, tmp_path: Path) -> None:
    from astra_bot.decision.live_risk import load_kill_halted

    monkeypatch.setenv("ZEUS_VST", "1")
    monkeypatch.setenv("ZEUS_MAINNET", "0")
    monkeypatch.setenv("ZEUS_VST_DISABLE_PERFORMANCE_HALT", "1")
    monkeypatch.setenv("ZEUS_VST_ENDPOINT_CONFIRMED", "1")
    state = tmp_path / "kill.json"
    state.write_text('{"halted": true, "reason": "PF=0.9"}', encoding="utf-8")
    assert load_kill_halted(state) is None



def test_vst_override_skips_day_week_and_pf_performance_checks(
    monkeypatch, tmp_path: Path
) -> None:
    from astra_bot.decision.live_risk import check_pf_kill

    monkeypatch.setenv("ZEUS_VST", "1")
    monkeypatch.setenv("ZEUS_MAINNET", "0")
    monkeypatch.setenv("ZEUS_VST_DISABLE_PERFORMANCE_HALT", "1")
    monkeypatch.setenv("ZEUS_VST_ENDPOINT_CONFIRMED", "1")
    p = tmp_path / "trades.jsonl"
    now = time.time()
    _write(
        p,
        [
            {"event": "exit", "trade_id": "loss-1", "pnl": -10.0, "ts": now - 50},
            {"event": "exit", "trade_id": "loss-2", "pnl": -10.0, "ts": now - 40},
            {"event": "exit", "trade_id": "loss-3", "pnl": -10.0, "ts": now - 30},
            {"event": "exit", "trade_id": "loss-4", "pnl": -10.0, "ts": now - 20},
            {"event": "exit", "trade_id": "loss-5", "pnl": -10.0, "ts": now - 10},
        ],
    )
    state = tmp_path / "kill.json"
    assert check_day_week_limits(Decimal("100"), path=p) is None
    assert check_pf_kill(path=p, state_path=state) is None
    assert not state.exists()
