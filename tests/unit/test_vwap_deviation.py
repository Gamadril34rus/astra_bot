from datetime import datetime, timezone
from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.vwap_deviation import VWAPDeviationConfig, VWAPDeviationStrategy


def make_candle(open_p, high_p, low_p, close_p, volume=100.0, timestamp_ms=0):
    return Candle(
        exchange="bingx",
        symbol="BTC-USDT",
        timeframe="15m",
        open_time=timestamp_ms,
        open=Decimal(str(open_p)),
        high=Decimal(str(high_p)),
        low=Decimal(str(low_p)),
        close=Decimal(str(close_p)),
        volume=Decimal(str(volume)),
        quote_volume=Decimal(str(volume * close_p)),
    )


@pytest.mark.asyncio
async def test_vwap_deviation_long():
    strategy = VWAPDeviationStrategy(VWAPDeviationConfig(sigma_entry=1.0, min_rr=0.1))
    candles = []
    # Использовать сегодняшнее время UTC для прохождения фильтра сессии 00:00
    now = datetime.now(timezone.utc)
    today_00 = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc)
    base_ts = int(today_00.timestamp() * 1000)

    for i in range(10):
        candles.append(make_candle(100.0, 100.5, 99.5, 100.0, volume=100.0, timestamp_ms=base_ts + i * 900000))

    candles.append(make_candle(97.0, 98.2, 92.0, 98.0, volume=200.0, timestamp_ms=base_ts + 10 * 900000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=98.0)
    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert float(signal.entry_price) == 98.0
