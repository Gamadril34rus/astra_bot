from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.fair_value_gap import FairValueGapConfig, FairValueGapStrategy


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
async def test_fair_value_gap_bullish():
    strategy = FairValueGapStrategy(FairValueGapConfig(min_gap_pct=0.002, ema_period=10, min_rr=1.5))
    candles = []
    base_ts = 1000000

    for i in range(20):
        candles.append(make_candle(100, 102, 99, 101, timestamp_ms=base_ts + i * 3600000))

    candles.append(make_candle(101, 102, 100, 101.5, timestamp_ms=base_ts + 20 * 3600000))
    candles.append(make_candle(102, 108, 101.8, 107.5, timestamp_ms=base_ts + 21 * 3600000))
    candles.append(make_candle(107, 110, 104, 109, timestamp_ms=base_ts + 22 * 3600000))
    candles.append(make_candle(108, 109, 103.5, 109.0, timestamp_ms=base_ts + 23 * 3600000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=109.0)
    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert float(signal.entry_price) == 109.0
