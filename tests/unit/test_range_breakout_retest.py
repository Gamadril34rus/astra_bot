from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.range_breakout_retest import (
    RangeBreakoutRetestConfig,
    RangeBreakoutRetestStrategy,
)


def make_candle(open_p, high_p, low_p, close_p, volume=100.0, timestamp_ms=0):
    return Candle(
        exchange="bingx",
        symbol="BTC-USDT",
        timeframe="1h",
        open_time=timestamp_ms,
        open=Decimal(str(open_p)),
        high=Decimal(str(high_p)),
        low=Decimal(str(low_p)),
        close=Decimal(str(close_p)),
        volume=Decimal(str(volume)),
        quote_volume=Decimal(str(volume * close_p)),
    )


@pytest.mark.asyncio
async def test_range_breakout_retest_long():
    strategy = RangeBreakoutRetestStrategy(RangeBreakoutRetestConfig(range_min_bars=15, adx_threshold=30.0))
    candles = []
    base_ts = 1000000

    for i in range(40):
        candles.append(make_candle(100.5, 102.0, 100.0, 101.0, timestamp_ms=base_ts + i * 3600000))

    candles.append(make_candle(101.0, 104.5, 101.0, 104.0, timestamp_ms=base_ts + 40 * 3600000))

    candles.append(make_candle(104.0, 104.5, 102.0, 103.5, timestamp_ms=base_ts + 41 * 3600000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=103.5)
    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert float(signal.entry_price) == 103.5
