"""Блок E: стопы/тейки momentum и mean_reversion direction-aware."""

from __future__ import annotations

from decimal import Decimal

from astra_bot.core import models
from astra_bot.strategies.mean_reversion import MeanReversionStrategy
from astra_bot.strategies.momentum import MomentumStrategy


def test_momentum_short_sides():
    s = MomentumStrategy()
    entry = Decimal("100")
    stop = s.calculate_stop_loss(entry, [], atr=2.0, direction=models.TradeDirection.SHORT)
    assert stop > entry  # был ниже входа — перевёрнут
    tps = s.calculate_take_profit(entry, stop, [], direction="short")
    assert all(t["price"] < entry for t in tps)


def test_momentum_long_sides():
    s = MomentumStrategy()
    entry = Decimal("100")
    stop = s.calculate_stop_loss(entry, [], atr=2.0, direction=models.TradeDirection.LONG)
    assert stop < entry
    tps = s.calculate_take_profit(entry, stop, [], direction="long")
    assert all(t["price"] > entry for t in tps)
    # RR уровней: 1R/2R/3R от риска 3.0
    assert float(tps[1]["price"]) == 106.0


def test_momentum_default_is_long_compatible():
    s = MomentumStrategy()
    entry = Decimal("100")
    assert s.calculate_stop_loss(entry, [], atr=2.0) < entry


def _downtrend_candles(n: int = 220):
    out = []
    price = 200.0
    for i in range(n):
        price -= 0.15
        out.append(
            models.Candle(
                exchange="test", symbol="BTC-USDT", timeframe="5m",
                open_time=i, open=Decimal(str(price + 0.1)),
                high=Decimal(str(price + 0.2)), low=Decimal(str(price - 0.2)),
                close=Decimal(str(price)),
                volume=Decimal("20") if i == n - 1 else Decimal("10"),
                quote_volume=Decimal("1"),
            )
        )
    return out


async def test_momentum_short_signal_sides_end_to_end():
    s = MomentumStrategy()
    sig = await s.evaluate("BTC-USDT", _downtrend_candles(), current_price=167.0)
    assert sig is not None
    assert sig.direction == models.TradeDirection.SHORT
    assert sig.stop_loss > sig.entry_price
    assert sig.take_profit < sig.entry_price


def test_mean_reversion_long_stop_below():
    s = MeanReversionStrategy()
    entry = Decimal("100")
    stop = s.calculate_stop_loss(entry, [], direction=models.TradeDirection.LONG)
    assert stop == Decimal("98.00")  # было 102 — мгновенная смерть лонга


def test_mean_reversion_short_stop_above():
    s = MeanReversionStrategy()
    entry = Decimal("100")
    stop = s.calculate_stop_loss(entry, [], direction="short")
    assert stop == Decimal("102.00")
