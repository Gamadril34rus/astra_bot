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
    ROUNDED_BOTTOM = "ROUNDED_BOTTOM"  # Закругление снизу (чаша) — лонг
    ROUNDED_TOP = "ROUNDED_TOP"  # Закругление сверху (купол) — шорт
    NONE = "NONE"


# Минимальное число касаний границы (по ТЕНЯМ) для подтверждения линии.
# Правило «3 касания снизу + 3 сверху»: граница проверена рынком, и
# пробой после этого — не случайность.
MIN_TOUCHES_PER_LINE = 3


@dataclass
class TrendLine:
    slope: float
    intercept: float
    r2: float  # качество фита
    rmse: float = 0.0  # СКО остатков (для плоских линий R² вырождается)
    # Квадратичное расширение y = a*x^2 + b*x + c для ЗАКРУГЛЁННЫХ
    # границ (клинья с закруглением). None — прямая линия.
    quad: tuple[float, float, float] | None = None
    # Кривизна 2a: >0 — чаша (выпукла вниз), <0 — купол (выпукла вверх).
    curvature: float = 0.0

    def value_at(self, x: float) -> float:
        """Значение линии в точке x (учитывает закругление)."""
        if self.quad is not None:
            a, b, c = self.quad
            return a * x * x + b * x + c
        return self.slope * x + self.intercept

    def slope_at(self, x: float) -> float:
        """Локальный наклон в точке x (для кривой — производная)."""
        if self.quad is not None:
            a, b, _ = self.quad
            return 2.0 * a * x + b
        return self.slope


@dataclass
class PatternResult:
    pattern: PatternType
    confidence: float
    upper_line: TrendLine | None
    lower_line: TrendLine | None
    breakout_direction: str | None  # "up", "down", None
    diagnostics: dict[str, Any]


def _fit_line(x: list[float], y: list[float]) -> TrendLine:
    """Линейная регрессия; при явной кривизне — квадратичная.

    Закруглённые клинья/чаши парабола описывает существенно лучше
    прямой: если квадратичный fit даёт R² заметно выше линейного —
    берём его (линия «с закруглением»).
    """
    line = _linear_regression(x, y)
    if len(x) >= 4:
        quad = _quadratic_regression(x, y)
        if quad is not None and quad.r2 >= line.r2 + 0.05:
            return quad
    return line


def _quadratic_regression(x: list[float], y: list[float]) -> TrendLine | None:
    """МНК-фит y = a*x² + b*x + c через numpy.polyfit.

    Внимание к численной устойчивости: нормальные уравнения «в лоб»
    плохо обусловлены при x ~ 100+ (s4 ~ 1e8), polyfit масштабирует
    базис внутри и даёт стабильный результат.
    """
    n = len(x)
    if n < 4:
        return None
    try:
        import numpy as np

        coefs = np.polyfit(np.asarray(x, dtype=float), np.asarray(y, dtype=float), 2)
        a, b, c = (float(v) for v in coefs)
        if not all(abs(v) < 1e12 for v in (a, b, c)):
            return None

        def q(xi: float) -> float:
            return a * xi * xi + b * xi + c

        y_mean = sum(y) / n
        ss_tot = sum((yi - y_mean) ** 2 for yi in y)
        ss_res = sum((yi - q(xi)) ** 2 for xi, yi in zip(x, y, strict=False))
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
        return TrendLine(
            slope=b,
            intercept=c,
            r2=max(0.0, min(1.0, r2)),
            rmse=(ss_res / n) ** 0.5,
            quad=(a, b, c),
            curvature=2.0 * a,
        )
    except Exception:
        return None


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
        rmse = (ss_res / n) ** 0.5
        return TrendLine(
            slope=slope,
            intercept=intercept,
            r2=max(0.0, min(1.0, r2)),
            rmse=rmse,
        )
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

    upper_line = _fit_line([float(i) for i in recent_high_idx], recent_high_vals)
    lower_line = _fit_line([float(i) for i in recent_low_idx], recent_low_vals)

    # Текущая цена
    price = closes[-1]
    last_idx = float(len(highs) - 1)
    upper_at_now = upper_line.value_at(last_idx)
    lower_at_now = lower_line.value_at(last_idx)

    # Касания границы ТЕНЯМИ: свинг — касание, если экстремум лёг в
    # пределах допуска от линии. Допуск — от СКО остатков линии
    # (адаптивен к масштабу), минимум 0.3% цены.
    def _count_touches(line: TrendLine, idxs: list[int], values: list[float]) -> int:
        tol = max(line.rmse * 1.5, price * 0.003)
        touches = 0
        for i, v in zip(idxs, values, strict=False):
            if abs(v - line.value_at(float(i))) <= tol:
                touches += 1
        return touches

    upper_touches = _count_touches(upper_line, high_idx, [highs[i] for i in high_idx])
    lower_touches = _count_touches(lower_line, low_idx, [lows[i] for i in low_idx])
    # Правило «3 сверху + 3 снизу»: граница проверена рынком.
    touches_confirmed = (
        upper_touches >= MIN_TOUCHES_PER_LINE and lower_touches >= MIN_TOUCHES_PER_LINE
    )

    # Диагностика
    diagnostics = {
        "upper_slope": upper_line.slope_at(last_idx),
        "lower_slope": lower_line.slope_at(last_idx),
        "upper_r2": upper_line.r2,
        "lower_r2": lower_line.r2,
        "upper_at_now": upper_at_now,
        "lower_at_now": lower_at_now,
        "price": price,
        "high_swings": len(high_idx),
        "low_swings": len(low_idx),
        "upper_touches": upper_touches,
        "lower_touches": lower_touches,
        "touches_confirmed": touches_confirmed,
        "upper_rounded": upper_line.quad is not None,
        "lower_rounded": lower_line.quad is not None,
    }

    # Проверка качества линий.
    # Для ПОЛОГИХ линий R² вырождается: у идеальной плоской поддержки с
    # мелким шумом ss_tot→0 и R² падает, хотя линия отличная. Поэтому
    # линия считается качественной, если R² достаточно (наклонные), ИЛИ
    # СКО остатков мало относительно цены (плоские).
    def _line_is_good(line: TrendLine) -> bool:
        return line.r2 >= 0.5 or line.rmse <= price * 0.003

    if not (_line_is_good(upper_line) and _line_is_good(lower_line)):
        # Слабые линии — не паттерн
        return PatternResult(
            pattern=PatternType.NONE,
            confidence=0.0,
            upper_line=upper_line,
            lower_line=lower_line,
            breakout_direction=None,
            diagnostics={
                **diagnostics,
                "reason": "low R2",
                "upper_rmse": upper_line.rmse,
                "lower_rmse": lower_line.rmse,
            },
        )

    # Определяем тип паттерна. Для кривых линий (закругления) важен
    # ЛОКАЛЬНЫЙ наклон на правом крае, а не средний slope регрессии.
    flat_threshold = price * 0.0005  # 0.05% на бар

    up_slope = upper_line.slope_at(last_idx)
    lo_slope = lower_line.slope_at(last_idx)
    up_curv = upper_line.curvature
    lo_curv = lower_line.curvature

    upper_flat = abs(up_slope) < flat_threshold
    lower_flat = abs(lo_slope) < flat_threshold

    pattern = PatternType.NONE
    confidence = 0.0
    breakout_dir = None

    # Пробой границы отталкиваемся от ТЕНЕЙ: хай/лоу последнего бара за
    # линией — пробой тенью (потенциал), close за линией — подтверждён.
    upper_wick = highs[-1] > upper_at_now * 1.001 or price > upper_at_now * 1.001
    lower_wick = lows[-1] < lower_at_now * 0.999 or price < lower_at_now * 0.999
    body_up = price > upper_at_now * 1.001
    body_down = price < lower_at_now * 0.999

    # Закругление снизу (чаша): нижняя граница — парабола-чаша, на
    # правом крае разворачивается вверх → лонг.
    if lo_curv > 0 and upper_flat and lo_slope > flat_threshold * 0.5:
        pattern = PatternType.ROUNDED_BOTTOM
        confidence = min(0.9, lower_line.r2 * 0.7 + 0.2)
        if body_up or upper_wick:
            breakout_dir = "up"
            confidence = min(0.95, confidence + 0.15)
        diagnostics["type"] = "rounded_bottom"
        diagnostics["signal"] = "long_on_breakout_up"

    # Закругление сверху (купол): верхняя — парабола-купол, на правом
    # крае вниз → шорт.
    elif up_curv < 0 and lower_flat and up_slope < -flat_threshold * 0.5:
        pattern = PatternType.ROUNDED_TOP
        confidence = min(0.9, upper_line.r2 * 0.7 + 0.2)
        if body_down or lower_wick:
            breakout_dir = "down"
            confidence = min(0.95, confidence + 0.15)
        diagnostics["type"] = "rounded_top"
        diagnostics["signal"] = "short_on_breakdown_down"

    # Восходящий треугольник: верхняя плоская, нижняя вверх
    elif upper_flat and lo_slope > flat_threshold:
        pattern = PatternType.ASCENDING_TRIANGLE
        confidence = min(upper_line.r2, lower_line.r2) * 0.8 + 0.2
        if upper_wick:
            breakout_dir = "up"
            confidence = min(0.95, confidence + (0.2 if body_up else 0.1))
        diagnostics["type"] = "ascending_triangle"

    # Нисходящий треугольник: нижняя плоская, верхняя вниз
    elif lower_flat and up_slope < -flat_threshold:
        pattern = PatternType.DESCENDING_TRIANGLE
        confidence = min(upper_line.r2, lower_line.r2) * 0.8 + 0.2
        if lower_wick:
            breakout_dir = "down"
            confidence = min(0.95, confidence + (0.2 if body_down else 0.1))
        diagnostics["type"] = "descending_triangle"

    # Симметричный треугольник: верхняя вниз, нижняя вверх, сходятся
    elif up_slope < -flat_threshold and lo_slope > flat_threshold:
        upper_start = upper_line.value_at(float(recent_high_idx[0]))
        lower_start = lower_line.value_at(float(recent_low_idx[0]))
        upper_end = upper_line.value_at(float(recent_high_idx[-1]))
        lower_end = lower_line.value_at(float(recent_low_idx[-1]))
        start_dist = upper_start - lower_start
        end_dist = upper_end - lower_end
        if start_dist > 0 and end_dist > 0 and end_dist < start_dist * 0.8:
            pattern = PatternType.SYMMETRICAL_TRIANGLE
            confidence = min(upper_line.r2, lower_line.r2) * 0.7 + 0.15
            if upper_wick:
                breakout_dir = "up"
                confidence += 0.15
            elif lower_wick:
                breakout_dir = "down"
                confidence += 0.15
            diagnostics["type"] = "symmetrical_triangle"
            diagnostics["convergence"] = (start_dist - end_dist) / start_dist

    # Падающий клин: обе вниз, сходятся (верхняя круче вниз, чем нижняя)
    elif up_slope < -flat_threshold and lo_slope < -flat_threshold:
        if up_slope < lo_slope:
            pattern = PatternType.FALLING_WEDGE
            confidence = min(upper_line.r2, lower_line.r2) * 0.75 + 0.15
            if upper_wick:
                breakout_dir = "up"
                confidence = min(0.95, confidence + (0.2 if body_up else 0.1))
            diagnostics["type"] = "falling_wedge"
            diagnostics["signal"] = "long_on_breakout_up"

    # Восходящий клин: обе вверх, сходятся (нижняя круче вверх)
    elif up_slope > flat_threshold and lo_slope > flat_threshold:
        if lo_slope > up_slope:
            pattern = PatternType.RISING_WEDGE
            confidence = min(upper_line.r2, lower_line.r2) * 0.75 + 0.15
            if lower_wick:
                breakout_dir = "down"
                confidence = min(0.95, confidence + (0.2 if body_down else 0.1))
            diagnostics["type"] = "rising_wedge"
            diagnostics["signal"] = "short_on_breakdown_down"

    # Если не определили, но есть схождение — возможно клин
    if pattern == PatternType.NONE:
        try:
            upper_start = upper_line.value_at(float(recent_high_idx[0]))
            lower_start = lower_line.value_at(float(recent_low_idx[0]))
            upper_end = upper_line.value_at(float(recent_high_idx[-1]))
            lower_end = lower_line.value_at(float(recent_low_idx[-1]))
            if upper_start > lower_start and upper_end > lower_end:
                start_dist = upper_start - lower_start
                end_dist = upper_end - lower_end
                if end_dist < start_dist * 0.7 and end_dist > 0:
                    avg_slope = (up_slope + lo_slope) / 2
                    if avg_slope < -flat_threshold:
                        pattern = PatternType.FALLING_WEDGE
                        confidence = 0.6
                        if upper_wick:
                            breakout_dir = "up"
                    elif avg_slope > flat_threshold:
                        pattern = PatternType.RISING_WEDGE
                        confidence = 0.6
                        if lower_wick:
                            breakout_dir = "down"
        except Exception:
            pass

    # Правило «3 касания сверху + 3 снизу»: граница выверена рынком —
    # уверенность выше и пробой близко. Без подтверждения — штраф.
    if pattern != PatternType.NONE:
        if touches_confirmed:
            confidence = min(0.95, confidence + 0.1)
            diagnostics["touches_bonus"] = 0.1
        else:
            confidence = max(0.0, confidence - 0.05)
            diagnostics["touches_bonus"] = -0.05

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

    По ТЗ: клин вниз = лонг. Закругление снизу (чаша) — бычье
    закругление клина, тоже лонг.
    """

    name = "falling_wedge"

    # Бычьи фигуры, обрабатываемые стратегией (клин и его закругление).
    BULLISH_PATTERNS = frozenset({PatternType.FALLING_WEDGE, PatternType.ROUNDED_BOTTOM})

    async def evaluate(self, ctx: StrategyContext):
        candles = ctx.candles
        if len(candles) < 50:
            return None

        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]

        result = detect_pattern(highs, lows, closes)

        if result.pattern not in self.BULLISH_PATTERNS:
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

    # Медвежьи фигуры (клин и его закругление-купол).
    BEARISH_PATTERNS = frozenset({PatternType.RISING_WEDGE, PatternType.ROUNDED_TOP})

    async def evaluate(self, ctx: StrategyContext):
        candles = ctx.candles
        if len(candles) < 50:
            return None

        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]

        result = detect_pattern(highs, lows, closes)

        if result.pattern not in self.BEARISH_PATTERNS:
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
