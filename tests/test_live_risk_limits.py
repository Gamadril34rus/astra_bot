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
