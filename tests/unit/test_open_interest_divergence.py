from decimal import Decimal
from unittest.mock import patch

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.open_interest_divergence import (
    OpenInterestDivergenceConfig,
    OpenInterestDivergenceStrategy,
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
async def test_open_interest_divergence_short():
    strategy = OpenInterestDivergenceStrategy(
        OpenInterestDivergenceConfig(oi_window=5, price_change_min=0.01)
    )
    candles = []
    base_ts = 1000000

    for i in range(15):
        p = 100.0 + i * 0.35
        candles.append(make_candle(p - 0.2, p + 0.5, p - 0.2, p, timestamp_ms=base_ts + i * 3600000))

    with patch("astra_bot.strategies.open_interest_divergence._get_open_interest_val", return_value=9000.0):
        with patch("astra_bot.strategies.open_interest_divergence._oi_cache", {"BTC-USDT": [(0, 10000.0), (10, 9000.0)]}):
            signal = await strategy.evaluate("BTC-USDT", candles, current_price=105.0)
            assert signal is not None
            assert signal.direction == TradeDirection.SHORT
            assert float(signal.entry_price) == 105.0
