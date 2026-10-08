"""Zeus weekly confidence-tier audit tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from astra_bot.decision.zeus_tier_audit import (
    TierStats,
    aggregate_tiers,
    money_r,
    run_audit,
    status_line,
    verdict_from_tiers,
)


def test_tier_audit_money_r():
    """R = pnl / risk_budget; journal r_multiple is ignored."""
    assert money_r(60.0, 20.0) == 3.0
    assert money_r(-20.0, 20.0) == -1.0
    assert money_r(10.0, 0) is None

    trades = [
        # r_multiple lies at +99; money R is -1.0
        {"confidence": 0.90, "pnl": -20.0, "risk_budget": 20.0, "r_multiple": 99.0},
        {"confidence": 0.90, "pnl": 40.0, "risk_budget": 20.0, "r_multiple": -5.0},
        {"confidence": 0.50, "pnl": 10.0, "risk_budget": 20.0, "r_multiple": 0.0},
    ]
    buckets = aggregate_tiers(trades)
    top = buckets["top_ge_0.88"]
    assert top.n == 2
    assert abs(top.sum_r - (-1.0 + 2.0)) < 1e-9  # -1 + 2 = 1
    assert abs((top.exp or 0) - 0.5) < 1e-9
    base = buckets["base_lt_0.70"]
    assert base.n == 1
    assert abs((base.exp or 0) - 0.5) < 1e-9


def test_tier_audit_verdicts():
    # Build synthetic: top worse than base, n_top >= 30
    top_bad = TierStats(n=30, sum_r=-15.0)  # exp = -0.5
    base_ok = TierStats(n=10, sum_r=5.0)  # exp = 0.5
    buckets = {
        "top_ge_0.88": top_bad,
        "mid_0.80_0.88": TierStats(),
        "low_0.70_0.80": TierStats(),
        "base_lt_0.70": base_ok,
    }
    v, warn = verdict_from_tiers(buckets)
    assert v == "top_tier_below_base"
    assert warn is True

    top_good = TierStats(n=30, sum_r=30.0)  # exp = 1.0
    buckets["top_ge_0.88"] = top_good
    v, warn = verdict_from_tiers(buckets)
    assert v == "top_tier_ok"
    assert warn is False

    top_few = TierStats(n=10, sum_r=-5.0)
    buckets["top_ge_0.88"] = top_few
    v, warn = verdict_from_tiers(buckets)
    assert v == "insufficient_data"
    assert warn is False


def test_tier_audit_persist(tmp_path: Path):
    trades_path = tmp_path / "trades.jsonl"
    audit_path = tmp_path / "audit.json"
    rows = [
        {"confidence": 0.90, "pnl": 30.0, "risk_budget": 30.0},
        {"confidence": 0.50, "pnl": 10.0, "risk_budget": 20.0},
    ]
    with trades_path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    result = run_audit(trades_path=trades_path, audit_path=audit_path, now=now, force=True)
    assert audit_path.exists()
    assert result["verdict"] == "insufficient_data"
    next_run = datetime.fromisoformat(result["next_run"])
    assert next_run == now + timedelta(days=7)

    loaded = json.loads(audit_path.read_text())
    assert loaded["audited_at"] == result["audited_at"]
    assert loaded["next_run"] == result["next_run"]

    line = status_line(result)
    assert "санитар:" in line
    assert "мало данных" in line
