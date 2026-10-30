"""Early TP1: exchange leg at 0.6R so live MFE band can fill partial."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astra_bot.decision.live_arm import plan_from_candidate


def test_early_tp1_formula_matches_spec():
    """0.6R from entry/stop — the level the exchange TP leg must use."""
    entry, stop = Decimal("2000"), Decimal("2020")
    risk = abs(entry - stop)
    tp1_rr = Decimal("0.6")
    tp_short = entry - risk * tp1_rr
    tp_long = entry + risk * tp1_rr
    assert tp_short == Decimal("1988.0")
    assert abs(float(entry + abs(entry - Decimal("1990")) * tp1_rr) )  # sanity
    entry_l, stop_l = Decimal("100"), Decimal("99")
    assert entry_l + abs(entry_l - stop_l) * tp1_rr == Decimal("100.6")


def _cand(side: str, entry: str, stop: str, tp: str):
    c = MagicMock()
    c.symbol = "ETH-USDT"
    c.side = side
    c.entry = Decimal(entry)
    c.stop = Decimal(stop)
    c.take_profit = Decimal(tp)
    c.confidence = 0.9
    c.strategy = "zeus_channel_boundary_4h"
    c.step_size = Decimal("0.01")
    c.signal_id = "test-sig"
    c.prior_r = 2.0
    return c


@pytest.mark.asyncio
async def test_plan_from_candidate_early_tp1_short():
    broker = MagicMock()
    broker.config.max_leverage = 10
    broker.risk_budget = AsyncMock(return_value=Decimal("1000"))
    cand = _cand("short", "2000", "2020", "1940")
    with patch("astra_bot.decision.live_arm.cooldown_blocks", return_value=False), patch(
        "astra_bot.decision.live_arm.count_open", return_value=0
    ), patch("astra_bot.decision.live_arm.size_qty", return_value=Decimal("1")):
        plan = await plan_from_candidate(
            broker, cand, exchange_symbols=set(), exchange_open_count=0
        )
    assert plan is not None
    assert abs(float(plan.take_profit_price) - 1988.0) < 0.05
