"""Бэклог аудита A5: входы — только по закрытым барам.

Фид klines биржи отдаёт текущий НЕЗАКРЫТЫЙ бар последним. До фикса
стратегии строили входной сигнал по ``primary[-1]`` (формирующийся
бар, close ещё плывёт). Теперь для генерации ВХОДНЫХ сигналов
формирующийся бар вычитается; выходы/трейлинг/стопы на живой цене —
не тронуты.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from astra_bot.core import models
from astra_bot.decision.context import MarketContext
from astra_bot.decision.pipeline import DecisionPipeline


def _candles(n: int, tf: str = "5m") -> list[models.Candle]:
    out = []
    for i in range(n):
        out.append(
            models.Candle(
                exchange="bingx",
                symbol="TEST-USDT",
                timeframe=tf,
                open_time=1_700_000_000_000 + i * 300_000,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("10"),
                quote_volume=Decimal("1000"),
            )
        )
    return out


# ------------------------------------------------------------ pure helper
def test_drops_forming_candle():
    cs = _candles(5)
    closed = DecisionPipeline._closed_entry_candles(cs)
    assert len(closed) == 4
    # Последняя закрытая — не та, что была последней во входе.
    assert closed[-1].open_time == cs[-2].open_time
    # Исходный список не мутирован (важно: candles_on возвращает shared).
    assert len(cs) == 5


def test_single_candle_kept():
    cs = _candles(1)
    assert len(DecisionPipeline._closed_entry_candles(cs)) == 1


def test_empty_candles():
    assert DecisionPipeline._closed_entry_candles([]) == []


# --------------------------------------------------------------- pipeline
class _ProbeStrategy:
    """Запоминает, сколько свечей получила в evaluate."""

    name = "probe"
    preferred_timeframe = None

    def __init__(self) -> None:
        self.seen: list[int] = []

    async def evaluate(self, symbol, candles, orderbook=None, current_price=0.0, market_regime=""):
        self.seen.append(len(candles))
        return None


def _ctx(candles_5m: list[models.Candle]) -> MarketContext:
    return MarketContext(
        symbol="TEST-USDT",
        current_price=Decimal("100.5"),
        candles={"5m": candles_5m},
    )


@pytest.mark.asyncio
async def test_strategy_sees_only_closed_candles():
    pipe = DecisionPipeline()
    probe = _ProbeStrategy()
    pipe.strategies = [probe]
    cs = _candles(5)
    out = await pipe._candidates_from_strategies(_ctx(cs), regime="ANY")
    # Стратегия получила 4 (5 минус формирующийся), сигналов нет.
    assert probe.seen == [4]
    assert out == []


@pytest.mark.asyncio
async def test_preferred_tf_also_excludes_forming():
    pipe = DecisionPipeline()

    class _Probe4h(_ProbeStrategy):
        preferred_timeframe = "4h"

    probe = _Probe4h()
    pipe.strategies = [probe]
    ctx = MarketContext(
        symbol="TEST-USDT",
        current_price=Decimal("100.5"),
        candles={
            "5m": _candles(5, "5m"),
            "4h": _candles(6, "4h"),
        },
    )
    await pipe._candidates_from_strategies(ctx, regime="ANY")
    # По preferred_tf (4h) тоже без формирующегося: 6 -> 5.
    assert probe.seen == [5]
