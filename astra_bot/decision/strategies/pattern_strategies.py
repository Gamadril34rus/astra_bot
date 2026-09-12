"""
Chart Pattern Strategies — клинья, треугольники, двойные/тройные вершины/днища,
прямоугольники, расширяющиеся треугольники, флаги, вымпелы, ромбы, чаши с ручкой.

Основа по ТЗ пользователя.
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
    HEAD_SHOULDERS = "HEAD_SHOULDERS"  # Голова и плечи — шорт
    INVERTED_HEAD_SHOULDERS = "INVERTED_HEAD_SHOULDERS"  # Обратная ГП — лонг
    DOUBLE_TOP = "DOUBLE_TOP"  # Двойная вершина — шорт
    DOUBLE_BOTTOM = "DOUBLE_BOTTOM"  # Двойное дно — лонг
    TRIPLE_TOP = "TRIPLE_TOP"  # Тройная вершина — шорт
    TRIPLE_BOTTOM = "TRIPLE_BOTTOM"  # Тройное дно — лонг
    RECTANGLE = "RECTANGLE"  # Прямоугольник (канал) — лонг/шорт
    EXPANDING_TRIANGLE = "EXPANDING_TRIANGLE"  # Расходящийся треугольник — лонг/шорт
    FLAG = "FLAG"  # Флаг — бычий/медвежий
    PENNANT = "PENNANT"  # Вымпел — бычий/медвежий
    DIAMOND_TOP = "DIAMOND_TOP"  # Ромб вершина — шорт
    DIAMOND_BOTTOM = "DIAMOND_BOTTOM"  # Ромб дно — лонг
    CUP_AND_HANDLE = "CUP_AND_HANDLE"  # Чаша с ручкой — лонг
    INVERTED_CUP_AND_HANDLE = "INVERTED_CUP_AND_HANDLE"  # Перевёрнутая чаша — шорт
    NONE = "NONE"


# Минимальное число касаний границы (по ТЕНЯМ) для подтверждения линии.
MIN_TOUCHES_PER_LINE = 3


@dataclass
class TrendLine:
    slope: float
    intercept: float
    r2: float  # качество фита
    rmse: float = 0.0  # СКО остатков (для плоских линий R² вырождается)
    quad: tuple[float, float, float] | None = None
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
    """Линейная регрессия; при явной кривизне — квадратичная."""
    line = _linear_regression(x, y)
    if len(x) >= 4:
        quad = _quadratic_regression(x, y)
        if quad is not None and quad.r2 >= line.r2 + 0.05:
            return quad
    return line


def _quadratic_regression(x: list[float], y: list[float]) -> TrendLine | None:
    """МНК-фит y = a*x² + b*x + c через numpy.polyfit."""
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
        if highs[i] == max(highs[i - window : i + window + 1]):
            high_idx.append(i)
        if lows[i] == min(lows[i - window : i + window + 1]):
            low_idx.append(i)
    return high_idx, low_idx


def _collapse_plateaus(indices: list[int], gap: int = 3) -> list[int]:
    """Схлопнуть серии соседних свинг-индексов."""
    if not indices:
        return []
    out: list[int] = []
    run: list[int] = [indices[0]]
    for i in indices[1:]:
        if i - run[-1] <= gap:
            run.append(i)
        else:
            out.append(run[len(run) // 2])
            run = [i]
    out.append(run[len(run) // 2])
    return out


# ----------------------------------------------------------------------
# Функции детекции паттернов
# ----------------------------------------------------------------------


def detect_double_top_bottom(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Двойная вершина / Двойное дно (2 пика/впадины на одном уровне ±0.4%)."""
    try:
        n = len(highs)
        if n < 20:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough data"})

        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        high_idx = _collapse_plateaus(_find_swings(hist_highs, hist_lows, window=2)[0])
        low_idx = _collapse_plateaus(_find_swings(hist_highs, hist_lows, window=2)[1])
        price = closes[-1]

        # 1. Двойная Вершина (bearish)
        if len(high_idx) >= 2:
            for i in range(len(high_idx) - 1):
                p1, p2 = high_idx[i], high_idx[i + 1]
                v1, v2 = highs[p1], highs[p2]
                max_v = max(v1, v2)
                if max_v <= 0:
                    continue
                if abs(v1 - v2) / max_v <= 0.004:
                    between_lows = [lows[k] for k in range(p1, p2 + 1)]
                    if not between_lows:
                        continue
                    neckline = min(between_lows)
                    height = max_v - neckline
                    if height < price * 0.003:
                        continue

                    if price < neckline:
                        level_diff = abs(v1 - v2) / max_v
                        conf = 0.65 + (0.004 - level_diff) / 0.004 * 0.15
                        if price < neckline * 0.998:
                            conf += 0.1
                        conf = max(0.5, min(0.95, conf))

                        neck_line = TrendLine(slope=0.0, intercept=neckline, r2=1.0)
                        peaks_line = TrendLine(slope=0.0, intercept=max_v, r2=1.0)
                        return PatternResult(
                            pattern=PatternType.DOUBLE_TOP,
                            confidence=conf,
                            upper_line=peaks_line,
                            lower_line=neck_line,
                            breakout_direction="down",
                            diagnostics={
                                "type": "double_top",
                                "peak1": v1,
                                "peak2": v2,
                                "neckline": neckline,
                                "height": height,
                                "price": price,
                            },
                        )

        # 2. Двойное Дно (bullish)
        if len(low_idx) >= 2:
            for i in range(len(low_idx) - 1):
                t1, t2 = low_idx[i], low_idx[i + 1]
                v1, v2 = lows[t1], lows[t2]
                min_v = min(v1, v2)
                if min_v <= 0:
                    continue
                if abs(v1 - v2) / min_v <= 0.004:
                    between_highs = [highs[k] for k in range(t1, t2 + 1)]
                    if not between_highs:
                        continue
                    neckline = max(between_highs)
                    height = neckline - min_v
                    if height < price * 0.003:
                        continue

                    if price > neckline:
                        level_diff = abs(v1 - v2) / min_v
                        conf = 0.65 + (0.004 - level_diff) / 0.004 * 0.15
                        if price > neckline * 1.002:
                            conf += 0.1
                        conf = max(0.5, min(0.95, conf))

                        neck_line = TrendLine(slope=0.0, intercept=neckline, r2=1.0)
                        troughs_line = TrendLine(slope=0.0, intercept=min_v, r2=1.0)
                        return PatternResult(
                            pattern=PatternType.DOUBLE_BOTTOM,
                            confidence=conf,
                            upper_line=neck_line,
                            lower_line=troughs_line,
                            breakout_direction="up",
                            diagnostics={
                                "type": "double_bottom",
                                "trough1": v1,
                                "trough2": v2,
                                "neckline": neckline,
                                "height": height,
                                "price": price,
                            },
                        )

        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "no double top/bottom"})
    except Exception as e:
        logger.debug("detect_double_top_bottom exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_triple_top_bottom(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Тройная вершина / Тройное дно (3 пика/впадины на одном уровне ±0.4%)."""
    try:
        n = len(highs)
        if n < 25:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough data"})

        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        high_idx = _collapse_plateaus(_find_swings(hist_highs, hist_lows, window=2)[0])
        low_idx = _collapse_plateaus(_find_swings(hist_highs, hist_lows, window=2)[1])
        price = closes[-1]

        # 1. Тройная вершина
        if len(high_idx) >= 3:
            for i in range(len(high_idx) - 2):
                p1, p2, p3 = high_idx[i], high_idx[i + 1], high_idx[i + 2]
                v1, v2, v3 = highs[p1], highs[p2], highs[p3]
                max_v = max(v1, v2, v3)
                min_peak = min(v1, v2, v3)
                if max_v <= 0:
                    continue
                if (max_v - min_peak) / max_v <= 0.004:
                    between_lows = [lows[k] for k in range(p1, p3 + 1)]
                    if not between_lows:
                        continue
                    neckline = min(between_lows)
                    height = max_v - neckline
                    if height < price * 0.003:
                        continue
                    if price < neckline:
                        diff = (max_v - min_peak) / max_v
                        conf = 0.70 + (0.004 - diff) / 0.004 * 0.15
                        if price < neckline * 0.998:
                            conf += 0.1
                        conf = max(0.55, min(0.95, conf))

                        neck_line = TrendLine(slope=0.0, intercept=neckline, r2=1.0)
                        peaks_line = TrendLine(slope=0.0, intercept=max_v, r2=1.0)
                        return PatternResult(
                            pattern=PatternType.TRIPLE_TOP,
                            confidence=conf,
                            upper_line=peaks_line,
                            lower_line=neck_line,
                            breakout_direction="down",
                            diagnostics={
                                "type": "triple_top",
                                "peaks": [v1, v2, v3],
                                "neckline": neckline,
                                "height": height,
                                "price": price,
                            },
                        )

        # 2. Тройное дно
        if len(low_idx) >= 3:
            for i in range(len(low_idx) - 2):
                t1, t2, t3 = low_idx[i], low_idx[i + 1], low_idx[i + 2]
                v1, v2, v3 = lows[t1], lows[t2], lows[t3]
                max_trough = max(v1, v2, v3)
                min_v = min(v1, v2, v3)
                if min_v <= 0:
                    continue
                if (max_trough - min_v) / min_v <= 0.004:
                    between_highs = [highs[k] for k in range(t1, t3 + 1)]
                    if not between_highs:
                        continue
                    neckline = max(between_highs)
                    height = neckline - min_v
                    if height < price * 0.003:
                        continue
                    if price > neckline:
                        diff = (max_trough - min_v) / min_v
                        conf = 0.70 + (0.004 - diff) / 0.004 * 0.15
                        if price > neckline * 1.002:
                            conf += 0.1
                        conf = max(0.55, min(0.95, conf))

                        neck_line = TrendLine(slope=0.0, intercept=neckline, r2=1.0)
                        troughs_line = TrendLine(slope=0.0, intercept=min_v, r2=1.0)
                        return PatternResult(
                            pattern=PatternType.TRIPLE_BOTTOM,
                            confidence=conf,
                            upper_line=neck_line,
                            lower_line=troughs_line,
                            breakout_direction="up",
                            diagnostics={
                                "type": "triple_bottom",
                                "troughs": [v1, v2, v3],
                                "neckline": neckline,
                                "height": height,
                                "price": price,
                            },
                        )

        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "no triple top/bottom"})
    except Exception as e:
        logger.debug("detect_triple_top_bottom exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_rectangle(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Прямоугольник (параллельные горизонтальные поддержка и сопротивление, мин 2 касания)."""
    try:
        n = len(highs)
        if n < 20:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough data"})

        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        high_idx, low_idx = _find_swings(hist_highs, hist_lows, window=2)

        if len(high_idx) < 2 or len(low_idx) < 2:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough swings"})

        recent_highs = [hist_highs[i] for i in high_idx[-6:]]
        recent_lows = [hist_lows[i] for i in low_idx[-6:]]

        res_level = sum(recent_highs) / len(recent_highs)
        sup_level = sum(recent_lows) / len(recent_lows)

        if res_level <= sup_level:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "invalid channel levels"})

        height = res_level - sup_level
        price = closes[-1]
        if height < price * 0.003:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "channel too narrow"})

        tol = max(price * 0.003, height * 0.1)
        upper_touches = sum(1 for v in recent_highs if abs(v - res_level) <= tol)
        lower_touches = sum(1 for v in recent_lows if abs(v - sup_level) <= tol)

        if upper_touches < 2 or lower_touches < 2:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough touches"})

        upper_line = TrendLine(slope=0.0, intercept=res_level, r2=0.9)
        lower_line = TrendLine(slope=0.0, intercept=sup_level, r2=0.9)

        breakout_dir = None
        conf = 0.60 + min(0.2, 0.03 * (upper_touches + lower_touches))

        if price > res_level:
            breakout_dir = "up"
            conf = min(0.95, conf + 0.1)
        elif price < sup_level:
            breakout_dir = "down"
            conf = min(0.95, conf + 0.1)
        else:
            return PatternResult(PatternType.NONE, 0.0, upper_line, lower_line, None, {"reason": "no breakout"})

        return PatternResult(
            pattern=PatternType.RECTANGLE,
            confidence=conf,
            upper_line=upper_line,
            lower_line=lower_line,
            breakout_direction=breakout_dir,
            diagnostics={
                "type": "rectangle",
                "resistance": res_level,
                "support": sup_level,
                "height": height,
                "upper_touches": upper_touches,
                "lower_touches": lower_touches,
                "price": price,
            },
        )
    except Exception as e:
        logger.debug("detect_rectangle exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_expanding_triangle(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Расходящийся треугольник (верхняя вверх, нижняя вниз)."""
    try:
        n = len(highs)
        if n < 20:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough data"})

        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        high_idx, low_idx = _find_swings(hist_highs, hist_lows, window=2)

        if len(high_idx) < 2 or len(low_idx) < 2:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough swings"})

        recent_h_idx = high_idx[-4:]
        recent_l_idx = low_idx[-4:]

        upper_line = _fit_line([float(i) for i in recent_h_idx], [hist_highs[i] for i in recent_h_idx])
        lower_line = _fit_line([float(i) for i in recent_l_idx], [hist_lows[i] for i in recent_l_idx])

        price = closes[-1]
        flat_thresh = price * 0.0003

        if upper_line.slope <= flat_thresh or lower_line.slope >= -flat_thresh:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "lines not expanding"})

        start_idx = float(min(recent_h_idx[0], recent_l_idx[0]))
        end_idx = float(n - 1)
        start_dist = upper_line.value_at(start_idx) - lower_line.value_at(start_idx)
        end_dist = upper_line.value_at(end_idx) - lower_line.value_at(end_idx)

        if end_dist <= start_dist * 1.1:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "insufficient expansion"})

        upper_at_now = upper_line.value_at(end_idx)
        lower_at_now = lower_line.value_at(end_idx)

        breakout_dir = None
        conf = min(0.9, (upper_line.r2 + lower_line.r2) * 0.4 + 0.20)

        if price > upper_at_now:
            breakout_dir = "up"
            conf = min(0.95, conf + 0.1)
        elif price < lower_at_now:
            breakout_dir = "down"
            conf = min(0.95, conf + 0.1)
        else:
            return PatternResult(PatternType.NONE, 0.0, upper_line, lower_line, None, {"reason": "no breakout"})

        return PatternResult(
            pattern=PatternType.EXPANDING_TRIANGLE,
            confidence=conf,
            upper_line=upper_line,
            lower_line=lower_line,
            breakout_direction=breakout_dir,
            diagnostics={
                "type": "expanding_triangle",
                "upper_slope": upper_line.slope,
                "lower_slope": lower_line.slope,
                "upper_at_now": upper_at_now,
                "lower_at_now": lower_at_now,
                "height": end_dist,
                "price": price,
            },
        )
    except Exception as e:
        logger.debug("detect_expanding_triangle exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_flag(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Флаг (флагшток >=3% за <=5 баров + узкий наклонный канал против импульса)."""
    try:
        n = len(highs)
        if n < 20:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough data"})

        price = closes[-1]
        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        hist_closes = closes[:-1]
        m = len(hist_closes)

        best_flagpole = None
        for pole_end in range(5, m - 3):
            for pole_len in range(2, 6):
                pole_start = pole_end - pole_len
                if pole_start < 0:
                    continue
                start_p = hist_lows[pole_start]
                end_p = hist_highs[pole_end]
                if start_p > 0 and (end_p - start_p) / start_p >= 0.03:
                    best_flagpole = ("bull", pole_start, pole_end, end_p - start_p)
                    break
                start_p = hist_highs[pole_start]
                end_p = hist_lows[pole_end]
                if start_p > 0 and (start_p - end_p) / start_p >= 0.03:
                    best_flagpole = ("bear", pole_start, pole_end, start_p - end_p)
                    break
            if best_flagpole is not None:
                break

        if best_flagpole is None:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "no flagpole found"})

        direction, pole_start, pole_end, pole_height = best_flagpole
        cons_highs = hist_highs[pole_end:]
        cons_lows = hist_lows[pole_end:]
        cons_len = len(cons_highs)
        if cons_len < 3:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "consolidation too short"})

        cons_x = [float(pole_end + i) for i in range(cons_len)]
        upper_line = _fit_line(cons_x, cons_highs)
        lower_line = _fit_line(cons_x, cons_lows)

        last_idx = float(n - 1)
        upper_at_now = upper_line.value_at(last_idx)
        lower_at_now = lower_line.value_at(last_idx)
        channel_height = upper_at_now - lower_at_now

        if channel_height > pole_height * 0.6 or channel_height <= 0:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "consolidation channel too wide"})

        breakout_dir = None
        conf = 0.65 + min(0.15, (pole_height / price) * 5.0)

        if direction == "bull":
            if upper_line.slope <= price * 0.0005:
                if price > upper_at_now:
                    breakout_dir = "up"
                    conf = min(0.95, conf + 0.15)
        else:
            if lower_line.slope >= -price * 0.0005:
                if price < lower_at_now:
                    breakout_dir = "down"
                    conf = min(0.95, conf + 0.15)

        if breakout_dir is None:
            return PatternResult(PatternType.NONE, 0.0, upper_line, lower_line, None, {"reason": "no flag breakout"})

        return PatternResult(
            pattern=PatternType.FLAG,
            confidence=conf,
            upper_line=upper_line,
            lower_line=lower_line,
            breakout_direction=breakout_dir,
            diagnostics={
                "type": "flag",
                "flag_type": direction,
                "pole_height": pole_height,
                "channel_height": channel_height,
                "upper_at_now": upper_at_now,
                "lower_at_now": lower_at_now,
                "price": price,
            },
        )
    except Exception as e:
        logger.debug("detect_flag exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_pennant(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Вымпел (флагшток + маленький симметричный треугольник)."""
    try:
        n = len(highs)
        if n < 20:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough data"})

        price = closes[-1]
        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        hist_closes = closes[:-1]
        m = len(hist_closes)

        best_flagpole = None
        for pole_end in range(5, m - 3):
            for pole_len in range(2, 6):
                pole_start = pole_end - pole_len
                if pole_start < 0:
                    continue
                start_p = hist_lows[pole_start]
                end_p = hist_highs[pole_end]
                if start_p > 0 and (end_p - start_p) / start_p >= 0.03:
                    best_flagpole = ("bull", pole_start, pole_end, end_p - start_p)
                    break
                start_p = hist_highs[pole_start]
                end_p = hist_lows[pole_end]
                if start_p > 0 and (start_p - end_p) / start_p >= 0.03:
                    best_flagpole = ("bear", pole_start, pole_end, start_p - end_p)
                    break
            if best_flagpole is not None:
                break

        if best_flagpole is None:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "no flagpole found"})

        direction, pole_start, pole_end, pole_height = best_flagpole
        cons_highs = hist_highs[pole_end:]
        cons_lows = hist_lows[pole_end:]
        cons_len = len(cons_highs)
        if cons_len < 3:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "pennant too short"})

        cons_x = [float(pole_end + i) for i in range(cons_len)]
        upper_line = _fit_line(cons_x, cons_highs)
        lower_line = _fit_line(cons_x, cons_lows)

        flat_thresh = price * 0.0001
        if upper_line.slope >= flat_thresh or lower_line.slope <= -flat_thresh:
            # Разрешаем пологую верхнюю/нижнюю границу вымпела если схождение сильное
            start_dist = upper_line.value_at(float(pole_end)) - lower_line.value_at(float(pole_end))
            end_dist = upper_line.value_at(float(m - 1)) - lower_line.value_at(float(m - 1))
            if not (start_dist > 0 and end_dist > 0 and end_dist < start_dist * 0.9):
                return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "lines not converging for pennant"})

        last_idx = float(n - 1)
        upper_at_now = upper_line.value_at(last_idx)
        lower_at_now = lower_line.value_at(last_idx)
        pennant_height = upper_line.value_at(float(pole_end)) - lower_line.value_at(float(pole_end))

        breakout_dir = None
        conf = 0.65 + min(0.15, (pole_height / price) * 5.0)

        if direction == "bull" and price > upper_at_now:
            breakout_dir = "up"
            conf = min(0.95, conf + 0.15)
        elif direction == "bear" and price < lower_at_now:
            breakout_dir = "down"
            conf = min(0.95, conf + 0.15)

        if breakout_dir is None:
            return PatternResult(PatternType.NONE, 0.0, upper_line, lower_line, None, {"reason": "no pennant breakout"})

        return PatternResult(
            pattern=PatternType.PENNANT,
            confidence=conf,
            upper_line=upper_line,
            lower_line=lower_line,
            breakout_direction=breakout_dir,
            diagnostics={
                "type": "pennant",
                "pennant_type": direction,
                "pole_height": pole_height,
                "pennant_height": pennant_height,
                "upper_at_now": upper_at_now,
                "lower_at_now": lower_at_now,
                "price": price,
            },
        )
    except Exception as e:
        logger.debug("detect_pennant exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_diamond(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Ромб (Diamond Top / Diamond Bottom)."""
    try:
        n = len(highs)
        if n < 30:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough data"})

        price = closes[-1]
        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        m = len(hist_highs)

        mid = m // 2
        window = min(mid - 5, 20)
        if window < 5:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "window too small"})

        spreads = [hist_highs[i] - hist_lows[i] for i in range(m - 2 * window, m)]
        if not spreads:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "no spreads"})

        max_spread_idx = m - 2 * window + spreads.index(max(spreads))
        if max_spread_idx <= m - 20 or max_spread_idx >= m - 3:
            max_spread_idx = m - 10

        left_h = hist_highs[max_spread_idx - 8 : max_spread_idx]
        right_h = hist_highs[max_spread_idx:m]
        right_l = hist_lows[max_spread_idx:m]

        if len(right_h) < 3 or len(left_h) < 3:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "sides too short"})

        right_x = [float(max_spread_idx + i) for i in range(len(right_h))]
        upper_line = _fit_line(right_x, right_h)
        lower_line = _fit_line(right_x, right_l)

        diamond_height = hist_highs[max_spread_idx] - hist_lows[max_spread_idx]
        if diamond_height < price * 0.005:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "diamond height too small"})

        last_idx = float(n - 1)
        upper_at_now = upper_line.value_at(last_idx)
        lower_at_now = lower_line.value_at(last_idx)

        breakout_dir = None
        pattern_type = PatternType.NONE
        conf = 0.65

        if price > upper_at_now:
            breakout_dir = "up"
            pattern_type = PatternType.DIAMOND_BOTTOM
            conf = 0.75
        elif price < lower_at_now:
            breakout_dir = "down"
            pattern_type = PatternType.DIAMOND_TOP
            conf = 0.75

        if breakout_dir is None:
            return PatternResult(PatternType.NONE, 0.0, upper_line, lower_line, None, {"reason": "no diamond breakout"})

        return PatternResult(
            pattern=pattern_type,
            confidence=conf,
            upper_line=upper_line,
            lower_line=lower_line,
            breakout_direction=breakout_dir,
            diagnostics={
                "type": "diamond",
                "diamond_height": diamond_height,
                "upper_at_now": upper_at_now,
                "lower_at_now": lower_at_now,
                "price": price,
            },
        )
    except Exception as e:
        logger.debug("detect_diamond exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_cup_and_handle(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Чаша и ручка (обычная, перевёрнутая, с ручкой / без ручки)."""
    try:
        n = len(highs)
        if n < 30:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "not enough data"})

        price = closes[-1]
        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        m = len(hist_lows)

        # Оцениваем фит чаши без ручки (первые m - h_len баров)
        best_cup = None
        for h_len in (0, 3, 5, 8):
            cup_m = m - h_len
            if cup_m < 20:
                continue
            cup_window = min(cup_m, 35)
            c_lows = hist_lows[cup_m - cup_window : cup_m]
            c_highs = hist_highs[cup_m - cup_window : cup_m]
            norm_x = [float(i) for i in range(len(c_lows))]

            quad_low = _quadratic_regression(norm_x, c_lows)
            quad_high = _quadratic_regression(norm_x, c_highs)

            # 1. Обычная чаша (выпукла вниз)
            if quad_low is not None and quad_low.curvature > 0 and quad_low.r2 >= 0.4:
                left_lip = max(c_highs[: len(c_highs) // 3])
                right_lip = max(c_highs[2 * len(c_highs) // 3 :])
                rim_level = (left_lip + right_lip) / 2.0
                cup_bottom = min(c_lows)
                cup_depth = rim_level - cup_bottom

                if cup_depth >= price * 0.005 and price > rim_level * 0.998:
                    has_handle = h_len > 0
                    conf = 0.70 + (0.10 if has_handle else 0.0) + min(0.15, quad_low.r2 * 0.2)
                    best_cup = (PatternType.CUP_AND_HANDLE, "up", conf, rim_level, cup_depth)
                    break

            # 2. Перевёрнутая чаша (выпукла вверх)
            elif quad_high is not None and quad_high.curvature < 0 and quad_high.r2 >= 0.4:
                left_lip = min(c_lows[: len(c_lows) // 3])
                right_lip = min(c_lows[2 * len(c_lows) // 3 :])
                rim_level = (left_lip + right_lip) / 2.0
                cup_top = max(c_highs)
                cup_depth = cup_top - rim_level

                if cup_depth >= price * 0.005 and price < rim_level * 1.002:
                    has_handle = h_len > 0
                    conf = 0.70 + (0.10 if has_handle else 0.0) + min(0.15, quad_high.r2 * 0.2)
                    best_cup = (PatternType.INVERTED_CUP_AND_HANDLE, "down", conf, rim_level, cup_depth)
                    break

        if best_cup is None:
            return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": "no cup and handle structure"})

        pattern_type, breakout_dir, conf, rim_level, cup_depth = best_cup
        rim_line = TrendLine(slope=0.0, intercept=rim_level, r2=0.8)
        return PatternResult(
            pattern=pattern_type,
            confidence=conf,
            upper_line=rim_line if breakout_dir == "up" else None,
            lower_line=rim_line if breakout_dir == "down" else None,
            breakout_direction=breakout_dir,
            diagnostics={
                "type": "cup_and_handle",
                "rim_level": rim_level,
                "cup_depth": cup_depth,
                "price": price,
            },
        )
    except Exception as e:
        logger.debug("detect_cup_and_handle exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_head_shoulders(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Голова и плечи (и обратная) — разворотные паттерны."""
    try:
        n = len(highs)
        if n < 30:
            return PatternResult(PatternType.NONE, 0.0, None, None, None,
                                 {"reason": "not enough data"})
        high_idx = _collapse_plateaus(_find_swings(highs, lows, window=3)[0])
        low_idx = _collapse_plateaus(_find_swings(highs, lows, window=3)[1])
        price = closes[-1]

        def _neck_touches(neck, swing_indices, use_lows: bool) -> int:
            tol = price * 0.003
            touches = 0
            for i in swing_indices:
                wick = lows[i] if use_lows else highs[i]
                if abs(wick - neck.value_at(float(i))) <= tol:
                    touches += 1
            return touches

        best: dict[str, object] = {}

        def _emit(inverted: bool, a: int, b: int, c: int, t1: int, t2: int) -> None:
            if not (a < t1 < b < t2 < c):
                return
            if inverted:
                ls, head, rs = lows[a], lows[b], lows[c]
                if not (head < ls and head < rs):
                    return
                prominence = min(ls, rs) - head
                t_vals = [highs[t1], highs[t2]]
            else:
                ls, head, rs = highs[a], highs[b], highs[c]
                if not (head > ls and head > rs):
                    return
                prominence = head - max(ls, rs)
                t_vals = [lows[t1], lows[t2]]
            if prominence <= price * 0.004:
                return
            shoulder_diff = abs(ls - rs)
            if shoulder_diff > 0.4 * prominence + price * 0.003:
                return
            neck = _fit_line([float(t1), float(t2)], t_vals)
            neck_now = neck.value_at(float(n - 1))
            rel_idx = [i for i in (low_idx if not inverted else high_idx)
                       if a - 5 <= i <= n]
            touches = _neck_touches(neck, rel_idx, use_lows=not inverted)
            conf = 0.45
            conf += 0.15 * min(1.0, prominence / (price * 0.02))
            conf += 0.10 * (1.0 - min(1.0, shoulder_diff / (0.4 * prominence)))
            if touches >= 2:
                conf += 0.10
            if inverted:
                wick_break = lows[-1] > neck_now
                close_break = price > neck_now
                breakout = (
                    "up" if close_break else ("early_up" if wick_break else None)
                )
            else:
                wick_break = lows[-1] < neck_now
                close_break = price < neck_now
                breakout = (
                    "down" if close_break else ("early_down" if wick_break else None)
                )
            if breakout is None:
                conf -= 0.05
            elif str(breakout).startswith("early"):
                conf += 0.10
            else:
                conf += 0.20
            conf = max(0.0, min(0.95, conf))
            if c < n - 40:
                return
            peaks_line = _fit_line([float(a), float(b), float(c)],
                                   [ls, head, rs])
            ptype = (PatternType.INVERTED_HEAD_SHOULDERS if inverted
                     else PatternType.HEAD_SHOULDERS)
            res = PatternResult(
                pattern=ptype,
                confidence=conf,
                upper_line=None if inverted else peaks_line,
                lower_line=neck if not inverted else peaks_line,
                breakout_direction="up" if inverted else "down",
                diagnostics={
                    "type": ("inverted_head_shoulders" if inverted
                             else "head_shoulders"),
                    "signal": ("long_on_neckline_break_up" if inverted
                               else "short_on_neckline_break_down"),
                    "head": head,
                    "left_shoulder": ls,
                    "right_shoulder": rs,
                    "neckline_at_now": neck_now,
                    "neckline_touches": touches,
                    "breakout": breakout,
                    "price": price,
                },
            )
            if not best or res.confidence > best["res"].confidence:
                best["res"] = res

        for swing_idx, other_idx, inverted in (
            (high_idx, low_idx, False),
            (low_idx, high_idx, True),
        ):
            for offset in (3, 4):
                if len(swing_idx) < offset:
                    continue
                a = swing_idx[-offset]
                b = swing_idx[-offset + 1]
                c = swing_idx[-offset + 2]
                between = [i for i in other_idx if a < i < c]
                if len(between) < 2:
                    continue
                _emit(inverted=inverted, a=a, b=b, c=c,
                      t1=between[0], t2=between[-1])

        if best:
            return best["res"]  # type: ignore[return-value]
        return PatternResult(PatternType.NONE, 0.0, None, None, None,
                             {"reason": "no head-shoulders structure"})
    except Exception as e:
        logger.debug("detect_head_shoulders exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


def detect_pattern(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    min_swings: int = 3,
) -> PatternResult:
    """Детекция паттернов клин/треугольник."""
    try:
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

        recent_high_idx = high_idx[-5:]
        recent_low_idx = low_idx[-5:]

        recent_high_vals = [highs[i] for i in recent_high_idx]
        recent_low_vals = [lows[i] for i in recent_low_idx]

        upper_line = _fit_line([float(i) for i in recent_high_idx], recent_high_vals)
        lower_line = _fit_line([float(i) for i in recent_low_idx], recent_low_vals)

        price = closes[-1]
        last_idx = float(len(highs) - 1)
        upper_at_now = upper_line.value_at(last_idx)
        lower_at_now = lower_line.value_at(last_idx)

        def _count_touches(line: TrendLine, idxs: list[int], values: list[float]) -> int:
            tol = max(line.rmse * 1.5, price * 0.003)
            touches = 0
            for i, v in zip(idxs, values, strict=False):
                if abs(v - line.value_at(float(i))) <= tol:
                    touches += 1
            return touches

        upper_touches = _count_touches(upper_line, high_idx, [highs[i] for i in high_idx])
        lower_touches = _count_touches(lower_line, low_idx, [lows[i] for i in low_idx])
        touches_confirmed = (
            upper_touches >= MIN_TOUCHES_PER_LINE and lower_touches >= MIN_TOUCHES_PER_LINE
        )

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

        def _line_is_good(line: TrendLine) -> bool:
            return line.r2 >= 0.5 or line.rmse <= price * 0.003

        if not (_line_is_good(upper_line) and _line_is_good(lower_line)):
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

        flat_threshold = price * 0.0005

        up_slope = upper_line.slope_at(last_idx)
        lo_slope = lower_line.slope_at(last_idx)
        up_curv = upper_line.curvature
        lo_curv = lower_line.curvature

        upper_flat = abs(up_slope) < flat_threshold
        lower_flat = abs(lo_slope) < flat_threshold

        pattern = PatternType.NONE
        confidence = 0.0
        breakout_dir = None

        upper_wick = highs[-1] > upper_at_now * 1.001 or price > upper_at_now * 1.001
        lower_wick = lows[-1] < lower_at_now * 0.999 or price < lower_at_now * 0.999
        body_up = price > upper_at_now * 1.001
        body_down = price < lower_at_now * 0.999

        if lo_curv > 0 and upper_flat and lo_slope > flat_threshold * 0.5:
            pattern = PatternType.ROUNDED_BOTTOM
            confidence = min(0.9, lower_line.r2 * 0.7 + 0.2)
            if body_up or upper_wick:
                breakout_dir = "up"
                confidence = min(0.95, confidence + 0.15)
            diagnostics["type"] = "rounded_bottom"
            diagnostics["signal"] = "long_on_breakout_up"

        elif up_curv < 0 and lower_flat and up_slope < -flat_threshold * 0.5:
            pattern = PatternType.ROUNDED_TOP
            confidence = min(0.9, upper_line.r2 * 0.7 + 0.2)
            if body_down or lower_wick:
                breakout_dir = "down"
                confidence = min(0.95, confidence + 0.15)
            diagnostics["type"] = "rounded_top"
            diagnostics["signal"] = "short_on_breakdown_down"

        elif upper_flat and lo_slope > flat_threshold:
            pattern = PatternType.ASCENDING_TRIANGLE
            confidence = min(upper_line.r2, lower_line.r2) * 0.8 + 0.2
            if upper_wick:
                breakout_dir = "up"
                confidence = min(0.95, confidence + (0.2 if body_up else 0.1))
            diagnostics["type"] = "ascending_triangle"

        elif lower_flat and up_slope < -flat_threshold:
            pattern = PatternType.DESCENDING_TRIANGLE
            confidence = min(upper_line.r2, lower_line.r2) * 0.8 + 0.2
            if lower_wick:
                breakout_dir = "down"
                confidence = min(0.95, confidence + (0.2 if body_down else 0.1))
            diagnostics["type"] = "descending_triangle"

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

        elif up_slope < -flat_threshold and lo_slope < -flat_threshold:
            if up_slope < lo_slope:
                pattern = PatternType.FALLING_WEDGE
                confidence = min(upper_line.r2, lower_line.r2) * 0.75 + 0.15
                if upper_wick:
                    breakout_dir = "up"
                    confidence = min(0.95, confidence + (0.2 if body_up else 0.1))
                diagnostics["type"] = "falling_wedge"
                diagnostics["signal"] = "long_on_breakout_up"

        elif up_slope > flat_threshold and lo_slope > flat_threshold:
            if lo_slope > up_slope:
                pattern = PatternType.RISING_WEDGE
                confidence = min(upper_line.r2, lower_line.r2) * 0.75 + 0.15
                if lower_wick:
                    breakout_dir = "down"
                    confidence = min(0.95, confidence + (0.2 if body_down else 0.1))
                diagnostics["type"] = "rising_wedge"
                diagnostics["signal"] = "short_on_breakdown_down"

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
    except Exception as e:
        logger.debug("detect_pattern exception: %s", e)
        return PatternResult(PatternType.NONE, 0.0, None, None, None, {"reason": str(e)})


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
    """Падающий клин — бычий паттерн, лонг на пробое вверх."""

    name = "falling_wedge"
    BULLISH_PATTERNS = frozenset({PatternType.FALLING_WEDGE, PatternType.ROUNDED_BOTTOM})

    async def evaluate(self, ctx: StrategyContext):
        try:
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

            if result.breakout_direction != "up" and result.confidence < 0.7:
                if result.diagnostics.get("price", 0) < result.diagnostics.get("upper_at_now", 0) * 0.995:
                    return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            lower_at_now = result.diagnostics.get("lower_at_now", price * 0.99)
            risk = price - lower_at_now
            if risk <= 0:
                risk = price * 0.01
            sl = price - risk * 1.2
            tp = price + max(risk * 2.5, abs(price - sl) * 1.6)

            if (tp - price) / abs(price - sl) < 1.5:
                return None

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
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class RisingWedgeStrategy(BasePatternStrategy):
    """Восходящий клин — медвежий паттерн, шорт на пробое вниз."""

    name = "rising_wedge"
    BEARISH_PATTERNS = frozenset({PatternType.RISING_WEDGE, PatternType.ROUNDED_TOP})

    async def evaluate(self, ctx: StrategyContext):
        try:
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
            tp = price - max(risk * 2.5, abs(sl - price) * 1.6)

            if abs(price - tp) / abs(sl - price) < 1.5:
                return None

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
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class AscendingTriangleStrategy(BasePatternStrategy):
    """Восходящий треугольник — бычий, лонг на пробое вверх."""

    name = "ascending_triangle"

    async def evaluate(self, ctx: StrategyContext):
        try:
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

            if result.breakout_direction != "up":
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

            sl = price - risk
            tp = price + risk * 2.0

            if (tp - price) / abs(price - sl) < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction="long",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=result.confidence,
                features={"pattern": result.pattern.value, "breakout": result.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class DescendingTriangleStrategy(BasePatternStrategy):
    """Нисходящий треугольник — медвежий, шорт на пробое вниз."""

    name = "descending_triangle"

    async def evaluate(self, ctx: StrategyContext):
        try:
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

            sl = price + risk
            tp = price - risk * 2.0

            if abs(price - tp) / abs(sl - price) < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction="short",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=result.confidence,
                features={"pattern": result.pattern.value, "breakout": result.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class SymmetricalTriangleStrategy(BasePatternStrategy):
    """Симметричный треугольник — пробой в любую сторону."""

    name = "symmetrical_triangle"

    async def evaluate(self, ctx: StrategyContext):
        try:
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
                sl = price - risk
                tp = price + risk * 2.0
                if (tp - price) / abs(price - sl) < 1.5:
                    return None
                return SignalCandidate(
                    symbol=ctx.symbol,
                    direction="long",
                    entry_price=Decimal(str(price)),
                    stop_loss=Decimal(str(sl)),
                    take_profit=Decimal(str(tp)),
                    timeframe=ctx.timeframe,
                    strategy=self.name,
                    confidence=result.confidence,
                    features={"pattern": result.pattern.value, "breakout": "up"},
                )
            else:
                sl = price + risk
                tp = price - risk * 2.0
                if abs(price - tp) / abs(sl - price) < 1.5:
                    return None
                return SignalCandidate(
                    symbol=ctx.symbol,
                    direction="short",
                    entry_price=Decimal(str(price)),
                    stop_loss=Decimal(str(sl)),
                    take_profit=Decimal(str(tp)),
                    timeframe=ctx.timeframe,
                    strategy=self.name,
                    confidence=result.confidence,
                    features={"pattern": result.pattern.value, "breakout": "down"},
                )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class HeadShouldersStrategy(BasePatternStrategy):
    """Голова и плечи (и обратная ГП)."""

    name = "head_shoulders"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 40:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            result = detect_head_shoulders(highs, lows, closes)
            if result.pattern == PatternType.HEAD_SHOULDERS:
                direction = "short"
            elif result.pattern == PatternType.INVERTED_HEAD_SHOULDERS:
                direction = "long"
            else:
                return None

            if result.confidence < 0.5:
                return None
            breakout = result.diagnostics.get("breakout")
            if breakout is None:
                return None
            if str(breakout).startswith("early") and result.confidence < 0.65:
                return None
            if not self._check_volume(candles):
                return None

            price = closes[-1]
            neck = float(result.diagnostics.get("neckline_at_now", price))
            head = float(result.diagnostics.get("head", price))
            rs = float(result.diagnostics.get("right_shoulder", price))
            if direction == "short":
                sl = rs + 0.5 * max(head - rs, price * 0.005)
                measured = max(head - neck, price * 0.01)
                risk = sl - price
                tp = price - max(measured, risk * 1.6)
                reward = price - tp
            else:
                sl = rs - 0.5 * max(rs - head, price * 0.005)
                measured = max(neck - head, price * 0.01)
                risk = price - sl
                tp = price + max(measured, risk * 1.6)
                reward = tp - price

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=result.confidence,
                features={
                    "pattern": result.pattern.value,
                    "breakout": breakout,
                    "neckline": neck,
                    "head": head,
                    "neckline_touches": result.diagnostics.get("neckline_touches", 0),
                },
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class DoubleTopBottomStrategy(BasePatternStrategy):
    """Двойная вершина / Двойное дно."""

    name = "double_top_bottom"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 30:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            res = detect_double_top_bottom(highs, lows, closes)
            if res.pattern == PatternType.NONE or res.confidence < 0.5:
                return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            diag = res.diagnostics
            height = diag.get("height", price * 0.01)

            if res.pattern == PatternType.DOUBLE_TOP:
                direction = "short"
                sl = max(diag.get("peak1", price), diag.get("peak2", price)) + price * 0.001
                risk = sl - price
                tp = price - max(height, risk * 1.6)
                reward = price - tp
            else:
                direction = "long"
                sl = min(diag.get("trough1", price), diag.get("trough2", price)) - price * 0.001
                risk = price - sl
                tp = price + max(height, risk * 1.6)
                reward = tp - price

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=res.confidence,
                features={"pattern": res.pattern.value, "breakout": res.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class TripleTopBottomStrategy(BasePatternStrategy):
    """Тройная вершина / Тройное дно."""

    name = "triple_top_bottom"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 35:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            res = detect_triple_top_bottom(highs, lows, closes)
            if res.pattern == PatternType.NONE or res.confidence < 0.5:
                return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            diag = res.diagnostics
            height = diag.get("height", price * 0.01)

            if res.pattern == PatternType.TRIPLE_TOP:
                direction = "short"
                peaks = diag.get("peaks", [price])
                sl = max(peaks) + price * 0.001
                risk = sl - price
                tp = price - max(height, risk * 1.6)
                reward = price - tp
            else:
                direction = "long"
                troughs = diag.get("troughs", [price])
                sl = min(troughs) - price * 0.001
                risk = price - sl
                tp = price + max(height, risk * 1.6)
                reward = tp - price

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=res.confidence,
                features={"pattern": res.pattern.value, "breakout": res.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class RectangleStrategy(BasePatternStrategy):
    """Прямоугольник (горизонтальный канал)."""

    name = "rectangle"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 30:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            res = detect_rectangle(highs, lows, closes)
            if res.pattern == PatternType.NONE or res.confidence < 0.5:
                return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            diag = res.diagnostics
            res_level = diag.get("resistance", price * 1.01)
            sup_level = diag.get("support", price * 0.99)
            height = diag.get("height", price * 0.01)

            if res.breakout_direction == "up":
                direction = "long"
                sl = sup_level
                risk = price - sl
                tp = price + max(height, risk * 1.6)
                reward = tp - price
                if risk > 0 and reward / risk < 1.5:
                    sl = price - reward / 1.6
                    risk = price - sl
            else:
                direction = "short"
                sl = res_level
                risk = sl - price
                tp = price - max(height, risk * 1.6)
                reward = price - tp
                if risk > 0 and reward / risk < 1.5:
                    sl = price + reward / 1.6
                    risk = sl - price

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=res.confidence,
                features={"pattern": res.pattern.value, "breakout": res.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class ExpandingTriangleStrategy(BasePatternStrategy):
    """Расходящийся треугольник."""

    name = "expanding_triangle"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 30:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            res = detect_expanding_triangle(highs, lows, closes)
            if res.pattern == PatternType.NONE or res.confidence < 0.5:
                return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            diag = res.diagnostics
            upper_at_now = diag.get("upper_at_now", price * 1.01)
            lower_at_now = diag.get("lower_at_now", price * 0.99)
            height = diag.get("height", price * 0.01)

            if res.breakout_direction == "up":
                direction = "long"
                sl = lower_at_now
                risk = price - sl
                tp = price + max(height, risk * 1.6)
                reward = tp - price
                if risk > 0 and reward / risk < 1.5:
                    sl = price - reward / 1.6
                    risk = price - sl
            else:
                direction = "short"
                sl = upper_at_now
                risk = sl - price
                tp = price - max(height, risk * 1.6)
                reward = price - tp
                if risk > 0 and reward / risk < 1.5:
                    sl = price + reward / 1.6
                    risk = sl - price

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=res.confidence,
                features={"pattern": res.pattern.value, "breakout": res.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class FlagStrategy(BasePatternStrategy):
    """Флаг (бычий / медвежий)."""

    name = "flag"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 30:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            res = detect_flag(highs, lows, closes)
            if res.pattern == PatternType.NONE or res.confidence < 0.5:
                return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            diag = res.diagnostics
            pole_height = diag.get("pole_height", price * 0.03)
            upper_at_now = diag.get("upper_at_now", price * 1.005)
            lower_at_now = diag.get("lower_at_now", price * 0.995)

            if res.breakout_direction == "up":
                direction = "long"
                sl = lower_at_now
                risk = price - sl
                tp = price + max(pole_height, risk * 1.6)
                reward = tp - price
            else:
                direction = "short"
                sl = upper_at_now
                risk = sl - price
                tp = price - max(pole_height, risk * 1.6)
                reward = price - tp

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=res.confidence,
                features={"pattern": res.pattern.value, "breakout": res.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class PennantStrategy(BasePatternStrategy):
    """Вымпел (бычий / медвежий)."""

    name = "pennant"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 30:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            res = detect_pennant(highs, lows, closes)
            if res.pattern == PatternType.NONE or res.confidence < 0.5:
                return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            diag = res.diagnostics
            pole_height = diag.get("pole_height", price * 0.03)
            upper_at_now = diag.get("upper_at_now", price * 1.005)
            lower_at_now = diag.get("lower_at_now", price * 0.995)

            if res.breakout_direction == "up":
                direction = "long"
                sl = lower_at_now
                risk = price - sl
                tp = price + max(pole_height, risk * 1.6)
                reward = tp - price
            else:
                direction = "short"
                sl = upper_at_now
                risk = sl - price
                tp = price - max(pole_height, risk * 1.6)
                reward = price - tp

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=res.confidence,
                features={"pattern": res.pattern.value, "breakout": res.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class DiamondStrategy(BasePatternStrategy):
    """Ромб (Diamond Top / Diamond Bottom)."""

    name = "diamond"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 35:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            res = detect_diamond(highs, lows, closes)
            if res.pattern == PatternType.NONE or res.confidence < 0.5:
                return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            diag = res.diagnostics
            diamond_height = diag.get("diamond_height", price * 0.01)
            upper_at_now = diag.get("upper_at_now", price * 1.005)
            lower_at_now = diag.get("lower_at_now", price * 0.995)

            if res.breakout_direction == "up":
                direction = "long"
                sl = lower_at_now
                risk = price - sl
                tp = price + max(diamond_height, risk * 1.6)
                reward = tp - price
            else:
                direction = "short"
                sl = upper_at_now
                risk = sl - price
                tp = price - max(diamond_height, risk * 1.6)
                reward = price - tp

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=res.confidence,
                features={"pattern": res.pattern.value, "breakout": res.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class CupAndHandleStrategy(BasePatternStrategy):
    """Чаша и ручка (Cup and Handle)."""

    name = "cup_and_handle"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < 35:
                return None

            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]

            res = detect_cup_and_handle(highs, lows, closes)
            if res.pattern == PatternType.NONE or res.confidence < 0.5:
                return None

            if not self._check_volume(candles):
                return None

            price = closes[-1]
            diag = res.diagnostics
            rim = diag.get("rim_level", price)
            cup_depth = diag.get("cup_depth", price * 0.02)

            if res.breakout_direction == "up":
                direction = "long"
                sl = rim - cup_depth * 0.4
                risk = price - sl
                tp = price + max(cup_depth, risk * 1.6)
                reward = tp - price
            else:
                direction = "short"
                sl = rim + cup_depth * 0.4
                risk = sl - price
                tp = price - max(cup_depth, risk * 1.6)
                reward = price - tp

            if risk <= 0 or reward / risk < 1.5:
                return None

            return SignalCandidate(
                symbol=ctx.symbol,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=res.confidence,
                features={"pattern": res.pattern.value, "breakout": res.breakout_direction},
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


# ---------------------------------------------------------------------------
# Круглая вершина / круглое дно (Д-фигура): свинг-дуга из N пивотов +
# пробой линии шеи. Контракт владельца (12.09.2026): наследник
# BasePatternStrategy, обе стороны, объёмный фильтр тот же, что у соседей
# (BasePatternStrategy._check_volume), бакеты статистики —
# rounded_top / rounded_bottom.
#
# Анти-дублирование с double/triple top — МЕХАНИЗМОМ, а не порогами:
# круглая фигура — дуга из >= 5 пивотов с РОВНО ОДНОЙ вершиной (в верхней
# 15%-зоне высоты фигуры лежит ровно 1 пивот); двойная/тройная вершина —
# это 2-3 пика близкой высоты, и правило «одна вершина» их не пропускает.
# Обратно: дуга короче 5 пивотов сюда не попадает и остаётся в зоне
# ответственности double/triple.
# ---------------------------------------------------------------------------

# Пороги дуги (решение владельца 12.09.2026: зафиксированы в коде и НЕ
# ослабляются ради частоты входов — частотой управляет kill-switch):
ROUNDED_MIN_PIVOTS = 5          # меньше 5 пивотов — не дуга (двойная/тройная)
ROUNDED_MAX_PIVOTS = 9          # больше 9 пивотов — канал, а не фигура
ROUNDED_MIN_WINDOW = 40         # минимальная ширина дуги, баров
ROUNDED_MAX_WINDOW = 120        # максимальная ширина дуги, баров
ROUNDED_MIN_SIDE_PIVOTS = 2     # минимум пивотов с каждой стороны вершины
ROUNDED_MAX_RESID_PCT = 0.15    # отклонение пивотов от дуги <= 15% высоты
ROUNDED_MIN_R2 = 0.6            # гладкость квадратичной дуги (у соседей 0.5)
ROUNDED_FILL_RATIO = 0.4        # «полнота» дуги у вершины (см. детектор)
ROUNDED_BREAK_GAP = 0.001       # пробой: закрытие за шеей с зазором 0.1%
ROUNDED_FRESH_BARS = 20         # последний пивот дуги не старше 20 баров
ROUNDED_MIN_HEIGHT_PCT = 0.005  # фигура ниже 0.5% цены — шум, не сигнал


def detect_rounded_top_bottom(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> PatternResult:
    """Д-фигура: круглая вершина (шорт) / круглое дно (лонг).

    Свинг-дуга из 5-9 пивотов шириной 40-120 баров, квадратичная кривизна,
    одна вершина; линия шеи — по двум последним противоположным свингам;
    пробой — только закрытием последнего (закрытого в контексте А5) бара.
    """
    none_res = PatternResult(
        pattern=PatternType.NONE,
        confidence=0.0,
        upper_line=None,
        lower_line=None,
        breakout_direction=None,
        diagnostics={"type": "none"},
    )
    n = len(closes)
    if n < ROUNDED_MIN_WINDOW + 2:
        return none_res
    hi_idx_all = _collapse_plateaus(_find_swings(highs, lows, window=3)[0])
    lo_idx_all = _collapse_plateaus(_find_swings(highs, lows, window=3)[1])
    price = float(closes[-1])

    def _try(top: bool) -> PatternResult | None:
        own = hi_idx_all if top else lo_idx_all     # пивоты дуги
        opp = lo_idx_all if top else hi_idx_all     # шея — противоположные
        lo_bound = n - 1 - ROUNDED_MAX_WINDOW
        piv = [i for i in own if lo_bound < i < n - 1]
        if not (ROUNDED_MIN_PIVOTS <= len(piv) <= ROUNDED_MAX_PIVOTS):
            return None
        if piv[-1] - piv[0] < ROUNDED_MIN_WINDOW:
            return None
        if piv[-1] < n - 1 - ROUNDED_FRESH_BARS:
            return None  # дуга старая — пробой уже не привязан к фигуре
        vals = [highs[i] if top else lows[i] for i in piv]
        apex_k = (max if top else min)(range(len(vals)), key=lambda k: vals[k])
        # вершина не с краю: дуга поднимается к центру и спускается
        if (
            apex_k < ROUNDED_MIN_SIDE_PIVOTS
            or len(vals) - 1 - apex_k < ROUNDED_MIN_SIDE_PIVOTS
        ):
            return None
        # линия шеи — по двум последним противоположным свингам окна
        neck_piv = [i for i in opp if lo_bound < i < n - 1][-2:]
        if len(neck_piv) < 2:
            return None
        opp_vals = lows if top else highs
        neck = _fit_line(
            [float(neck_piv[0]), float(neck_piv[1])],
            [opp_vals[neck_piv[0]], opp_vals[neck_piv[1]]],
        )
        apex_v = vals[apex_k]
        neck_at_apex = neck.value_at(float(piv[apex_k]))
        height = (apex_v - neck_at_apex) if top else (neck_at_apex - apex_v)
        if height < price * ROUNDED_MIN_HEIGHT_PCT:
            return None
        # квадратичная дуга: знак кривизны, гладкость, остаток
        arc = _quadratic_regression([float(i) for i in piv], vals)
        if arc is None or arc.quad is None:
            return None
        if (arc.curvature > 0) == top:  # купол вогнут (кривизна < 0), чаша — нет
            return None
        if arc.r2 < ROUNDED_MIN_R2:
            return None
        max_resid = max(
            abs(v - arc.value_at(float(i))) for i, v in zip(piv, vals, strict=False)
        )
        if max_resid > ROUNDED_MAX_RESID_PCT * height:
            return None
        # «Полная» дуга (анти-дублирование с double/triple): между
        # пивотами вокруг вершины цена НЕ возвращается к шее — у двойной
        # вершины между пиками глубокий ретест шеи, здесь это отсекается.
        a = max(0, apex_k - 1)
        b = min(len(piv) - 1, apex_k + 1)
        for j in range(a, b):
            i0, i1 = piv[j], piv[j + 1]
            neck_mid = neck.value_at(float(i0 + i1) / 2.0)
            if top:
                if min(lows[i0 : i1 + 1]) < neck_mid + ROUNDED_FILL_RATIO * height:
                    return None
            else:
                if max(highs[i0 : i1 + 1]) > neck_mid - ROUNDED_FILL_RATIO * height:
                    return None
        # пробой — закрытием последнего закрытого бара (А5)
        neck_now = neck.value_at(float(n - 1))
        if top:
            if not price < neck_now * (1 - ROUNDED_BREAK_GAP):
                return None
            ptype = PatternType.ROUNDED_TOP
            brk, sig = "down", "short_on_neckline_break_down"
        else:
            if not price > neck_now * (1 + ROUNDED_BREAK_GAP):
                return None
            ptype = PatternType.ROUNDED_BOTTOM
            brk, sig = "up", "long_on_neckline_break_up"
        return PatternResult(
            pattern=ptype,
            confidence=min(0.9, 0.5 + arc.r2 * 0.3),
            upper_line=None if top else neck,
            lower_line=neck if top else None,
            breakout_direction=brk,
            diagnostics={
                "type": "rounded_top" if top else "rounded_bottom",
                "signal": sig,
                "breakout": "closed_bar",
                "arc_pivots": len(piv),
                "arc_span": piv[-1] - piv[0],
                "apex": apex_v,
                "last_pivot": vals[-1],
                "arc_height": height,
                "neckline_at_now": neck_now,
                "arc_r2": arc.r2,
                "max_resid_pct": (max_resid / height) if height else 0.0,
            },
        )

    return _try(top=True) or _try(top=False) or none_res


class RoundedTopStrategy(BasePatternStrategy):
    """Круглая вершина (купол): шорт на пробое линии шеи."""

    name = "rounded_top"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < ROUNDED_MIN_WINDOW + 2:
                return None
            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]
            result = detect_rounded_top_bottom(highs, lows, closes)
            if result.pattern != PatternType.ROUNDED_TOP:
                return None
            if result.confidence < 0.5:
                return None
            # объёмный фильтр — точно как у соседей (порог 1.2), без послаблений
            if not self._check_volume(candles):
                return None
            price = closes[-1]
            neck = float(result.diagnostics.get("neckline_at_now", price))
            apex = float(result.diagnostics.get("apex", price))
            last_pivot = float(result.diagnostics.get("last_pivot", apex))
            height = max(float(result.diagnostics.get("arc_height", 0.0)), price * 0.005)
            # стоп за последний пивот дуги (правый «склон»), буфер как у
            # head_shoulders: 0.5 x max(часть высоты, 0.5% цены)
            sl = last_pivot + 0.5 * max(height * 0.25, price * 0.005)
            risk = sl - price
            tp = price - max(height, risk * 1.6)
            reward = price - tp
            if risk <= 0 or reward / risk < 1.5:
                return None
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
                    "breakout": result.diagnostics.get("breakout"),
                    "neckline": neck,
                    "arc_height": height,
                },
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


class RoundedBottomStrategy(BasePatternStrategy):
    """Круглое дно (чаша): лонг на пробое линии шеи."""

    name = "rounded_bottom"

    async def evaluate(self, ctx: StrategyContext):
        try:
            candles = ctx.candles
            if len(candles) < ROUNDED_MIN_WINDOW + 2:
                return None
            highs = [float(c.high) for c in candles]
            lows = [float(c.low) for c in candles]
            closes = [float(c.close) for c in candles]
            result = detect_rounded_top_bottom(highs, lows, closes)
            if result.pattern != PatternType.ROUNDED_BOTTOM:
                return None
            if result.confidence < 0.5:
                return None
            # объёмный фильтр — точно как у соседей (порог 1.2), без послаблений
            if not self._check_volume(candles):
                return None
            price = closes[-1]
            neck = float(result.diagnostics.get("neckline_at_now", price))
            apex = float(result.diagnostics.get("apex", price))
            last_pivot = float(result.diagnostics.get("last_pivot", apex))
            height = max(float(result.diagnostics.get("arc_height", 0.0)), price * 0.005)
            sl = last_pivot - 0.5 * max(height * 0.25, price * 0.005)
            risk = price - sl
            tp = price + max(height, risk * 1.6)
            reward = tp - price
            if risk <= 0 or reward / risk < 1.5:
                return None
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
                    "breakout": result.diagnostics.get("breakout"),
                    "neckline": neck,
                    "arc_height": height,
                },
            )
        except Exception as e:
            logger.debug("%s strategy error: %s", self.name, e)
            return None


ALL_PATTERN_STRATEGIES = [
    FallingWedgeStrategy,
    RisingWedgeStrategy,
    AscendingTriangleStrategy,
    DescendingTriangleStrategy,
    SymmetricalTriangleStrategy,
    HeadShouldersStrategy,
    DoubleTopBottomStrategy,
    TripleTopBottomStrategy,
    RectangleStrategy,
    ExpandingTriangleStrategy,
    FlagStrategy,
    PennantStrategy,
    DiamondStrategy,
    CupAndHandleStrategy,
    RoundedTopStrategy,
    RoundedBottomStrategy,
]
