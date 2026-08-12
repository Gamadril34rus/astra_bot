# ruff: noqa: UP042
"""Market Regime Engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core import models
from . import indicators as ind


class MarketRegime(str, Enum):
    STRONG_BULL = "STRONG_BULL_TREND"
    WEAK_BULL = "WEAK_BULL_TREND"
    STRONG_BEAR = "STRONG_BEAR_TREND"
    WEAK_BEAR = "WEAK_BEAR_TREND"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOL = "HIGH_VOLATILITY"
    LOW_VOL = "LOW_VOLATILITY"
    PANIC = "PANIC"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeReport:
    regime: MarketRegime
    confidence: float
    details: dict

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 2),
            "details": self.details,
        }


class RegimeEngine:
    def __init__(self, adx_threshold: float = 23.0, adx_strong: float = 40.0):
        self.adx_threshold = adx_threshold
        self.adx_strong = adx_strong

    def classify(
        self,
        candles: list[models.Candle],
        news_score: int = 0,
        btc_regime: str | None = None,
    ) -> RegimeReport:
        if len(candles) < 60:
            return RegimeReport(MarketRegime.UNKNOWN, 0.0, {})

        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        volumes = [float(c.volume) for c in candles]

        e20 = ind.ema(closes, 20)
        e50 = ind.ema(closes, 50)
        e200 = ind.ema(closes, 200)
        adx_val = ind.adx(highs, lows, closes, period=14) or 0.0
        atr_val = ind.atr(highs, lows, closes)
        atr_pct = (atr_val / closes[-1] * 100) if atr_val and closes[-1] else 0.0
        bb = ind.bollinger_bands(closes)
        bw = bb[3] if bb else 0.0

        avg_vol = sum(volumes[-20:]) / 20
        vol_spike = volumes[-1] / avg_vol if avg_vol else 1.0

        # Panic override.
        if news_score >= 75 or atr_pct > 10 or (btc_regime == "PANIC"):
            return RegimeReport(
                MarketRegime.PANIC,
                0.95,
                {"atr_pct": atr_pct, "news": news_score, "btc": btc_regime},
            )
        if atr_pct > 5 or news_score >= 50:
            return RegimeReport(
                MarketRegime.HIGH_VOL,
                0.8,
                {"atr_pct": atr_pct, "news": news_score},
            )
        if bw < 0.03 and atr_pct < 1.5:
            return RegimeReport(MarketRegime.LOW_VOL, 0.7, {"bb_width": bw})

        if e20 and e50 and e200:
            aligned_bull = e20 > e50 > e200 and closes[-1] > e20
            aligned_bear = e20 < e50 < e200 and closes[-1] < e20
            if aligned_bull and adx_val >= self.adx_strong:
                regime = MarketRegime.STRONG_BULL
            elif aligned_bear and adx_val >= self.adx_strong:
                regime = MarketRegime.STRONG_BEAR
            elif aligned_bull and adx_val >= self.adx_threshold:
                regime = MarketRegime.WEAK_BULL
            elif aligned_bear and adx_val >= self.adx_threshold:
                regime = MarketRegime.WEAK_BEAR
            else:
                regime = MarketRegime.RANGE
            # Breakout: close above recent 20-bar high on volume.
            if regime in (MarketRegime.WEAK_BULL, MarketRegime.STRONG_BULL):
                recent_high = max(highs[-21:-1]) if len(highs) > 21 else max(highs[:-1])
                if (
                    closes[-1] > recent_high
                    and vol_spike > 1.5
                    and atr_pct > 1.5
                ):
                    regime = MarketRegime.BREAKOUT
            confidence = min(
                0.99, 0.4 + adx_val / 100.0 + min(atr_pct, 5) / 50.0
            )
            return RegimeReport(
                regime,
                confidence,
                {
                    "ema20": e20,
                    "ema50": e50,
                    "ema200": e200,
                    "adx": adx_val,
                    "atr_pct": atr_pct,
                    "bb_width": bw,
                    "volume_spike": vol_spike,
                },
            )

        return RegimeReport(
            MarketRegime.UNKNOWN, 0.2, {"reason": "not enough EMA data"}
        )
