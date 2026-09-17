"""Fixture tests for Zeus wedge false-break + retest (enabled=false by default)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from astra_bot.core import models
from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)


def _c(i: int, o: float, h: float, l: float, cl: float) -> models.Candle:
    return models.Candle(
        exchange="bingx",
        symbol="BTC-USDT",
        timeframe="4h",
        open_time=1_700_000_000_000 + i * 4 * 3600 * 1000,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(cl)),
        volume=Decimal("1000"),
        quote_volume=Decimal("100000"),
    )


def _rising_wedge_then_false_break() -> list[models.Candle]:
    """Сходящийся rising wedge, ложный пробой вверх, возврат внутрь → SHORT."""
    candles = []
    base = 100.0
    for i in range(24):
        lo = base + i * 0.15
        hi = base + 6.0 - i * 0.05
        mid = (lo + hi) / 2
        candles.append(_c(i, mid, hi, lo, mid + 0.05))
    last_hi = float(candles[-1].high)
    candles.append(_c(24, last_hi + 0.2, last_hi + 1.5, last_hi + 0.1, last_hi + 1.2))
    candles.append(_c(25, last_hi + 1.0, last_hi + 1.8, last_hi + 0.5, last_hi + 0.9))
    upper_approx = float(candles[23].high)
    candles.append(
        _c(26, upper_approx + 0.2, upper_approx + 0.4, upper_approx - 0.8, upper_approx - 0.3)
    )
    return candles


@pytest.mark.asyncio
async def test_disabled_returns_none():
    strat = ZeusWedgeRetestStrategy()
    assert strat.config.enabled is False
    res = await strat.evaluate("BTC-USDT", _rising_wedge_then_false_break())
    assert res is None


@pytest.mark.asyncio
async def test_false_upside_break_short_when_enabled():
    cfg = ZeusWedgeRetestConfig(enabled=True)
    strat = ZeusWedgeRetestStrategy(cfg)
    res = await strat.evaluate("BTC-USDT", _rising_wedge_then_false_break())
    assert res is not None
    assert res.direction == models.TradeDirection.SHORT
    assert res.strategy_name == "zeus_wedge_retest_4h"
    assert float(res.stop_loss) > float(res.entry_price)
    assert float(res.take_profit) < float(res.entry_price)


@pytest.mark.asyncio
async def test_too_few_bars_none():
    cfg = ZeusWedgeRetestConfig(enabled=True)
    strat = ZeusWedgeRetestStrategy(cfg)
    res = await strat.evaluate("BTC-USDT", _rising_wedge_then_false_break()[:5])
    assert res is None
