from decimal import Decimal
from unittest.mock import patch

import pytest
from astra_bot.core.models import Candle, TradeDirection
from astra_bot.strategies.funding_rate_contrarian import (
    FundingRateContrarianConfig,
    FundingRateContrarianStrategy,
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
async def test_funding_rate_contrarian_short():
    strategy = FundingRateContrarianStrategy(
        FundingRateContrarianConfig(funding_long_extreme=0.0015, rsi_overbought=60.0)
    )
    candles = []
    base_ts = 1000000

    for i in range(30):
        p = 100.0 + i * 1.0
        candles.append(make_candle(p - 0.5, p + 1.0, p - 0.5, p, timestamp_ms=base_ts + i * 3600000))

    with patch("astra_bot.strategies.funding_rate_contrarian._get_funding_rate", return_value=0.002):
        signal = await strategy.evaluate("BTC-USDT", candles, current_price=129.0)
        assert signal is not None
        assert signal.direction == TradeDirection.SHORT
        assert float(signal.entry_price) == 129.0
