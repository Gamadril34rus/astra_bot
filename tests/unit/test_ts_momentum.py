"""Тесты флип-стратегии time-series momentum (ts_momentum).

Правило: держим направление рынка по знаку доходности за N дней с мёртвой
зоной ±band; сигнал — только при смене режима (0→long, long→short, …→0).
Проверено на истории скриптом scripts/research_free_strategies.py.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from astra_bot.core import models
from astra_bot.strategies.ts_momentum import (
    TSM_ACTION_FLAT,
    TSM_ACTION_FLIP,
    TimeSeriesMomentumConfig,
    TimeSeriesMomentumStrategy,
)


def _candles(
    n: int,
    tf_ms: int = 3_600_000,
    start: int = 1_700_000_000_000,
    base: float = 100.0,
    step: float = 0.001,
    high_pad: float = 0.002,
    low_pad: float = 0.002,
):
    """n свечей с постоянным шагом цены (вверх при step>0)."""
    out = []
    price = base
    for i in range(n):
        op = price
        cl = price * (1 + step)
        out.append(
            models.Candle(
                exchange="test",
                symbol="BTC/USDT",
                timeframe="1h",
                open_time=start + i * tf_ms,
                open=Decimal(str(round(op, 6))),
                high=Decimal(str(round(max(op, cl) * (1 + high_pad), 6))),
                low=Decimal(str(round(min(op, cl) * (1 - low_pad), 6))),
                close=Decimal(str(round(cl, 6))),
                volume=Decimal("10"),
                quote_volume=Decimal("1"),
            )
        )
        price = cl
    return out


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _cfg(lookback_days: int = 2, **kwargs) -> TimeSeriesMomentumConfig:
    return TimeSeriesMomentumConfig(lookback_days=lookback_days, **kwargs)


def test_no_signal_with_insufficient_candles():
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=30))
    candles = _candles(20)
    assert _run(s.evaluate("BTC/USDT", candles)) is None


def test_bullish_momentum_emits_long_flip():
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=2))
    candles = _candles(60, step=0.002)  # ~4.8% за 48 баров > band 2%
    sig = _run(s.evaluate("BTC/USDT", candles))
    assert sig is not None
    assert sig.direction == models.TradeDirection.LONG
    assert sig.features["tsm_action"] == TSM_ACTION_FLIP
    assert sig.features["no_take_profit"] == 1.0
    assert sig.stop_loss < sig.entry_price
    assert sig.take_profit == Decimal("0")
    assert sig.strategy_name == "ts_momentum"


def test_no_repeat_signal_while_regime_unchanged():
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=2))
    candles = _candles(60, step=0.002)
    assert _run(s.evaluate("BTC/USDT", candles)) is not None
    # Режим тот же — повторного сигнала быть не должно.
    assert _run(s.evaluate("BTC/USDT", candles)) is None


def test_deadband_holds_position_no_signal():
    """Мёртвая зона держит режим: затухание импульса не даёт сигнала."""
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=2))
    bull = _candles(60, step=0.002)
    assert _run(s.evaluate("BTC/USDT", bull)) is not None  # long
    # Боковик (доходность ≈ 0.5% < band) — режим держится, сигнала нет.
    flat = _candles(60, step=0.0001)
    assert _run(s.evaluate("BTC/USDT", flat)) is None


def test_flat_signal_when_long_only_and_momentum_bearish():
    """long-only: импульс ниже −band закрывает позицию (flat)."""
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=2, allow_short=False))
    bull = _candles(60, step=0.002)
    assert _run(s.evaluate("BTC/USDT", bull)) is not None  # long
    bear = _candles(60, step=-0.002)
    sig = _run(s.evaluate("BTC/USDT", bear))
    assert sig is not None
    assert sig.features["tsm_action"] == TSM_ACTION_FLAT


def test_flip_long_to_short_through_deadband():
    """Переворот long→short при импульсе ниже −band (через мёртвую зону)."""
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=2))
    bull = _candles(60, step=0.002)
    assert _run(s.evaluate("BTC/USDT", bull)) is not None  # long
    bear = _candles(60, step=-0.002)
    sig = _run(s.evaluate("BTC/USDT", bear))
    assert sig is not None
    assert sig.direction == models.TradeDirection.SHORT
    assert sig.features["tsm_action"] == TSM_ACTION_FLIP


def test_short_flip_when_allowed():
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=2))
    bear = _candles(60, step=-0.002)
    sig = _run(s.evaluate("BTC/USDT", bear))
    assert sig is not None
    assert sig.direction == models.TradeDirection.SHORT
    assert sig.stop_loss > sig.entry_price


def test_long_only_ignores_bearish_momentum():
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=2, allow_short=False))
    bear = _candles(60, step=-0.002)
    assert _run(s.evaluate("BTC/USDT", bear)) is None


def test_lookback_bars_derived_from_timeframe():
    s = TimeSeriesMomentumStrategy(_cfg(lookback_days=45))
    assert s._lookback_bars(_candles(10, tf_ms=3_600_000)) == 45 * 24
    assert s._lookback_bars(_candles(10, tf_ms=14_400_000)) == 45 * 6
    assert s._lookback_bars(_candles(10, tf_ms=86_400_000)) == 45
    assert s._lookback_bars(_candles(2)) == 0


def test_skip_signal_when_disabled():
    cfg = _cfg(lookback_days=2)
    cfg.enabled = False
    s = TimeSeriesMomentumStrategy(cfg)
    candles = _candles(60, step=0.002)
    assert _run(s.evaluate("BTC/USDT", candles)) is None
