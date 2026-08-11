"""Тесты PullbackStrategy."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.strategies import PullbackStrategy


def _bullish_candles(n: int = 400, seed: int = 42) -> list[models.Candle]:
    random.seed(seed)
    out = []
    price = 30000.0
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    for i in range(n):
        drift = 0.0008 if (i // 400) % 3 != 1 else -0.0005
        price = max(1.0, price * (1 + random.gauss(drift, 0.01)))
        op = price * (1 + random.gauss(0, 0.002))
        hi = max(price, op) * 1.003
        lo = min(price, op) * 0.997
        out.append(
            models.Candle(
                exchange="t",
                symbol="BTC",
                timeframe="1h",
                open_time=start + i * 3_600_000,
                open=Decimal(str(round(op, 2))),
                high=Decimal(str(round(hi, 2))),
                low=Decimal(str(round(lo, 2))),
                close=Decimal(str(round(price, 2))),
                volume=Decimal("10"),
                quote_volume=Decimal("1"),
            )
        )
    return out


@pytest.mark.asyncio
async def test_pullback_emits_signals_in_trend():
    strategy = PullbackStrategy()
    candles = _bullish_candles(2000)
    signals = 0
    for i in range(250, len(candles)):
        sig = await strategy.evaluate(
            "BTC", candles[: i + 1], current_price=float(candles[i].close)
        )
        if sig:
            signals += 1
            assert sig.risk_reward_ratio == pytest.approx(0.75, abs=0.05)
            assert sig.entry_price > 0
            assert sig.stop_loss < sig.entry_price or sig.direction.value == "short"
    assert signals > 0


@pytest.mark.asyncio
async def test_pullback_returns_none_without_enough_data():
    strategy = PullbackStrategy()
    candles = _bullish_candles(100)
    sig = await strategy.evaluate("BTC", candles)
    assert sig is None


def test_stop_and_take_calculation():
    strategy = PullbackStrategy()
    entry = Decimal("100")
    stop = strategy.calculate_stop_loss(entry, candles=[])
    assert stop == Decimal("100") * Decimal("0.992")
    tp_levels = strategy.calculate_take_profit(entry, stop, candles=[])
    assert len(tp_levels) == 1
    # risk = 0.8; take = entry + 0.8 * 0.75 = 100.6
    assert float(tp_levels[0]["price"]) == pytest.approx(100.6, abs=0.01)
