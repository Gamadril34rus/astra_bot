"""Zeus-like regular RSI divergence (rules v2 / K-DIV).

Extracted from video lesson 5 + transcripts (5.txt): RSI(14), swing high/low,
regular divergence only. Not a hard entry gate — confidence bump only where wired.
"""
from __future__ import annotations

from collections.abc import Sequence


def _rsi14(closes: Sequence[float]) -> list[float]:
    n = len(closes)
    out = [50.0] * n
    if n < 15:
        return out
    gains = [0.0]
    losses = [0.0]
    for i in range(1, n):
        d = float(closes[i]) - float(closes[i - 1])
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[1:15]) / 14.0
    avg_l = sum(losses[1:15]) / 14.0
    for i in range(14, n):
        if i > 14:
            avg_g = (avg_g * 13 + gains[i]) / 14.0
            avg_l = (avg_l * 13 + losses[i]) / 14.0
        if avg_l < 1e-12:
            out[i] = 100.0
        else:
            rs = avg_g / avg_l
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def div_zeus_like(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    side: str,
    *,
    radius: int = 2,
) -> bool:
    """Regular RSI divergence on high/low swings (Zeus-like).

    long: last two swing lows — price LL (strict <) and RSI HL (strict >).
    short: last two swing highs — price HH and RSI LH.
    Fewer than two swings → False. No 0.1% / ±1.0 thresholds.
    """
    n = len(closes)
    if n < 16 or side not in ("long", "short"):
        return False
    rsi = _rsi14(closes)
    if side == "long":
        swings = [
            i
            for i in range(radius, n - radius)
            if all(lows[i] < lows[i + d] for d in range(-radius, radius + 1) if d != 0)
        ]
        swings = swings[-3:]
        if len(swings) < 2:
            return False
        i1, i2 = swings[-2], swings[-1]
        return float(lows[i2]) < float(lows[i1]) and rsi[i2] > rsi[i1]
    swings = [
        i
        for i in range(radius, n - radius)
        if all(highs[i] > highs[i + d] for d in range(-radius, radius + 1) if d != 0)
    ]
    swings = swings[-3:]
    if len(swings) < 2:
        return False
    i1, i2 = swings[-2], swings[-1]
    return float(highs[i2]) > float(highs[i1]) and rsi[i2] < rsi[i1]
