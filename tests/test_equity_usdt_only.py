"""A-014: equity for limits only from USDT/VST balance rows."""
from __future__ import annotations

from decimal import Decimal

from astra_bot.adapters.bingx.client_live import equity_from_balance_rows


def test_usdt_row_used() -> None:
    eq, foreign = equity_from_balance_rows(
        [
            {"asset": "BTC", "equity": "99999"},
            {"asset": "USDT", "equity": "100.5"},
        ]
    )
    assert eq == Decimal("100.5")
    assert "BTC" in foreign


def test_foreign_only_equity_zero() -> None:
    eq, foreign = equity_from_balance_rows(
        [
            {"asset": "BTC", "equity": "1.5"},
            {"asset": "ETH", "balance": "20"},
        ]
    )
    assert eq == Decimal("0")
    assert set(foreign) >= {"BTC", "ETH"}


def test_foreign_does_not_inflate_day_limit() -> None:
    """With inflated fake equity, 4% day limit would never trip; USDT-only does."""
    import json
    import tempfile
    import time
    from pathlib import Path

    from astra_bot.decision.live_risk import check_day_week_limits

    # day loss -5 on equity 100 → 5% > 4% halt; if equity wrongly 10000 → no halt
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "trades.jsonl"
        now = time.time()
        p.write_text(
            json.dumps({"event": "exit", "pnl": -5.0, "ts": now - 10}) + "\n",
            encoding="utf-8",
        )
        foreign_eq, _ = equity_from_balance_rows([{"asset": "BTC", "equity": "10000"}])
        usdt_eq, _ = equity_from_balance_rows(
            [{"asset": "BTC", "equity": "10000"}, {"asset": "USDT", "equity": "100"}]
        )
        assert foreign_eq == Decimal("0")
        assert usdt_eq == Decimal("100")
        # foreign equity 0 → check returns None (no division halt when equity<=0)
        assert check_day_week_limits(foreign_eq, path=p) is None
        reason = check_day_week_limits(usdt_eq, path=p)
        assert reason is not None
        assert "day_loss" in reason


def test_vst_accepted_as_settlement() -> None:
    eq, foreign = equity_from_balance_rows([{"asset": "VST", "equity": "50"}])
    assert eq == Decimal("50")
    assert foreign == []
