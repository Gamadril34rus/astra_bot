from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.order_block import OrderBlockConfig, OrderBlockStrategy


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
async def test_order_block_bullish():
    strategy = OrderBlockStrategy(OrderBlockConfig(impulse_bars=3, impulse_min_pct=0.015))
    candles = []
    base_ts = 1000000

    for i in range(50):
        candles.append(make_candle(100, 102, 99, 101, timestamp_ms=base_ts + i * 3600000))

    candles.append(make_candle(101, 101.5, 99, 99.5, timestamp_ms=base_ts + 50 * 3600000))

    candles.append(make_candle(99.5, 101, 99.5, 101, timestamp_ms=base_ts + 51 * 3600000))
    candles.append(make_candle(101, 103, 101, 103, timestamp_ms=base_ts + 52 * 3600000))
    candles.append(make_candle(103, 105, 103, 105, timestamp_ms=base_ts + 53 * 3600000))

    candles.append(make_candle(104, 104, 100.5, 102, timestamp_ms=base_ts + 54 * 3600000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=102.0)
    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert float(signal.entry_price) == 102.0
