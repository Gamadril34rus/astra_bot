from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.volume_delta import VolumeDeltaConfig, VolumeDeltaStrategy


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
async def test_volume_delta_bearish_divergence():
    strategy = VolumeDeltaStrategy(VolumeDeltaConfig(delta_window=5, divergence_threshold=0.2))
    candles = []
    base_ts = 1000000

    for i in range(5):
        candles.append(make_candle(100.0, 101.0, 100.0, 100.9, volume=100.0, timestamp_ms=base_ts + i * 3600000))

    for i in range(5, 10):
        candles.append(make_candle(101.0, 105.0, 101.0, 101.1, volume=100.0, timestamp_ms=base_ts + i * 3600000))

    candles.append(make_candle(101.1, 105.0, 101.0, 104.5, volume=100.0, timestamp_ms=base_ts + 10 * 3600000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=104.5)
    assert signal is not None
    assert signal.direction == TradeDirection.SHORT
    assert float(signal.entry_price) == 104.5
