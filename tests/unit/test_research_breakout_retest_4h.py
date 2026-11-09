from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.research_breakout_retest_4h import (
    ResearchBreakoutRetest4HConfig,
    ResearchBreakoutRetest4HStrategy,
)


def candle(index, open_p, high_p, low_p, close_p, volume=100.0, timeframe="4h"):
    return Candle(
        exchange="bingx",
        symbol="BTC-USDT",
        timeframe=timeframe,
        open_time=index * 4 * 60 * 60 * 1000,
        open=Decimal(str(open_p)),
        high=Decimal(str(high_p)),
        low=Decimal(str(low_p)),
        close=Decimal(str(close_p)),
        volume=Decimal(str(volume)),
        quote_volume=Decimal(str(volume * close_p)),
    )


def rising_history():
    rows = []
    for index in range(200):
        close = 80.0 + index * 0.1
        rows.append(
            candle(index, close - 0.05, close + 0.05, close - 0.15, close)
        )
    rows[-20] = candle(180, 99.75, 100.0, 99.5, 99.8)
    rows[-1] = candle(199, 99.8, 100.0, 99.5, 99.95)
    return rows


def falling_history():
    rows = []
    for index in range(200):
        close = 120.0 - index * 0.1
        rows.append(
            candle(index, close + 0.05, close + 0.15, close - 0.05, close)
        )
    rows[-20] = candle(180, 100.25, 100.5, 100.0, 100.2)
    rows[-1] = candle(199, 100.2, 100.5, 100.0, 100.05)
    return rows


@pytest.mark.asyncio
async def test_candidate_is_disabled_by_default():
    strategy = ResearchBreakoutRetest4HStrategy()
    candles = rising_history()
    candles.append(candle(200, 100.0, 101.0, 99.8, 100.5, volume=140.0))

    assert await strategy.evaluate("BTC-USDT", candles, current_price=100.5) is None


@pytest.mark.asyncio
async def test_long_breakout_retest_matches_research_entry_conditions():
    strategy = ResearchBreakoutRetest4HStrategy(
        ResearchBreakoutRetest4HConfig(enabled=True)
    )
    candles = rising_history()
    candles.append(candle(200, 100.0, 101.0, 99.8, 100.5, volume=140.0))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=100.5)

    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert signal.entry_price == Decimal("100.5")
    assert signal.stop_loss < signal.entry_price < signal.take_profit
    assert signal.features["volume_ratio"] >= 1.3


@pytest.mark.asyncio
async def test_short_breakout_retest_matches_research_entry_conditions():
    strategy = ResearchBreakoutRetest4HStrategy(
        ResearchBreakoutRetest4HConfig(enabled=True)
    )
    candles = falling_history()
    candles.append(candle(200, 100.0, 100.2, 99.0, 99.5, volume=140.0))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=99.5)

    assert signal is not None
    assert signal.direction == TradeDirection.SHORT
    assert signal.take_profit < signal.entry_price < signal.stop_loss
    assert signal.features["volume_ratio"] >= 1.3


@pytest.mark.asyncio
async def test_candidate_rejects_weak_volume_and_wrong_timeframe():
    weak_volume = ResearchBreakoutRetest4HStrategy(
        ResearchBreakoutRetest4HConfig(enabled=True)
    )
    candles = rising_history()
    candles.append(candle(200, 100.0, 101.0, 99.8, 100.5, volume=100.0))

    assert await weak_volume.evaluate("BTC-USDT", candles, current_price=100.5) is None

    wrong_timeframe = ResearchBreakoutRetest4HStrategy(
        ResearchBreakoutRetest4HConfig(enabled=True)
    )
    candles[-1] = candle(
        200, 100.0, 101.0, 99.8, 100.5, volume=140.0, timeframe="1h"
    )
    assert (
        await wrong_timeframe.evaluate("BTC-USDT", candles, current_price=100.5)
        is None
    )


@pytest.mark.asyncio
async def test_candidate_rejects_non_monotonic_candle_timestamps():
    strategy = ResearchBreakoutRetest4HStrategy(
        ResearchBreakoutRetest4HConfig(enabled=True)
    )
    candles = rising_history()
    candles.append(candle(199, 100.0, 101.0, 99.8, 100.5, volume=140.0))

    assert await strategy.evaluate("BTC-USDT", candles, current_price=100.5) is None


def test_long_stop_and_take_geometry_is_1_5_atr_and_1_5r():
    strategy = ResearchBreakoutRetest4HStrategy(
        ResearchBreakoutRetest4HConfig(enabled=True)
    )
    strategy._last_direction = TradeDirection.LONG
    entry = Decimal("100")
    stop = strategy.calculate_stop_loss(entry, [], atr=2.0)
    take = strategy.calculate_take_profit(entry, stop, [])

    assert stop == Decimal("97.00")
    assert take[0]["price"] == Decimal("104.500")
    assert take[0]["fraction"] == 1.0
