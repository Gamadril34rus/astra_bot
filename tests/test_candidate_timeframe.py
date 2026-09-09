"""Блок K: в статистику пишется фактический ТФ оценки, а не "1h"."""

from __future__ import annotations

from decimal import Decimal

from astra_bot.core import models
from astra_bot.decision.context import MarketContext
from astra_bot.decision.pipeline import DecisionPipeline
from astra_bot.strategies.base import Signal, SignalType


class _Dummy:
    name = "dummy"
    preferred_timeframe = None

    async def evaluate(self, symbol, candles, orderbook=None, current_price=None,
                       market_regime=None):
        return Signal(
            symbol=symbol, strategy_name="dummy", signal_type=SignalType.MOMENTUM,
            direction=models.TradeDirection.LONG, entry_price=Decimal("100"),
            stop_loss=Decimal("99"), take_profit=Decimal("102"), confidence=0.7,
        )


def _candle(tf: str):
    return models.Candle(
        exchange="t", symbol="BTC-USDT", timeframe=tf, open_time=1,
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
        close=Decimal("100"), volume=Decimal("1"), quote_volume=Decimal("1"),
    )


async def test_records_actual_timeframe():
    pipe = DecisionPipeline(strategies=[_Dummy()])
    ctx = MarketContext(
        symbol="BTC-USDT", current_price=Decimal("100"),
        candles={"5m": [_candle("5m")]},
    )
    cands = await pipe._candidates_from_strategies(ctx, "RANGE")
    assert len(cands) == 1
    assert cands[0].timeframe == "5m"
