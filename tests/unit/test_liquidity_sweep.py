from decimal import Decimal

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.liquidity_sweep import LiquiditySweepConfig, LiquiditySweepStrategy


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
async def test_liquidity_sweep_bullish():
    strategy = LiquiditySweepStrategy(LiquiditySweepConfig(swing_lookback=20, volume_multiplier=1.5, min_rr=1.2))
    candles = []
    base_ts = 1000000

    for i in range(30):
        candles.append(make_candle(102, 110, 101, 105, volume=100.0, timestamp_ms=base_ts + i * 3600000))

    # Свеча свипа: c_low = 99 (< swing_low 101), close = 102 (> swing_low 101), volume = 250
    candles.append(make_candle(101, 103, 99, 102, volume=250.0, timestamp_ms=base_ts + 30 * 3600000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=102.0)
    assert signal is not None
    assert signal.direction == TradeDirection.LONG
    assert float(signal.entry_price) == 102.0
    assert float(signal.stop_loss) < 99.0


@pytest.mark.asyncio
async def test_liquidity_sweep_bearish():
    strategy = LiquiditySweepStrategy(LiquiditySweepConfig(swing_lookback=20, volume_multiplier=1.5, min_rr=1.2))
    candles = []
    base_ts = 1000000

    for i in range(30):
        candles.append(make_candle(102, 108, 100, 105, volume=100.0, timestamp_ms=base_ts + i * 3600000))

    # Свеча свипа: c_high = 109 (> swing_high 108), close = 107 (< swing_high 108), volume = 200
    candles.append(make_candle(108, 109, 104, 107, volume=200.0, timestamp_ms=base_ts + 30 * 3600000))

    signal = await strategy.evaluate("BTC-USDT", candles, current_price=107.0)
    assert signal is not None
    assert signal.direction == TradeDirection.SHORT
    assert float(signal.entry_price) == 107.0
    assert float(signal.stop_loss) > 109.0
