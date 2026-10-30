"""Early TP1: exchange leg at 0.6R so live MFE band can fill partial."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astra_bot.decision.live_arm import plan_from_candidate


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
async def test_early_tp1_short_0_6R():
    broker = MagicMock()
    broker.config.max_leverage = 10
    broker.risk_budget = AsyncMock(return_value=Decimal("1000"))
    cand = _cand("short", "2000", "2020", "1940")  # risk=20 → 0.6R → 1988
    with patch("astra_bot.decision.live_arm.cooldown_blocks", return_value=False), patch(
        "astra_bot.decision.live_arm.conflict_blocks", return_value=False
    ), patch("astra_bot.decision.live_arm.count_open", return_value=0), patch(
        "astra_bot.decision.live_arm.size_qty", return_value=Decimal("1")
    ), patch("astra_bot.decision.live_arm.leverage_for", return_value=6):
        plan = await plan_from_candidate(broker, cand, exchange_symbols=set(), exchange_open_count=0)
    assert plan is not None
    assert abs(float(plan.take_profit_price) - 1988.0) < 0.05
    # not the old clamp-tail (~1960 for 2R)
    assert abs(float(plan.take_profit_price) - 1960.0) > 1.0


@pytest.mark.asyncio
async def test_early_tp1_long_0_6R():
    broker = MagicMock()
    broker.config.max_leverage = 10
    broker.risk_budget = AsyncMock(return_value=Decimal("1000"))
    cand = _cand("long", "100", "99", "103")  # risk=1 → tp=100.6
    with patch("astra_bot.decision.live_arm.cooldown_blocks", return_value=False), patch(
        "astra_bot.decision.live_arm.conflict_blocks", return_value=False
    ), patch("astra_bot.decision.live_arm.count_open", return_value=0), patch(
        "astra_bot.decision.live_arm.size_qty", return_value=Decimal("1")
    ), patch("astra_bot.decision.live_arm.leverage_for", return_value=5):
        plan = await plan_from_candidate(broker, cand, exchange_symbols=set(), exchange_open_count=0)
    assert plan is not None
    assert abs(float(plan.take_profit_price) - 100.6) < 0.05
