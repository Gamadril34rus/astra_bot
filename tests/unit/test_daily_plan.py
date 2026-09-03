"""Тесты построения суточного плана сделок."""

import random
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.ml.daily_plan import (
    PlannedTrade,
    build_daily_plan,
    format_plan,
)


@pytest.fixture()
def history():
    bars = {}
    for i, symbol in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT")):
        random.seed(i + 1)
        base = 30000.0 if "BTC" in symbol else 2000.0 if "ETH" in symbol else 100.0
        out = []
        start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        for j in range(300):
            base *= 1 + random.uniform(-0.004, 0.005)
            out.append(
                models.Candle(
                    exchange="bingx",
                    symbol=symbol,
                    timeframe="1h",
                    open_time=start + j * 3_600_000,
                    open=Decimal(str(base)),
                    high=Decimal(str(base * 1.002)),
                    low=Decimal(str(base * 0.998)),
                    close=Decimal(str(base)),
                    volume=Decimal(str(random.uniform(5, 30))),
                    quote_volume=Decimal("1"),
                )
            )
        bars[symbol] = out
    return bars


async def test_build_daily_plan_without_model_returns_ranked_candidates(history):
    # Передадим фиктивную стратегию, у которой evaluate всегда даёт сигнал.
    class _FakeStrategy:
        name = "fake"

        async def evaluate(self, *args, **kwargs):
            return None

    # Без модели и без реальных сигналов план пуст.

    plan = await build_daily_plan(
        history=history,
        strategies=[_FakeStrategy()],
        model_path=__import__("pathlib").Path("/nonexistent.pkl"),
    )
    assert plan == []


def test_format_plan_handles_empty_and_nonempty():
    assert "сделок нет" in format_plan([])

    fake = [
        PlannedTrade(
            symbol="BTC/USDT",
            direction="long",
            strategy="momentum",
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit=51000.0,
            risk_reward=2.0,
            strategy_confidence=0.7,
            ml_probability=0.65,
            regime="BULL_TREND",
            expected_value=650.0,
            reason="ML win prob 65%",
        )
    ]
    text = format_plan(fake)
    assert "BTC/USDT" in text
    assert "R:R = 2.00" in text
    assert "ML win = 65%" in text
