"""Zeus multi-top / multi-bottom breakdown (owner 30.10).

Catch continuation dumps/pumps after 2-3 swing extremes - the gap that
missed BTC 29.10 19:00->30.10 04:00 (~5.5k drop) after flat highs near 81.9k.

Pure functions for live_arm_feed. No exchange IO. Short primary; long symmetric.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

Side = Literal["long", "short"]


@dataclass(frozen=True)
class BreakdownSignal:
    side: Side
    entry: float
    stop: float
    take_profit: float
    confidence: float
    neckline: float
    n_tops: int
    bar_index: int
    meta: dict[str, Any]


def _swing_highs(highs: Sequence[float], radius: int = 2) -> list[int]:
    out: list[int] = []
    n = len(highs)
    for i in range(radius, n - radius):
        h = highs[i]
        if all(h >= highs[j] for j in range(i - radius, i + radius + 1)):
            out.append(i)
    return out


def _swing_lows(lows: Sequence[float], radius: int = 2) -> list[int]:
    out: list[int] = []
    n = len(lows)
    for i in range(radius, n - radius):
        lo = lows[i]
        if all(lo <= lows[j] for j in range(i - radius, i + radius + 1)):
            out.append(i)
    return out


def detect_multi_top_breakdown(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    min_tops: int = 2,
    max_tops: int = 3,
    radius: int = 2,
    tops_flat_pct: float = 0.015,
    min_break_pct: float = 0.001,
    stop_buffer_pct: float = 0.002,
    tp1_rr: float = 0.6,
    require_close_break: bool = True,
) -> BreakdownSignal | None:
    """Short after 2-3 flat swing highs + close below neckline.

    tops_flat_pct: max(max_top - min_top) / mid_top -- flat shelf of tops.
    Neckline: min low from first to last top inclusive.
    """
    n = len(closes)
    if n < radius * 2 + 10 or min_tops < 2:
        return None
    swings = _swing_highs(highs, radius=radius)
    if len(swings) < min_tops:
        return None
    i = n - 1
    swings = [s for s in swings if s <= i - radius]
    if len(swings) < min_tops:
        return None
    use = swings[-max_tops:]
    if len(use) < min_tops:
        return None
    top_px = [float(highs[s]) for s in use]
    mid = sum(top_px) / len(top_px)
    if mid <= 0:
        return None
    if (max(top_px) - min(top_px)) / mid > tops_flat_pct:
        return None
    neck = min(float(lows[j]) for j in range(use[0], use[-1] + 1))
    close = float(closes[i])
    low = float(lows[i])
    broke = close < neck * (1.0 - min_break_pct)
    if not require_close_break:
        broke = broke or low < neck * (1.0 - min_break_pct)
    if not broke:
        return None
    if i >= 1 and float(closes[i - 1]) < neck:
        return None
    stop = max(top_px) * (1.0 + stop_buffer_pct)
    risk = stop - close
    if risk <= 0:
        return None
    tp = close - risk * tp1_rr
    conf = min(0.55 + 0.08 * (len(use) - 2) + 0.05, 0.85)
    return BreakdownSignal(
        side="short",
        entry=close,
        stop=stop,
        take_profit=tp,
        confidence=conf,
        neckline=neck,
        n_tops=len(use),
        bar_index=i,
        meta={
            "tops": top_px,
            "top_idx": list(use),
            "pattern": "multi_top_breakdown",
            "tp1_rr": tp1_rr,
        },
    )


def detect_multi_bottom_breakout(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    min_bottoms: int = 2,
    max_bottoms: int = 3,
    radius: int = 2,
    bottoms_flat_pct: float = 0.015,
    min_break_pct: float = 0.001,
    stop_buffer_pct: float = 0.002,
    tp1_rr: float = 0.6,
    require_close_break: bool = True,
) -> BreakdownSignal | None:
    """Long mirror: 2-3 flat swing lows + close above neckline."""
    n = len(closes)
    if n < radius * 2 + 10 or min_bottoms < 2:
        return None
    swings = _swing_lows(lows, radius=radius)
    i = n - 1
    swings = [s for s in swings if s <= i - radius]
    if len(swings) < min_bottoms:
        return None
    use = swings[-max_bottoms:]
    if len(use) < min_bottoms:
        return None
    bot_px = [float(lows[s]) for s in use]
    mid = sum(bot_px) / len(bot_px)
    if mid <= 0:
        return None
    if (max(bot_px) - min(bot_px)) / mid > bottoms_flat_pct:
        return None
    neck = max(float(highs[j]) for j in range(use[0], use[-1] + 1))
    close = float(closes[i])
    high = float(highs[i])
    broke = close > neck * (1.0 + min_break_pct)
    if not require_close_break:
        broke = broke or high > neck * (1.0 + min_break_pct)
    if not broke:
        return None
    if i >= 1 and float(closes[i - 1]) > neck:
        return None
    stop = min(bot_px) * (1.0 - stop_buffer_pct)
    risk = close - stop
    if risk <= 0:
        return None
    tp = close + risk * tp1_rr
    conf = min(0.55 + 0.08 * (len(use) - 2) + 0.05, 0.85)
    return BreakdownSignal(
        side="long",
        entry=close,
        stop=stop,
        take_profit=tp,
        confidence=conf,
        neckline=neck,
        n_tops=len(use),
        bar_index=i,
        meta={
            "bottoms": bot_px,
            "bottom_idx": list(use),
            "pattern": "multi_bottom_breakout",
            "tp1_rr": tp1_rr,
        },
    )


def detect_breakdown_from_ohlc(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    **kwargs: Any,
) -> BreakdownSignal | None:
    """Prefer short multi-top; else long multi-bottom."""
    s = detect_multi_top_breakdown(highs, lows, closes, **kwargs)
    if s is not None:
        return s
    return detect_multi_bottom_breakout(highs, lows, closes, **kwargs)


def series_from_candles(candles: Sequence[Any]) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for c in candles:
        if isinstance(c, dict):
            highs.append(float(c["high"]))
            lows.append(float(c["low"]))
            closes.append(float(c["close"]))
        else:
            highs.append(float(c.high))
            lows.append(float(c.low))
            closes.append(float(c.close))
    return highs, lows, closes
