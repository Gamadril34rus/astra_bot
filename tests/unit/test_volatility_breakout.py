from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.volatility_breakout import (
    VolatilityBreakoutConfig,
    VolatilityBreakoutStrategy,
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
async def test_volatility_breakout_long():
    strategy = VolatilityBreakoutStrategy(
        VolatilityBreakoutConfig(squeeze_ratio=0.8, squeeze_min_bars=10, volume_mult=1.2, roc_period=14, min_rr=1.5)
    )
    candles = []
    base_ts = 1000000

    for i in range(40):
        candles.append(make_candle(100, 105, 95, 100, volume=100.0, timestamp_ms=base_ts + i * 3600000))

    for i in range(40, 55):
        candles.append(make_candle(100.0, 100.1, 99.9, 100.0, volume=50.0, timestamp_ms=base_ts + i * 3600000))

    candles.append(make_candle(100.0, 103.5, 100.0, 103.2, volume=200.0, timestamp_ms=base_ts + 55 * 3600000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=103.2)
    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert float(signal.entry_price) == 103.2
