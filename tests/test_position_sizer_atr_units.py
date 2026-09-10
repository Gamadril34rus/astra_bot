"""Бэклог аудита A3: единицы ATR в position_sizer — только % цены.

Регрессии:
* мёртвая ветка «extreme > 5% -> 0.3x» (атр>5 всегда попадал в atr>2);
* абсолютный ATR, просоченный как проценты, не должен жать позицию
  в 3+ раза (sanity-guard > 20% -> без корректировки).
"""
from __future__ import annotations

from decimal import Decimal

from astra_bot.engines.position_sizer import calculate_position_size

COMMON = dict(
    equity=Decimal("36000"),
    entry_price=Decimal("100"),
    stop_loss=Decimal("95"),
    risk_per_trade_pct=Decimal("0.01"),
    win_rate=0.55,
    avg_win_r=1.5,
    avg_loss_r=1.0,
    ml_confidence=None,
    # 100% — чтобы хард-макс по notionals не клипал базовый размер
    # (иначе отношение с ATR-корректировкой искажается клипом).
    max_notional_pct=Decimal("1.0"),
)


def test_no_atr_no_adjustment():
    base = calculate_position_size(atr_pct=None, **COMMON)
    none_atr = calculate_position_size(atr_pct=1.0, **COMMON)
    # ATR <= 2% — корректировки нет вообще.
    assert base == none_atr


def test_moderate_vol_reduces_size():
    base = calculate_position_size(atr_pct=None, **COMMON)
    shrunk = calculate_position_size(atr_pct=3.0, **COMMON)
    # 2/3 = 0.667 (но не меньше 0.3)
    assert 0 < shrunk < base
    assert float(shrunk / base) < 0.9


def test_extreme_vol_hits_0_3x():
    """atr=6% — раньше давал 2/6=0.333 (мёртвый elif), теперь строго 0.3x."""
    base = calculate_position_size(atr_pct=None, **COMMON)
    extreme = calculate_position_size(atr_pct=6.0, **COMMON)
    assert abs(float(extreme / base) - 0.3) < 0.01


def test_absolute_atr_not_slipped_as_pct():
    """Абсолютный ATR (например 50 при цене 100k) — не корректируем.

    Без guard размер жали бы в ~3.3 раза (max(0.3, 2/50)).
    """
    base = calculate_position_size(atr_pct=None, **COMMON)
    slipped = calculate_position_size(atr_pct=50.0, **COMMON)
    assert slipped == base


def test_boundary_at_2_and_5():
    """Границы: ровно 2% — без корректировки; ровно 5% — 2/5 = 0.4x
    (ветка «extreme» срабатывает строго при >5%)."""
    base = calculate_position_size(atr_pct=None, **COMMON)
    assert calculate_position_size(atr_pct=2.0, **COMMON) == base
    at_5 = calculate_position_size(atr_pct=5.0, **COMMON)
    assert abs(float(at_5 / base) - 0.4) < 0.01
