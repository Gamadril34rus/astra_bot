"""
Market Regime Detector V2 — Block 4.1 implementation per spec.

Detects 5 phases:
- TRENDING_UP / TRENDING_DOWN — ADX > 25, Choppiness Index < 40
- RANGING — ADX < 20, Choppiness Index > 60
- SQUEEZE — Bollinger Bandwidth < 20th percentile for 100 candles
- VOLATILE — ATR ratio (7/28) > 1.5
- TRANSITIONAL — everything else

Each phase maps to strategy set. VOLATILE and TRANSITIONAL → no trade or min size.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from ..core import models

logger = logging.getLogger(__name__)


class MarketRegimeV2(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    SQUEEZE = "SQUEEZE"
    VOLATILE = "VOLATILE"
    TRANSITIONAL = "TRANSITIONAL"


# Mapping regime -> allowed strategies (Block 4.1)
REGIME_STRATEGY_MAP = {
    MarketRegimeV2.TRENDING_UP: ["trend_following", "momentum", "ts_momentum", "scalp5m"],
    MarketRegimeV2.TRENDING_DOWN: ["trend_following", "momentum", "ts_momentum", "scalp5m"],
    MarketRegimeV2.RANGING: ["mean_reversion", "scalp", "scalp5m"],
    MarketRegimeV2.SQUEEZE: ["breakout", "scalp5m"],
    MarketRegimeV2.VOLATILE: [],  # No trade or min size
    MarketRegimeV2.TRANSITIONAL: ["momentum"],  # Minimal
}


@dataclass
class RegimeV2Result:
    regime: MarketRegimeV2
    confidence: float
    adx: float
    choppiness: float
    bb_bandwidth: float
    bb_percentile: float
    atr_ratio: float
    diagnostics: dict[str, Any]


def _calculate_ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return []
    ema = []
    k = 2 / (period + 1)
    sma = sum(prices[:period]) / period
    ema.append(sma)
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def _calculate_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Simplified ADX calculation."""
    if len(highs) < period * 2:
        return 0.0
    try:
        # True Range
        tr_list = []
        for i in range(1, len(highs)):
            tr1 = highs[i] - lows[i]
            tr2 = abs(highs[i] - closes[i - 1])
            tr3 = abs(lows[i] - closes[i - 1])
            tr_list.append(max(tr1, tr2, tr3))

        # Directional Movement
        plus_dm = []
        minus_dm = []
        for i in range(1, len(highs)):
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            if up_move > down_move and up_move > 0:
                plus_dm.append(up_move)
            else:
                plus_dm.append(0.0)
            if down_move > up_move and down_move > 0:
                minus_dm.append(down_move)
            else:
                minus_dm.append(0.0)

        # Smoothed
        tr_smooth = sum(tr_list[:period])
        plus_dm_smooth = sum(plus_dm[:period])
        minus_dm_smooth = sum(minus_dm[:period])

        for i in range(period, len(tr_list)):
            tr_smooth = tr_smooth - tr_smooth / period + tr_list[i]
            plus_dm_smooth = plus_dm_smooth - plus_dm_smooth / period + plus_dm[i]
            minus_dm_smooth = minus_dm_smooth - minus_dm_smooth / period + minus_dm[i]

        if tr_smooth == 0:
            return 0.0

        plus_di = 100 * plus_dm_smooth / tr_smooth
        minus_di = 100 * minus_dm_smooth / tr_smooth
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) != 0 else 0

        # ADX is smoothed DX
        # For simplicity, return DX as ADX if not enough data for smoothing
        return float(dx)
    except Exception:
        return 0.0


def _calculate_choppiness(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Choppiness Index: 0-100, <38 trending, >61 ranging."""
    if len(highs) < period:
        return 50.0
    try:
        # ATR sum
        tr_sum = 0.0
        for i in range(1, period + 1):
            idx = len(highs) - i
            if idx <= 0:
                break
            tr1 = highs[idx] - lows[idx]
            tr2 = abs(highs[idx] - closes[idx - 1])
            tr3 = abs(lows[idx] - closes[idx - 1])
            tr_sum += max(tr1, tr2, tr3)

        high_max = max(highs[-period:])
        low_min = min(lows[-period:])
        range_total = high_max - low_min

        if range_total == 0 or tr_sum == 0:
            return 50.0

        # Choppiness = 100 * log10(TR_sum / Range) / log10(n)
        import math

        ci = 100 * math.log10(tr_sum / range_total) / math.log10(period)
        return float(max(0.0, min(100.0, ci)))
    except Exception:
        return 50.0


def _calculate_bollinger_bandwidth(closes: list[float], period: int = 20, std: float = 2.0) -> tuple[float, list[float]]:
    """Returns (current bandwidth, historical bandwidths for percentile)."""
    if len(closes) < period:
        return 0.0, []
    try:
        bandwidths = []
        for i in range(period, len(closes) + 1):
            window = closes[i - period : i]
            sma = sum(window) / period
            variance = sum((x - sma) ** 2 for x in window) / period
            std_dev = variance ** 0.5
            upper = sma + std * std_dev
            lower = sma - std * std_dev
            if sma != 0:
                bw = (upper - lower) / sma * 100
            else:
                bw = 0.0
            bandwidths.append(bw)

        current = bandwidths[-1] if bandwidths else 0.0
        return current, bandwidths
    except Exception:
        return 0.0, []


def _calculate_atr_ratio(highs: list[float], lows: list[float], closes: list[float]) -> float:
    """ATR ratio 7/28."""
    if len(highs) < 28:
        return 1.0
    try:

        def atr(period: int) -> float:
            tr_list = []
            for i in range(1, len(highs)):
                tr1 = highs[i] - lows[i]
                tr2 = abs(highs[i] - closes[i - 1])
                tr3 = abs(lows[i] - closes[i - 1])
                tr_list.append(max(tr1, tr2, tr3))
            if len(tr_list) < period:
                return 0.0
            # Simple ATR as SMA of TR
            return sum(tr_list[-period:]) / period

        atr7 = atr(7)
        atr28 = atr(28)
        if atr28 == 0:
            return 1.0
        return atr7 / atr28
    except Exception:
        return 1.0


class MarketRegimeDetectorV2:
    """Block 4.1: Market Regime Detection per spec."""

    def __init__(self):
        pass

    def detect(self, candles: list[models.Candle]) -> RegimeV2Result:
        if len(candles) < 100:
            return RegimeV2Result(
                regime=MarketRegimeV2.TRANSITIONAL,
                confidence=0.3,
                adx=0.0,
                choppiness=50.0,
                bb_bandwidth=0.0,
                bb_percentile=50.0,
                atr_ratio=1.0,
                diagnostics={"reason": "insufficient_data"},
            )

        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]

        adx = _calculate_adx(highs, lows, closes, period=14)
        choppiness = _calculate_choppiness(highs, lows, closes, period=14)
        bb_bw, bb_history = _calculate_bollinger_bandwidth(closes, period=20, std=2.0)
        atr_ratio = _calculate_atr_ratio(highs, lows, closes)

        # BB percentile for squeeze
        bb_percentile = 50.0
        if len(bb_history) >= 100:
            sorted_bw = sorted(bb_history[-100:])
            # Find percentile of current
            count_below = sum(1 for x in sorted_bw if x < bb_bw)
            bb_percentile = count_below / len(sorted_bw) * 100
        elif bb_history:
            sorted_bw = sorted(bb_history)
            count_below = sum(1 for x in sorted_bw if x < bb_bw)
            bb_percentile = count_below / len(sorted_bw) * 100 if sorted_bw else 50.0

        # Determine regime per spec
        regime = MarketRegimeV2.TRANSITIONAL
        confidence = 0.5
        reason = ""

        # VOLATILE check: ATR ratio > 1.5
        if atr_ratio > 1.5:
            regime = MarketRegimeV2.VOLATILE
            confidence = min(0.9, 0.5 + (atr_ratio - 1.5) * 0.4)
            reason = f"ATR ratio {atr_ratio:.2f} > 1.5"
        # SQUEEZE: BB bandwidth < 20th percentile
        elif bb_percentile < 20:
            regime = MarketRegimeV2.SQUEEZE
            confidence = 0.7 + (20 - bb_percentile) / 100
            reason = f"BB bandwidth percentile {bb_percentile:.1f}% < 20%"
        # TRENDING: ADX > 25 and Choppiness < 40
        elif adx > 25 and choppiness < 40:
            # Determine direction by EMA 50/200 or price vs EMA
            try:
                ema50 = _calculate_ema(closes, 50)
                ema200 = _calculate_ema(closes, 200)
                if ema50 and ema200 and len(ema50) > 0 and len(ema200) > 0:
                    if ema50[-1] > ema200[-1]:
                        regime = MarketRegimeV2.TRENDING_UP
                    else:
                        regime = MarketRegimeV2.TRENDING_DOWN
                else:
                    # Fallback: price vs SMA 50
                    sma50 = sum(closes[-50:]) / 50
                    if closes[-1] > sma50:
                        regime = MarketRegimeV2.TRENDING_UP
                    else:
                        regime = MarketRegimeV2.TRENDING_DOWN
            except Exception:
                regime = MarketRegimeV2.TRENDING_UP

            confidence = min(0.95, 0.6 + (adx - 25) / 50 + (40 - choppiness) / 100)
            reason = f"ADX {adx:.1f} > 25 and Choppiness {choppiness:.1f} < 40"
        # RANGING: ADX < 20 and Choppiness > 60
        elif adx < 20 and choppiness > 60:
            regime = MarketRegimeV2.RANGING
            confidence = 0.6 + (60 - adx) / 100 + (choppiness - 60) / 100
            confidence = min(0.9, confidence)
            reason = f"ADX {adx:.1f} < 20 and Choppiness {choppiness:.1f} > 60"
        else:
            regime = MarketRegimeV2.TRANSITIONAL
            confidence = 0.4
            reason = "No clear regime, transitional"

        diagnostics = {
            "adx": adx,
            "choppiness": choppiness,
            "bb_bandwidth": bb_bw,
            "bb_percentile": bb_percentile,
            "atr_ratio": atr_ratio,
            "reason": reason,
        }

        return RegimeV2Result(
            regime=regime,
            confidence=confidence,
            adx=adx,
            choppiness=choppiness,
            bb_bandwidth=bb_bw,
            bb_percentile=bb_percentile,
            atr_ratio=atr_ratio,
            diagnostics=diagnostics,
        )

    def is_tradable(self, regime: MarketRegimeV2) -> bool:
        """Block 4.1: In VOLATILE and TRANSITIONAL — no trade or minimal."""
        return regime not in (MarketRegimeV2.VOLATILE, MarketRegimeV2.TRANSITIONAL)

    def get_allowed_strategies(self, regime: MarketRegimeV2) -> list[str]:
        return REGIME_STRATEGY_MAP.get(regime, [])

    def get_position_size_multiplier(self, regime: MarketRegimeV2) -> float:
        """Return size multiplier per regime."""
        if regime == MarketRegimeV2.VOLATILE:
            return 0.0  # No trade
        if regime == MarketRegimeV2.TRANSITIONAL:
            return 0.3  # Minimal
        if regime == MarketRegimeV2.SQUEEZE:
            return 0.8
        if regime in (MarketRegimeV2.TRENDING_UP, MarketRegimeV2.TRENDING_DOWN):
            return 1.0
        if regime == MarketRegimeV2.RANGING:
            return 0.7
        return 0.5
