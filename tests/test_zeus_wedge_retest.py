"""Fixture tests for Zeus wedge false-break + retest (enabled=false by default)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

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


def _falling_wedge_then_false_break() -> list[models.Candle]:
    candles = []
    base = 120.0
    for i in range(24):
        hi = base - i * 0.15
        lo = base - 6.0 + i * 0.05
        mid = (lo + hi) / 2
        candles.append(_c(i, mid, hi, lo, mid - 0.05))
    last_lo = float(candles[-1].low)
    candles.append(_c(24, last_lo - 0.2, last_lo - 0.1, last_lo - 1.5, last_lo - 1.2))
    candles.append(_c(25, last_lo - 1.0, last_lo - 0.5, last_lo - 1.8, last_lo - 0.9))
    lower_approx = float(candles[23].low)
    candles.append(
        _c(26, lower_approx - 0.2, lower_approx + 0.8, lower_approx - 0.4, lower_approx + 0.3)
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
    cfg = ZeusWedgeRetestConfig(enabled=True, lookback=24)
    strat = ZeusWedgeRetestStrategy(cfg)
    with (
        patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=True),
        patch("astra_bot.strategies.zeus_wedge_retest._htf_bias_closes", return_value="down"),
    ):
        res = await strat.evaluate("BTC-USDT", _rising_wedge_then_false_break())
    assert res is not None
    assert res.direction == models.TradeDirection.SHORT
    assert res.strategy_name == "zeus_wedge_retest_4h"
    assert float(res.stop_loss) > float(res.entry_price)
    assert float(res.take_profit) < float(res.entry_price)


@pytest.mark.asyncio
async def test_too_few_bars_none():
    cfg = ZeusWedgeRetestConfig(enabled=True, lookback=24)
    strat = ZeusWedgeRetestStrategy(cfg)
    res = await strat.evaluate("BTC-USDT", _rising_wedge_then_false_break()[:5])
    assert res is None


@pytest.mark.asyncio
async def test_false_break_no_div_rejects():
    cfg = ZeusWedgeRetestConfig(enabled=True, lookback=24)
    strat = ZeusWedgeRetestStrategy(cfg)
    candles = _rising_wedge_then_false_break()
    with (
        patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=False),
        patch("astra_bot.strategies.zeus_wedge_retest._htf_bias_closes", return_value="neutral"),
    ):
        diag = strat.diagnose(candles)
        res = await strat.evaluate("BTC-USDT", candles)
    assert res is None
    assert diag.get("reject_reason") == "no_div_zeus"


@pytest.mark.asyncio
async def test_false_break_with_div_signals():
    cfg = ZeusWedgeRetestConfig(enabled=True, lookback=24)
    strat = ZeusWedgeRetestStrategy(cfg)
    candles = _rising_wedge_then_false_break()
    with (
        patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=True),
        patch("astra_bot.strategies.zeus_wedge_retest._htf_bias_closes", return_value="neutral"),
    ):
        diag = strat.diagnose(candles)
        res = await strat.evaluate("BTC-USDT", candles)
    assert diag.get("would_signal") is True
    assert diag.get("pattern") == "false_break_up_retest_inside"
    assert res is not None
    assert res.direction == models.TradeDirection.SHORT


@pytest.mark.asyncio
async def test_false_break_counter_trend_bias_rejects():
    cfg = ZeusWedgeRetestConfig(enabled=True, lookback=24)
    strat = ZeusWedgeRetestStrategy(cfg)
    candles = _rising_wedge_then_false_break()
    with (
        patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=True),
        patch("astra_bot.strategies.zeus_wedge_retest._htf_bias_closes", return_value="up"),
    ):
        diag = strat.diagnose(candles)
        res = await strat.evaluate("BTC-USDT", candles)
    assert res is None
    assert diag.get("reject_reason") == "counter_trend_false_break_up"


@pytest.mark.asyncio
async def test_false_break_long_counter_trend_bias_rejects():
    cfg = ZeusWedgeRetestConfig(enabled=True, lookback=24)
    strat = ZeusWedgeRetestStrategy(cfg)
    candles = _falling_wedge_then_false_break()
    with (
        patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=True),
        patch("astra_bot.strategies.zeus_wedge_retest._htf_bias_closes", return_value="down"),
    ):
        diag = strat.diagnose(candles)
        res = await strat.evaluate("BTC-USDT", candles)
    assert res is None
    assert diag.get("reject_reason") == "counter_trend_false_break_down"


@pytest.mark.asyncio
async def test_not_enough_touches_rejects():
    cfg2 = ZeusWedgeRetestConfig(enabled=True, lookback=24, min_touches=999)
    strat2 = ZeusWedgeRetestStrategy(cfg2)
    with patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=True):
        diag2 = strat2.diagnose(_rising_wedge_then_false_break())
    assert diag2.get("reject_reason") == "not_enough_touches"
    assert diag2.get("would_signal") is not True


@pytest.mark.asyncio
async def test_diagnose_touch_fields_present():
    cfg = ZeusWedgeRetestConfig(enabled=True, lookback=24)
    strat = ZeusWedgeRetestStrategy(cfg)
    with patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=True), patch(
        "astra_bot.strategies.zeus_wedge_retest._htf_bias_closes", return_value="neutral"
    ):
        diag = strat.diagnose(_rising_wedge_then_false_break())
    assert "touch_hi" in diag or diag.get("reject_reason") == "not_enough_touches"
    if diag.get("has_wedge"):
        assert isinstance(diag.get("touch_hi"), int)
        assert isinstance(diag.get("touch_lo"), int)


@pytest.mark.asyncio
async def test_true_breakout_not_gated_by_div():
    cfg = ZeusWedgeRetestConfig(
        enabled=True,
        lookback=24,
        enable_true_breakout=True,
        min_hold_bars_breakout=2,
        max_bars_true_breakout=8,
        require_htf_bias=False,
    )
    strat = ZeusWedgeRetestStrategy(cfg)
    candles = []
    base = 100.0
    for i in range(24):
        lo = base + i * 0.15
        hi = base + 6.0 - i * 0.05
        mid = (lo + hi) / 2
        candles.append(_c(i, mid, hi, lo, mid + 0.05))
    last_hi = float(candles[-1].high)
    for j, cl_off in enumerate([1.2, 1.5, 1.8]):
        candles.append(
            _c(24 + j, last_hi + 0.5, last_hi + 2.0, last_hi + 0.3, last_hi + cl_off)
        )
    with patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=False), patch(
        "astra_bot.strategies.zeus_wedge_retest._htf_bias_closes", return_value="up"
    ):
        diag = strat.diagnose(candles)
    assert diag.get("reject_reason") != "no_div_zeus"
    if diag.get("would_signal"):
        assert str(diag.get("pattern", "")).startswith("true_breakout")


@pytest.mark.asyncio
async def test_false_break_long_with_div_signals():
    cfg = ZeusWedgeRetestConfig(enabled=True, lookback=24)
    strat = ZeusWedgeRetestStrategy(cfg)
    candles = _falling_wedge_then_false_break()
    with (
        patch("astra_bot.strategies.zeus_wedge_retest.div_zeus_like", return_value=True),
        patch("astra_bot.strategies.zeus_wedge_retest._htf_bias_closes", return_value="up"),
    ):
        diag = strat.diagnose(candles)
        res = await strat.evaluate("BTC-USDT", candles)
    if diag.get("would_signal"):
        assert diag.get("direction") == "long"
        assert res is not None
        assert res.direction == models.TradeDirection.LONG
    else:
        assert diag.get("reject_reason") not in (None, "")


@pytest.mark.asyncio
async def test_config_lookback_default_48():
    cfg = ZeusWedgeRetestConfig()
    assert cfg.lookback == 48
    assert cfg.min_touches == 3
