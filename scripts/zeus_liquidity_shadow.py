"""Zeus shadow: liquidity sweep / equal high-low detect on 4h.

SHADOW ONLY — never opens trades. Logs event=liquidity_sweep to journal.
Rollback: remove import/call from run_paper_zeus.observe_zeus and delete this file.

Logic (Smart Money style, simplified):
  - Find recent swing highs/lows (3-bar pivot)
  - Equal highs/lows: two swings within EQUAL_PCT of each other
  - Sweep: last closed bar wicks beyond swing/EQ level then closes back inside
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("paper_zeus")

EQUAL_PCT = 0.0015  # 0.15% — equal high/low tolerance
LOOKBACK = 40  # closed 4h bars to scan
MIN_SWING_GAP = 2  # bars between pivots


def _ohlc(c: Any) -> tuple[float, float, float, float]:
    return (
        float(c.open),
        float(c.high),
        float(c.low),
        float(c.close),
    )


def _swing_highs(bars: list[Any]) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for i in range(1, len(bars) - 1):
        _, h, _, _ = _ohlc(bars[i])
        _, h0, _, _ = _ohlc(bars[i - 1])
        _, h1, _, _ = _ohlc(bars[i + 1])
        if h >= h0 and h >= h1:
            out.append((i, h))
    return out


def _swing_lows(bars: list[Any]) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for i in range(1, len(bars) - 1):
        _, _, lo, _ = _ohlc(bars[i])
        _, _, lo0, _ = _ohlc(bars[i - 1])
        _, _, lo1, _ = _ohlc(bars[i + 1])
        if lo <= lo0 and lo <= lo1:
            out.append((i, lo))
    return out


def detect_liquidity_sweep(bars: list[Any]) -> dict[str, Any] | None:
    """Return snapshot if last closed bar swept liquidity; else None."""
    if not bars or len(bars) < 8:
        return None
    window = bars[-LOOKBACK:] if len(bars) > LOOKBACK else bars
    # last bar is the candidate sweep bar (already closed in caller)
    last = window[-1]
    o, h, lo, c = _ohlc(last)
    body_top = max(o, c)
    body_bot = min(o, c)

    highs = _swing_highs(window[:-1])  # exclude last
    lows = _swing_lows(window[:-1])

    # equal highs: two recent swing highs close together
    eq_high = None
    if len(highs) >= 2:
        i1, p1 = highs[-1]
        i0, p0 = highs[-2]
        if abs(i1 - i0) >= MIN_SWING_GAP and abs(p1 - p0) / max(p1, 1e-12) <= EQUAL_PCT:
            eq_high = max(p0, p1)

    eq_low = None
    if len(lows) >= 2:
        i1, p1 = lows[-1]
        i0, p0 = lows[-2]
        if abs(i1 - i0) >= MIN_SWING_GAP and abs(p1 - p0) / max(p1, 1e-12) <= EQUAL_PCT:
            eq_low = min(p0, p1)

    last_sh = highs[-1][1] if highs else None
    last_sl = lows[-1][1] if lows else None

    # bearish sweep: wick above swing/EQ high, close back below
    level_hi = eq_high if eq_high is not None else last_sh
    if level_hi is not None and h > level_hi and c < level_hi and body_top <= level_hi * (1 + EQUAL_PCT):
        return {
            "side": "bearish_sweep",
            "level": round(level_hi, 8),
            "kind": "equal_high" if eq_high is not None else "swing_high",
            "wick_high": round(h, 8),
            "close": round(c, 8),
            "note": "shadow only; not an entry",
        }

    # bullish sweep: wick below swing/EQ low, close back above
    level_lo = eq_low if eq_low is not None else last_sl
    if level_lo is not None and lo < level_lo and c > level_lo and body_bot >= level_lo * (1 - EQUAL_PCT):
        return {
            "side": "bullish_sweep",
            "level": round(level_lo, 8),
            "kind": "equal_low" if eq_low is not None else "swing_low",
            "wick_low": round(lo, 8),
            "close": round(c, 8),
            "note": "shadow only; not an entry",
        }

    return None


def log_liquidity_sweep(*, journal: Any, symbol: str, bars: list[Any]) -> None:
    """Best-effort journal write; never raises into caller."""
    try:
        snap = detect_liquidity_sweep(bars)
        if not snap:
            return
        journal._write(
            {
                "event": "liquidity_sweep",
                "symbol": symbol,
                "strategy": "zeus_shadow",
                "side": snap.get("side"),
                "level": snap.get("level"),
                "kind": snap.get("kind"),
                "snapshot": snap,
                "note": "shadow; no trade",
            }
        )
        logger.info(
            "Zeus shadow sweep %s %s @ %s (%s)",
            symbol,
            snap.get("side"),
            snap.get("level"),
            snap.get("kind"),
        )
    except Exception as exc:
        logger.debug("liquidity_sweep skip: %s", exc)
