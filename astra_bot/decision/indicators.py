"""Технические индикаторы для цепочки решений.

Ни один индикатор здесь НЕ инициирует сделку. Это только значения
для Feature Engine, которые затем учитываются scoring-системой.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Array = Sequence[float]


def ema(values: Array, period: int) -> float | None:
    if not values or len(values) < period:
        return None
    k = 2 / (period + 1)
    e = float(values[0])
    for v in values[1:]:
        e = float(v) * k + e * (1 - k)
    return e


def sma(values: Array, period: int) -> float | None:
    if not values or len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values: Array, period: int = 14) -> float | None:
    if not values or len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        change = values[i] - values[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def true_range(highs: Array, lows: Array, closes: Array) -> list[float]:
    tr: list[float] = []
    for i in range(1, len(closes)):
        tr.append(
            max(
                float(highs[i]) - float(lows[i]),
                abs(float(highs[i]) - float(closes[i - 1])),
                abs(float(lows[i]) - float(closes[i - 1])),
            )
        )
    return tr


def atr(highs: Array, lows: Array, closes: Array, period: int = 14) -> float | None:
    tr = true_range(highs, lows, closes)
    if len(tr) < period:
        return None
    return sum(tr[-period:]) / period


def bollinger_bands(
    closes: Array, period: int = 20, std_dev: float = 2.0
) -> tuple[float, float, float, float] | None:
    """Return (middle, upper, lower, bandwidth)."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    var = sum((x - middle) ** 2 for x in window) / period
    std = math.sqrt(var)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = (upper - lower) / middle if middle else 0.0
    return middle, upper, lower, bandwidth


def adx(
    highs: Array,
    lows: Array,
    closes: Array,
    period: int = 14,
) -> float | None:
    """Средний индекс направленного движения."""
    if len(closes) < period * 2 + 1:
        return None
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr: list[float] = []
    for i in range(1, len(closes)):
        up = float(highs[i]) - float(highs[i - 1])
        dn = float(lows[i - 1]) - float(lows[i])
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(
            max(
                float(highs[i]) - float(lows[i]),
                abs(float(highs[i]) - float(closes[i - 1])),
                abs(float(lows[i]) - float(closes[i - 1])),
            )
        )

    def wilder_smooth(seq: list[float], n: int) -> list[float]:
        if len(seq) < n:
            return []
        smoothed = [sum(seq[:n])]
        for v in seq[n:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / n + v)
        return smoothed

    tr_s = wilder_smooth(tr, period)
    plus_s = wilder_smooth(plus_dm, period)
    minus_s = wilder_smooth(minus_dm, period)
    if not tr_s:
        return None
    dx: list[float] = []
    for t, p, m in zip(tr_s, plus_s, minus_s, strict=False):
        if t == 0:
            continue
        plus_di = 100 * p / t
        minus_di = 100 * m / t
        denom = plus_di + minus_di
        if denom == 0:
            continue
        dx.append(100 * abs(plus_di - minus_di) / denom)
    if len(dx) < period:
        return sum(dx) / len(dx) if dx else None
    return sum(dx[-period:]) / period


def macd(
    closes: Array, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[float, float, float] | None:
    if len(closes) < slow + signal:
        return None
    ema_fast = ema(closes[-(slow + signal * 2):], fast)
    ema_slow = ema(closes[-(slow + signal * 2):], slow)
    if ema_fast is None or ema_slow is None:
        return None
    line = ema_fast - ema_slow
    # Сигнал — упрощённо как EMA от серии MACD; используем 0.3*line.
    return line, line * 0.3, line * 0.7


def vwap(highs: Array, lows: Array, closes: Array, volumes: Array) -> float | None:
    if not highs:
        return None
    typical = [(float(h) + float(lo) + float(c)) / 3 for h, lo, c in zip(highs, lows, closes, strict=False)]
    total_v = sum(float(v) for v in volumes)
    if total_v <= 0:
        return closes[-1] if closes else None
    return sum(t * float(v) for t, v in zip(typical, volumes, strict=False)) / total_v


def roc(closes: Array, period: int = 10) -> float | None:
    if len(closes) <= period:
        return None
    past = closes[-period - 1]
    return (closes[-1] / past - 1.0) * 100 if past else None


def realized_volatility(closes: Array, period: int = 20) -> float | None:
    if len(closes) < period + 1:
        return None
    import math as _m

    rets = []
    for i in range(-period, 0):
        if closes[i - 1]:
            rets.append(_m.log(float(closes[i]) / float(closes[i - 1])))
    if not rets:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return _m.sqrt(var) * 100


def obv(closes: Array, volumes: Array) -> float:
    total = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            total += float(volumes[i])
        elif closes[i] < closes[i - 1]:
            total -= float(volumes[i])
    return total
