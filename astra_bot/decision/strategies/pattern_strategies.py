"""
Chart Pattern Strategies — клинья и треугольники.

Основа по ТЗ пользователя:
- Клин вниз (падающий клин) → Лонг
- Клин вверх (восходящий клин) → Шорт
- Треугольники: восходящий, нисходящий, симметричный

Детекция через линейную регрессию по свингам high/low.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from ..context import SignalCandidate, StrategyContext

logger = logging.getLogger(__name__)


class PatternType(Enum):
    FALLING_WEDGE = "FALLING_WEDGE"  # Бычий, лонг
    RISING_WEDGE = "RISING_WEDGE"  # Медвежий, шорт
    ASCENDING_TRIANGLE = "ASCENDING_TRIANGLE"  # Бычий, лонг
    DESCENDING_TRIANGLE = "DESCENDING_TRIANGLE"  # Медвежий, шорт
    SYMMETRICAL_TRIANGLE = "SYMMETRICAL_TRIANGLE"  # Оба направления
    NONE = "NONE"


@dataclass
class TrendLine:
    slope: float
    intercept: float
    r2: float  # качество фита


@dataclass
class PatternResult:
    pattern: PatternType
    confidence: float
    upper_line: TrendLine | None
    lower_line: TrendLine | None
    breakout_direction: str | None  # "up", "down", None
    diagnostics: dict[str, Any]


def _linear_regression(x: list[float], y: list[float]) -> TrendLine:
    """Простая линейная регрессия y = slope*x + intercept, возвращает R2."""
    n = len(x)
    if n < 2:
        return TrendLine(slope=0.0, intercept=y[0] if y else 0.0, r2=0.0)
    try:
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y, strict=False))
        sum_x2 = sum(xi * xi for xi in x)
        denom = n * sum_x2 - sum_x * sum_x
        if abs(denom) < 1e-9:
            return TrendLine(slope=0.0, intercept=sum_y / n, r2=0.0)
        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # R2
        y_mean = sum_y / n
        ss_tot = sum((yi - y_mean) ** 2 for yi in y)
        ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y, strict=False))
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
        return TrendLine(slope=slope, intercept=intercept, r2=max(0.0, min(1.0, r2)))
    except Exception:
        return TrendLine(slope=0.0, intercept=0.0, r2=0.0)


def _find_swings(highs: list[float], lows: list[float], window: int = 3) -> tuple[list[int], list[int]]:
    """Найти индексы свинг-хаев и свинг-лоев."""
    high_idx = []
    low_idx = []
    for i in range(window, len(highs) - window):
        # Swing high: max in window
        if highs[i] == max(highs[i - window : i + window + 1]):
            high_idx.append(i)
        # Swing low: min in window
        if lows[i] == min(lows[i - window : i + window + 1]):
            low_idx.append(i)
    return high_idx, low_idx


def detect_pattern(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    min_swings: int = 3,
) -> PatternResult:
    """
    Детекция паттернов клин/треугольник.

    Логика:
    - Находим свинги high/low
    - Строим трендлинии по последним 4-5 свингам
    - Определяем тип по наклонам:
      * Падающий клин: обе линии вниз, сходятся, пробой вверх → лонг
      * Восходящий клин: обе вверх, сходятся, пробой вниз → шорт
      * Восходящий треугольник: верхняя ~0, нижняя вверх
      * Нисходящий треугольник: нижняя ~0, верхняя вниз
      * Симметричный: верхняя вниз, нижняя вверх, сходятся
    """
    if len(highs) < 20 or len(lows) < 20 or len(closes) < 20:
        return PatternResult(
            pattern=PatternType.NONE,
            confidence=0.0,
            upper_line=None,
            lower_line=None,
            breakout_direction=None,
            diagnostics={"reason": "not enough data"},
        )

    high_idx, low_idx = _find_swings(highs, lows, window=3)

    if len(high_idx) < min_swings or len(low_idx) < min_swings:
        return PatternResult(
            pattern=PatternType.NONE,
            confidence=0.0,
            upper_line=None,
            lower_line=None,
            breakout_direction=None,
            diagnostics={"reason": f"not enough swings high={len(high_idx)} low={len(low_idx)}"},
        )

    # Берём последние 5 свингов для трендлиний
    recent_high_idx = high_idx[-5:]
    recent_low_idx = low_idx[-5:]

    recent_high_vals = [highs[i] for i in recent_high_idx]
    recent_low_vals = [lows[i] for i in recent_low_idx]

    upper_line = _linear_regression([float(i) for i in recent_high_idx], recent_high_vals)
    lower_line = _linear_regression([float(i) for i in recent_low_idx], recent_low_vals)

    # Текущая цена
    price = closes[-1]
    upper_at_now = upper_line.slope * (len(highs) - 1) + upper_line.intercept
    lower_at_now = lower_line.slope * (len(lows) - 1) + lower_line.intercept

    # Диагностика
    diagnostics = {
        "upper_slope": upper_line.slope,
        "lower_slope": lower_line.slope,
        "upper_r2": upper_line.r2,
        "lower_r2": lower_line.r2,
        "upper_at_now": upper_at_now,
        "lower_at_now": lower_at_now,
        "price": price,
        "high_swings": len(high_idx),
        "low_swings": len(low_idx),
    }

    # Проверка качества линий
    if upper_line.r2 < 0.5 or lower_line.r2 < 0.5:
        # Слабые линии — не паттерн
        return PatternResult(
            pattern=PatternType.NONE,
            confidence=0.0,
            upper_line=upper_line,
            lower_line=lower_line,
            breakout_direction=None,
            diagnostics={**diagnostics, "reason": "low R2"},
        )

    # Определяем тип паттерна по наклонам
    # Пороги для "плоской" линии: |slope| < 0.1 * ATR или < 0.0005 * price
    flat_threshold = price * 0.0005  # 0.05% на бар

    upper_flat = abs(upper_line.slope) < flat_threshold
    lower_flat = abs(lower_line.slope) < flat_threshold

    pattern = PatternType.NONE
    confidence = 0.0
    breakout_dir = None

    # Восходящий треугольник: верхняя плоская, нижняя вверх
    if upper_flat and lower_line.slope > flat_threshold:
        pattern = PatternType.ASCENDING_TRIANGLE
        # Сходятся ли? Верхняя плоская, нижняя вверх → сходятся
        confidence = min(upper_line.r2, lower_line.r2) * 0.8 + 0.2
        # Пробой вверх?
        if price > upper_at_now * 1.001:
            breakout_dir = "up"
            confidence = min(0.95, confidence + 0.2)
        diagnostics["type"] = "ascending_triangle"

    # Нисходящий треугольник: нижняя плоская, верхняя вниз
    elif lower_flat and upper_line.slope < -flat_threshold:
        pattern = PatternType.DESCENDING_TRIANGLE
        confidence = min(upper_line.r2, lower_line.r2) * 0.8 + 0.2
        if price < lower_at_now * 0.999:
            breakout_dir = "down"
            confidence = min(0.95, confidence + 0.2)
        diagnostics["type"] = "descending_triangle"

    # Симметричный треугольник: верхняя вниз, нижняя вверх, сходятся
    elif upper_line.slope < -flat_threshold and lower_line.slope > flat_threshold:
        # Проверяем схождение: расстояние между линиями уменьшается
        upper_start = upper_line.slope * recent_high_idx[0] + upper_line.intercept
        lower_start = lower_line.slope * recent_low_idx[0] + lower_line.intercept
        upper_end = upper_line.slope * recent_high_idx[-1] + upper_line.intercept
        lower_end = lower_line.slope * recent_low_idx[-1] + lower_line.intercept
        start_dist = upper_start - lower_start
        end_dist = upper_end - lower_end
        if start_dist > 0 and end_dist > 0 and end_dist < start_dist * 0.8:
            pattern = PatternType.SYMMETRICAL_TRIANGLE
            confidence = min(upper_line.r2, lower_line.r2) * 0.7 + 0.15
            # Пробой в любую сторону
            if price > upper_at_now * 1.001:
                breakout_dir = "up"
                confidence += 0.15
            elif price < lower_at_now * 0.999:
                breakout_dir = "down"
                confidence += 0.15
            diagnostics["type"] = "symmetrical_triangle"
            diagnostics["convergence"] = (start_dist - end_dist) / start_dist

    # Падающий клин: обе вниз, сходятся (верхняя более крутая вниз чем нижняя)
    # Логика: slope_up < 0, slope_low < 0, slope_up < slope_low (более отрицательный), и сходятся
    elif upper_line.slope < -flat_threshold and lower_line.slope < -flat_threshold:
        if upper_line.slope < lower_line.slope:
            # Верхняя падает быстрее → сходятся вниз
            pattern = PatternType.FALLING_WEDGE
            confidence = min(upper_line.r2, lower_line.r2) * 0.75 + 0.15
            # Пробой вверх — бычий
            if price > upper_at_now * 1.001:
                breakout_dir = "up"
                confidence = min(0.95, confidence + 0.2)
            diagnostics["type"] = "falling_wedge"
            # Дополнительно: клин вниз — лонг (по ТЗ)
            diagnostics["signal"] = "long_on_breakout_up"

    # Восходящий клин: обе вверх, сходятся (нижняя более крутая вверх)
    elif upper_line.slope > flat_threshold and lower_line.slope > flat_threshold:
        if lower_line.slope > upper_line.slope:
            pattern = PatternType.RISING_WEDGE
            confidence = min(upper_line.r2, lower_line.r2) * 0.75 + 0.15
            if price < lower_at_now * 0.999:
                breakout_dir = "down"
                confidence = min(0.95, confidence + 0.2)
            diagnostics["type"] = "rising_wedge"
            diagnostics["signal"] = "short_on_breakdown_down"

    # Если не определили, но есть схождение — возможно клин
    if pattern == PatternType.NONE:
        # Проверяем общее схождение
        try:
            upper_start = upper_line.slope * recent_high_idx[0] + upper_line.intercept
            lower_start = lower_line.slope * recent_low_idx[0] + lower_line.intercept
            upper_end = upper_line.slope * recent_high_idx[-1] + upper_line.intercept
            lower_end = lower_line.slope * recent_low_idx[-1] + lower_line.intercept
            if upper_start > lower_start and upper_end > lower_end:
                start_dist = upper_start - lower_start
                end_dist = upper_end - lower_end
                if end_dist < start_dist * 0.7 and end_dist > 0:
                    # Сходящийся канал — определяем по общему наклону
                    avg_slope = (upper_line.slope + lower_line.slope) / 2
                    if avg_slope < -flat_threshold:
                        pattern = PatternType.FALLING_WEDGE
                        confidence = 0.6
                        if price > upper_at_now:
                            breakout_dir = "up"
                    elif avg_slope > flat_threshold:
                        pattern = PatternType.RISING_WEDGE
                        confidence = 0.6
                        if price < lower_at_now:
                            breakout_dir = "down"
        except Exception:
            pass

    return PatternResult(
        pattern=pattern,
        confidence=confidence,
        upper_line=upper_line,
        lower_line=lower_line,
        breakout_direction=breakout_dir,
        diagnostics=diagnostics,
    )


# ----------------------------------------------------------------------
# Стратегии на основе паттернов
# ----------------------------------------------------------------------


def _volume_ok(volumes: list[float], threshold: float = 1.2) -> bool:
    if len(volumes) < 20:
        return True
    sma = sum(volumes[-20:]) / 20
    return volumes[-1] / sma >= threshold if sma > 0 else True


class BasePatternStrategy:
    """Базовый класс для паттерн-стратегий."""

    def __init__(self, volume_threshold: float = 1.2):
        self.volume_threshold = volume_threshold

    def _check_volume(self, candles) -> bool:
        try:
            vols = [float(getattr(c, "volume", 0)) for c in candles]
            return _volume_ok(vols, self.volume_threshold)
        except Exception:
            return True


class FallingWedgeStrategy(BasePatternStrategy):
    """
    Падающий клин — бычий паттерн, лонг на пробое вверх.

    По ТЗ: клин вниз = лонг
    """

    name = "falling_wedge"

    async def evaluate(self, ctx: StrategyContext):
        candles = ctx.candles
        if len(candles) < 50:
            return None

        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]

        result = detect_pattern(highs, lows, closes)

        if result.pattern != PatternType.FALLING_WEDGE:
            return None

        if result.confidence < 0.5:
            return None

        # Нужен пробой вверх или близость к пробою
        if result.breakout_direction != "up" and result.confidence < 0.7:
            # Если ещё нет пробоя, но паттерн сильный — ждём, но можно дать сигнал с меньшей уверенностью
            # Для торговли нужен именно пробой
            if result.diagnostics.get("price", 0) < result.diagnostics.get("upper_at_now", 0) * 0.995:
                return None

        if not self._check_volume(candles):
            return None

        price = closes[-1]
        # SL за нижнюю линию клина, TP 2:1
        lower_at_now = result.diagnostics.get("lower_at_now", price * 0.99)
        risk = price - lower_at_now
        if risk <= 0:
            risk = price * 0.01
        sl = price - risk * 1.2
        tp = price + risk * 2.5  # Клин часто даёт сильный импульс

        return SignalCandidate(
            symbol=ctx.symbol,
            direction="long",
            entry_price=Decimal(str(price)),
            stop_loss=Decimal(str(sl)),
            take_profit=Decimal(str(tp)),
            timeframe=ctx.timeframe,
            strategy=self.name,
            confidence=result.confidence,
            features={
                "pattern": result.pattern.value,
                "upper_slope": result.upper_line.slope if result.upper_line else 0,
                "lower_slope": result.lower_line.slope if result.lower_line else 0,
                "breakout": result.breakout_direction,
            },
        )


class RisingWedgeStrategy(BasePatternStrategy):
    """
    Восходящий клин — медвежий паттерн, шорт на пробое вниз.

    По ТЗ: клин вверх = шорт
    """

    name = "rising_wedge"

    async def evaluate(self, ctx: StrategyContext):
        candles = ctx.candles
        if len(candles) < 50:
            return None

        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]

        result = detect_pattern(highs, lows, closes)

        if result.pattern != PatternType.RISING_WEDGE:
            return None

        if result.confidence < 0.5:
            return None

        if result.breakout_direction != "down" and result.confidence < 0.7:
            if result.diagnostics.get("price", 0) > result.diagnostics.get("lower_at_now", 0) * 1.005:
                return None

        if not self._check_volume(candles):
            return None

        price = closes[-1]
        upper_at_now = result.diagnostics.get("upper_at_now", price * 1.01)
        risk = upper_at_now - price
        if risk <= 0:
            risk = price * 0.01
        sl = price + risk * 1.2
        tp = price - risk * 2.5

        return SignalCandidate(
            symbol=ctx.symbol,
            direction="short",
            entry_price=Decimal(str(price)),
            stop_loss=Decimal(str(sl)),
            take_profit=Decimal(str(tp)),
            timeframe=ctx.timeframe,
            strategy=self.name,
            confidence=result.confidence,
            features={
                "pattern": result.pattern.value,
                "upper_slope": result.upper_line.slope if result.upper_line else 0,
                "lower_slope": result.lower_line.slope if result.lower_line else 0,
                "breakout": result.breakout_direction,
            },
        )


class AscendingTriangleStrategy(BasePatternStrategy):
    """Восходящий треугольник — бычий, лонг на пробое вверх."""

    name = "ascending_triangle"

    async def evaluate(self, ctx: StrategyContext):
        candles = ctx.candles
        if len(candles) < 40:
            return None

        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]

        result = detect_pattern(highs, lows, closes)

        if result.pattern != PatternType.ASCENDING_TRIANGLE:
            return None

        if result.confidence < 0.55:
            return None

        # Для восходящего треугольника нужен пробой вверх
        if result.breakout_direction != "up":
            # Можно дать сигнал если цена близко к верхней границе
            price = closes[-1]
            upper = result.diagnostics.get("upper_at_now", price)
            if price < upper * 0.998:
                return None

        if not self._check_volume(candles):
            return None

        price = closes[-1]
        lower = result.diagnostics.get("lower_at_now", price * 0.99)
        risk = price - lower
        if risk <= 0:
            risk = price * 0.01

        return SignalCandidate(
            symbol=ctx.symbol,
            direction="long",
            entry_price=Decimal(str(price)),
            stop_loss=Decimal(str(price - risk)),
            take_profit=Decimal(str(price + risk * 2.0)),
            timeframe=ctx.timeframe,
            strategy=self.name,
            confidence=result.confidence,
            features={"pattern": result.pattern.value, "breakout": result.breakout_direction},
        )


class DescendingTriangleStrategy(BasePatternStrategy):
    """Нисходящий треугольник — медвежий, шорт на пробое вниз."""

    name = "descending_triangle"

    async def evaluate(self, ctx: StrategyContext):
        candles = ctx.candles
        if len(candles) < 40:
            return None

        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]

        result = detect_pattern(highs, lows, closes)

        if result.pattern != PatternType.DESCENDING_TRIANGLE:
            return None

        if result.confidence < 0.55:
            return None

        if result.breakout_direction != "down":
            price = closes[-1]
            lower = result.diagnostics.get("lower_at_now", price)
            if price > lower * 1.002:
                return None

        if not self._check_volume(candles):
            return None

        price = closes[-1]
        upper = result.diagnostics.get("upper_at_now", price * 1.01)
        risk = upper - price
        if risk <= 0:
            risk = price * 0.01

        return SignalCandidate(
            symbol=ctx.symbol,
            direction="short",
            entry_price=Decimal(str(price)),
            stop_loss=Decimal(str(price + risk)),
            take_profit=Decimal(str(price - risk * 2.0)),
            timeframe=ctx.timeframe,
            strategy=self.name,
            confidence=result.confidence,
            features={"pattern": result.pattern.value, "breakout": result.breakout_direction},
        )


class SymmetricalTriangleStrategy(BasePatternStrategy):
    """Симметричный треугольник — пробой в любую сторону."""

    name = "symmetrical_triangle"

    async def evaluate(self, ctx: StrategyContext):
        candles = ctx.candles
        if len(candles) < 40:
            return None

        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]

        result = detect_pattern(highs, lows, closes)

        if result.pattern != PatternType.SYMMETRICAL_TRIANGLE:
            return None

        if result.confidence < 0.6:
            return None

        if result.breakout_direction is None:
            return None

        if not self._check_volume(candles):
            return None

        price = closes[-1]
        upper = result.diagnostics.get("upper_at_now", price * 1.01)
        lower = result.diagnostics.get("lower_at_now", price * 0.99)
        risk = (upper - lower) / 2
        if risk <= 0:
            risk = price * 0.01

        if result.breakout_direction == "up":
            return SignalCandidate(
                symbol=ctx.symbol,
                direction="long",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(price - risk)),
                take_profit=Decimal(str(price + risk * 2.0)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=result.confidence,
                features={"pattern": result.pattern.value, "breakout": "up"},
            )
        else:
            return SignalCandidate(
                symbol=ctx.symbol,
                direction="short",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(price + risk)),
                take_profit=Decimal(str(price - risk * 2.0)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=result.confidence,
                features={"pattern": result.pattern.value, "breakout": "down"},
            )


# Все паттерн-стратегии для удобного импорта
ALL_PATTERN_STRATEGIES = [
    FallingWedgeStrategy,
    RisingWedgeStrategy,
    AscendingTriangleStrategy,
    DescendingTriangleStrategy,
    SymmetricalTriangleStrategy,
]
