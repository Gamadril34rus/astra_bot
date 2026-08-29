# ruff: noqa: UP042
"""Market Regime Engine.

Этап A2 (МТЗ §10–14): поверх единого legacy-enum ``MarketRegime`` строится
Regime 2.0 — ортогональные оси (trend × volatility × liquidity) и
кросс-маркет контекст. Legacy-классификация сохранена без изменений:
``regime``/``confidence``/``details`` ведут себя как раньше, оси —
дополнительное поле ``axes`` (его нет только при UNKNOWN «мало данных»).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..core import models
from . import indicators as ind
from .regime_axes import (
    CrossMarketContext,
    LiquidityAxis,
    RegimeAxes,
    TrendAxis,
    VolatilityAxis,
    derive_axes,  # noqa: F401  (реэкспорт для удобства потребителей)
    liquidity_axis,
    orderbook_liquidity_inputs,
    trend_axis,
    volatility_axis,
)


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
    # Regime 2.0: вектор осей (МТЗ §10). None — если данных для осей нет.
    axes: RegimeAxes | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 2),
            "details": self.details,
        }
        if self.axes is not None:
            out["axes"] = self.axes.to_dict()
            # Удобно потребителям (trading_engine пишет в позицию/ClosedTrade):
            out["axes_key"] = self.axes.axes_key()
        return out


class RegimeEngine:
    def __init__(self, adx_threshold: float = 23.0, adx_strong: float = 40.0):
        self.adx_threshold = adx_threshold
        self.adx_strong = adx_strong

    def classify(
        self,
        candles: list[models.Candle],
        news_score: int = 0,
        btc_regime: str | None = None,
        *,
        orderbook: Any | None = None,
        current_price: float | None = None,
        cross_market: dict[str, Any] | None = None,
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

        # Общие сигналы для осей (trend/vol/liquidity) — считаются один раз.
        aligned_bull = bool(e20 and e50 and e200 and e20 > e50 > e200 and closes[-1] > e20)
        aligned_bear = bool(e20 and e50 and e200 and e20 < e50 < e200 and closes[-1] < e20)
        recent_high = max(highs[-21:-1]) if len(highs) > 21 else max(highs[:-1])
        breakout = bool(
            (aligned_bull or aligned_bear)
            and closes[-1] > recent_high
            and vol_spike > 1.5
            and atr_pct > 1.5
        )
        spread_pct, depth_usd = orderbook_liquidity_inputs(
            orderbook, float(current_price or 0.0)
        )
        cross = CrossMarketContext.from_global_market(cross_market)

        def make_axes(
            trend: TrendAxis | None = None,
            vol: VolatilityAxis | None = None,
            liq: LiquidityAxis | None = None,
        ) -> RegimeAxes:
            return RegimeAxes(
                trend=trend
                if trend is not None
                else trend_axis(
                    adx=adx_val,
                    aligned_bull=aligned_bull,
                    aligned_bear=aligned_bear,
                    breakout=breakout,
                    adx_threshold=self.adx_threshold,
                    adx_strong=self.adx_strong,
                ),
                volatility=vol
                if vol is not None
                else volatility_axis(atr_pct=atr_pct),
                liquidity=liq
                if liq is not None
                else liquidity_axis(
                    spread_pct=spread_pct,
                    depth_usd=depth_usd,
                    vol_spike=vol_spike,
                ),
                cross=cross,
                inputs={
                    "adx": adx_val,
                    "atr_pct": atr_pct,
                    "bb_width": bw,
                    "volume_spike": vol_spike,
                    "spread_pct": spread_pct,
                    "depth_usd": depth_usd,
                },
            )

        # Panic override.
        if news_score >= 75 or atr_pct > 10 or (btc_regime == "PANIC"):
            return RegimeReport(
                MarketRegime.PANIC,
                0.95,
                {"atr_pct": atr_pct, "news": news_score, "btc": btc_regime},
                axes=make_axes(trend=TrendAxis.TRANSITION, vol=VolatilityAxis.EXTREME),
            )
        if atr_pct > 5 or news_score >= 50:
            return RegimeReport(
                MarketRegime.HIGH_VOL,
                0.8,
                {"atr_pct": atr_pct, "news": news_score},
                axes=make_axes(vol=VolatilityAxis.HIGH),
            )
        if bw < 0.03 and atr_pct < 1.5:
            return RegimeReport(
                MarketRegime.LOW_VOL,
                0.7,
                {"bb_width": bw},
                axes=make_axes(trend=TrendAxis.RANGE),
            )

        if e20 and e50 and e200:
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
                    # A2: кросс-маркет / relative strength (в diagnostics,
                    # в бакет статистики не входит — см. regime_axes).
                    "cross_market": cross.to_dict() if cross else None,
                },
                axes=make_axes(),
            )

        return RegimeReport(
            MarketRegime.UNKNOWN, 0.2, {"reason": "not enough EMA data"}
        )
