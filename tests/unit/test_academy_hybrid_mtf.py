"""Юнит-тесты для Академической гибридной MTF-стратегии (Academy Hybrid MTF Strategy)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.decision.strategy_registry import STRATEGY_REGISTRY
from astra_bot.strategies.academy_hybrid_mtf import AcademyHybridMTFStrategy


@pytest.mark.asyncio
async def test_academy_hybrid_mtf_registry_entry():
    assert "academy_hybrid_mtf" in STRATEGY_REGISTRY
    entry = STRATEGY_REGISTRY["academy_hybrid_mtf"]
    assert entry.tier == "audit"
    assert entry.execution_blocked_reason is not None


@pytest.mark.asyncio
async def test_academy_hybrid_mtf_evaluation():
    strat = AcademyHybridMTFStrategy()

    c1d = [
        models.Candle("t", "BTCUSDT", "1d", 1000 + i * 86400, Decimal(str(100 + i)), Decimal(str(101 + i)), Decimal(str(99 + i)), Decimal(str(100.5 + i)), Decimal("100"), Decimal("10"))
        for i in range(210)
    ]
    c4h = [
        models.Candle("t", "BTCUSDT", "4h", 1000 + i * 14400, Decimal("100"), Decimal("102"), Decimal("98"), Decimal("101"), Decimal("100"), Decimal("10"))
        for i in range(20)
    ]
    c1h = [
        models.Candle("t", "BTCUSDT", "1h", 1000 + i * 3600, Decimal("100"), Decimal("102"), Decimal("97"), Decimal("101"), Decimal("1000"), Decimal("10"))
        for i in range(25)
    ]

    sig = await strat.evaluate(
        "BTCUSDT",
        candles=c1h,
        candles_1d=c1d,
        candles_4h=c4h,
        candles_1h=c1h,
    )
    # Проверяем, что оцениватель корректно возвращает сигнал или None без исключений
    assert sig is None or isinstance(sig, models.Signal)
