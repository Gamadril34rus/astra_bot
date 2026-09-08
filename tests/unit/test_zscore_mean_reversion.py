from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.zscore_mean_reversion import (
    ZScoreMeanReversionConfig,
    ZScoreMeanReversionStrategy,
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
async def test_zscore_mean_reversion_long():
    strategy = ZScoreMeanReversionStrategy(ZScoreMeanReversionConfig(window=50, entry_z=2.0, adx_threshold=30.0))
    candles = []
    base_ts = 1000000

    for i in range(65):
        candles.append(make_candle(100.0, 101.0, 99.0, 100.0, timestamp_ms=base_ts + i * 3600000))

    candles.append(make_candle(98.0, 98.0, 89.0, 90.0, timestamp_ms=base_ts + 65 * 3600000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=90.0)
    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert float(signal.entry_price) == 90.0
