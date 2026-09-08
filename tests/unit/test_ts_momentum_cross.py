from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.ts_momentum_cross import TSMomentumCrossConfig, TSMomentumCrossStrategy


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
async def test_ts_momentum_cross_long():
    strategy = TSMomentumCrossStrategy(TSMomentumCrossConfig(fast=5, slow=10, adx_min=10.0, roc_period=5))
    candles = []
    base_ts = 1000000

    for i in range(30):
        p = 100.0 - i * 0.5
        candles.append(make_candle(p, p + 0.2, p - 0.2, p, timestamp_ms=base_ts + i * 3600000))

    # 2 резких бара импульса вверх, форсирующих пересечение ЕМА5 и ЕМА10
    candles.append(make_candle(85, 92, 85, 92, timestamp_ms=base_ts + 30 * 3600000))
    candles.append(make_candle(92, 100, 92, 100, timestamp_ms=base_ts + 31 * 3600000))

    curr_p = float(candles[-1].close)
    signal = await strategy.evaluate("BTC-USDT", candles, current_price=curr_p)
    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert float(signal.entry_price) == curr_p
