"""Тесты стратегии из «Простой книги торговли» и её свечных детекторов."""
from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest
from astra_bot.core import models
from astra_bot.ml.market_understanding import compute_market_features
from astra_bot.ml.research_engine import _events
from astra_bot.strategies.book_breakout import BookBreakoutConfig, BookBreakoutStrategy


def _candle(o: float, h: float, lo: float, c: float, t: int = 0, tf: str = "1h") -> models.Candle:
    return models.Candle(
        exchange="okx", symbol="BTC/USDT", timeframe=tf, open_time=t,
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(lo)),
        close=Decimal(str(c)), volume=Decimal("10"), quote_volume=Decimal("1000"),
    )


def _consolidation(n: int, lo: float = 95.0, hi: float = 100.0) -> list[models.Candle]:
    """Пилообразная консолидация в диапазоне [lo, hi]."""
    out = []
    for i in range(n):
        mid = (lo + hi) / 2
        spread = (i % 4) + 1  # разнообразие тел
        if i % 2 == 0:
            o, c = mid - spread, mid + spread * 0.5
        else:
            o, c = mid + spread, mid - spread * 0.5
        out.append(_candle(o, min(hi, max(o, c) + 2), max(lo, min(o, c) - 2), c, t=i))
    return out


def _flat(n: int, price: float = 100.0) -> list[models.Candle]:
    """Спокойные бары для накопления истории."""
    return [
        _candle(price - 0.3, price + 0.8, price - 0.8, price + 0.3, t=1000 + i)
        for i in range(n)
    ]


@pytest.fixture
def strategy() -> BookBreakoutStrategy:
    return BookBreakoutStrategy(
        BookBreakoutConfig(level_lookback=20, breakout_lookback=6, min_bars=31)
    )


async def test_book_long_on_breakout_retest_confirmation(strategy):
    """Пробой сопротивления → ретест → бычья свеча = LONG (стр. 29 книги)."""
    candles = _consolidation(28)
    # Пробой вверх (не на последнем баре!).
    candles.append(_candle(99.0, 102.0, 98.5, 101.5, t=100))
    # Откат к пробитому уровню (ретест) и бычья свеча-подтверждение.
    candles.append(_candle(101.2, 101.6, 100.4, 100.6, t=101))
    candles.append(_candle(100.5, 101.4, 100.3, 101.2, t=102))  # бычья

    sig = await strategy.evaluate("BTC/USDT", candles)
    assert sig is not None
    assert sig.direction == models.TradeDirection.LONG
    assert sig.strategy_name == "book_breakout"
    assert Decimal("0") < sig.stop_loss < sig.entry_price < sig.take_profit
    assert sig.risk_reward_ratio >= 1.0
    assert sig.features["pattern"] == 1.0
    assert sig.features["retest_depth_atr"] >= 0.0


async def test_book_no_fomo_entry_without_retest(strategy):
    """Книга (стр. 29): нельзя покупать сразу на пробое — ждём ретест."""
    candles = _consolidation(30)
    candles.append(_candle(99.0, 102.5, 98.5, 102.0, t=100))  # пробой СЕЙЧАС, последний бар
    sig = await strategy.evaluate("BTC/USDT", candles)
    assert sig is None


async def test_book_requires_confirmation_candle(strategy):
    """Ретест есть, но последняя свеча медвежья — входа нет (стр. 29)."""
    candles = _consolidation(28)
    candles.append(_candle(99.0, 102.0, 98.5, 101.5, t=100))
    candles.append(_candle(101.2, 101.6, 100.4, 100.6, t=101))
    candles.append(_candle(100.6, 100.8, 100.1, 100.2, t=102))  # медвежья
    sig = await strategy.evaluate("BTC/USDT", candles)
    assert sig is None


async def test_book_short_on_support_break_retest(strategy):
    """Зеркально: пробой поддержки вниз + ретест + медвежья свеча = SHORT."""
    candles = _consolidation(28)
    # Пробой поддержки вниз.
    candles.append(_candle(96.0, 96.5, 93.5, 94.0, t=100))
    # Ретест пробитого уровня снизу и медвежья подтверждающая свеча.
    candles.append(_candle(94.2, 95.6, 94.0, 94.8, t=101))
    candles.append(_candle(94.9, 95.2, 93.9, 94.1, t=102))  # медвежья

    sig = await strategy.evaluate("BTC/USDT", candles)
    assert sig is not None
    assert sig.direction == models.TradeDirection.SHORT
    assert sig.take_profit < sig.entry_price < sig.stop_loss


async def test_book_ignores_false_breakout(strategy):
    """Глубокий возврат под уровень = ложный пробой, входа нет."""
    candles = _consolidation(28)
    candles.append(_candle(99.0, 102.0, 98.5, 101.5, t=100))
    # Цена провалилась далеко под уровень (> max_retest_depth_atr).
    candles.append(_candle(101.0, 101.2, 93.0, 93.5, t=101))
    candles.append(_candle(93.5, 94.2, 93.0, 94.0, t=102))
    sig = await strategy.evaluate("BTC/USDT", candles)
    assert sig is None


# ---------------------------------------------------------------- detectors

def _features_with_tail(tail: list[models.Candle]) -> dict[str, float]:
    candles = _flat(70) + tail
    f = compute_market_features(candles, timeframe="1h")
    assert f, "features must be computed for >= 60 candles"
    return f


def test_detects_three_white_soldiers():
    tail = [
        _candle(100.0, 101.8, 99.8, 101.6, t=1),
        _candle(101.5, 102.9, 101.3, 102.7, t=2),
        _candle(102.6, 104.0, 102.4, 103.8, t=3),
    ]
    f = _features_with_tail(tail)
    assert f["three_white_soldiers"] == 1.0
    assert f["three_black_crows"] == 0.0


def test_detects_three_black_crows():
    tail = [
        _candle(103.8, 104.0, 102.4, 102.6, t=1),
        _candle(102.7, 102.9, 101.3, 101.5, t=2),
        _candle(101.6, 101.8, 99.8, 100.0, t=3),
    ]
    f = _features_with_tail(tail)
    assert f["three_black_crows"] == 1.0
    assert f["three_white_soldiers"] == 0.0


def test_detects_spinning_top():
    tail = [_candle(100.0, 101.5, 98.5, 100.2, t=1)]  # тело маленькое, тени длинные
    f = _features_with_tail(tail)
    assert f["candle_spinning_top"] == 1.0


# ------------------------------------------------------- book research events

def _events_for(f: dict) -> list[str]:
    closes = np.full(30, 100.0)
    highs = np.full(30, 101.0)
    lows = np.full(30, 99.0)
    volumes = np.full(30, 10.0)
    return _events(f, closes, highs, lows, volumes, 29)


def test_book_event_hammer_context():
    # Молот после снижения = разворот вверх (стр. 13); после роста = повешенный (стр. 18).
    base = {"candle_hammer": 1.0, "rsi_14": 40.0}
    assert "book_hammer_reversal" in _events_for({**base, "trend_slope_20": -0.02})
    assert "book_hanging_man_top" in _events_for({**base, "trend_slope_20": 0.02})


def test_book_event_breakout_retest_combo():
    long_f = {
        "retest_resistance": 1.0, "pivot_high_distance_atr": 0.0,
        "candle_bull": 1.0, "rsi_14": 55.0, "trend_slope_20": 0.0,
    }
    assert "book_breakout_retest_long" in _events_for(long_f)
    short_f = {
        "retest_support": 1.0, "pivot_low_distance_atr": 0.0,
        "candle_bear": 1.0, "rsi_14": 45.0, "trend_slope_20": 0.0,
    }
    assert "book_breakout_retest_short" in _events_for(short_f)
    # Без подтверждающей свечи события нет (анти-FOMO книги).
    assert "book_breakout_retest_long" not in _events_for({**long_f, "candle_bull": 0.0})


def test_book_event_indecision_pause():
    f = {"candle_doji": 1.0, "rsi_14": 50.0, "trend_slope_20": 0.0}
    assert "book_indecision_pause" in _events_for(f)
    f2 = {"candle_spinning_top": 1.0, "rsi_14": 50.0, "trend_slope_20": 0.0}
    assert "book_indecision_pause" in _events_for(f2)
