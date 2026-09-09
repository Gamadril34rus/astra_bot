"""Блок I: STRUCT-STOP двигает TP пропорционально расширению стопа."""

from __future__ import annotations

from decimal import Decimal

from astra_bot.decision.trading_engine import TradingEngine

F = TradingEngine._scaled_tp_with_stop


def test_doubled_stop_doubles_tp_distance_long():
    # entry 100, стоп 99→98 (R ×2), TP 102 → 104: RR сохранён.
    assert F(100, 99, 98, 102, "long") == Decimal("104.0")


def test_doubled_stop_doubles_tp_distance_short():
    # entry 100, стоп 101→102 (R ×2), TP 98 → 96.
    assert F(100, 101, 102, 98, "short") == Decimal("96.0")


def test_scale_capped_at_2_2():
    # R вырос ×5 → TP двигаем только ×2.2: 100 + 2×2.2 = 104.4.
    got = F(100, 99, 94, 102, "long")
    assert abs(float(got) - 104.4) < 1e-9


def test_no_scale_cases():
    assert F(100, 99, 99, 102, "long") is None  # стоп не расширился
    assert F(100, 99, 98, 98, "long") is None  # TP не на своей стороне
    assert F(100, 101, 102, 102, "short") is None  # TP не на своей стороне
    assert F(100, 99, 98, 0, "long") is None  # тейка нет
    assert F(100, 100, 98, 102, "long") is None  # нулевой исходный риск
