"""Feature Engine — собирает все признаки из MarketContext.

Не принимает торговых решений. Только считает значения для
Regime/Scoring/ML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core import models
from . import indicators as ind
from .config import DecisionConfig
from .context import MarketContext


@dataclass
class Features:
    """Все признаки, нужные scoring-системе и ML."""

    # Trend
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    adx: float | None = None
    trend_alignment: int = 0  # +1 long, -1 short, 0 none

    # Momentum
    rsi: float | None = None
    macd_line: float | None = None
    roc: float | None = None

    # Volatility
    atr_pct: float | None = None
    bb_width: float | None = None
    realized_vol: float | None = None

    # Volume
    volume_ratio: float | None = None
    obv_slope: float | None = None

    # VWAP
    vwap: float | None = None
    above_vwap: bool | None = None

    # Multi-timeframe
    htf_trend: str = "UNKNOWN"  # 4h
    mtf_regime: str = "UNKNOWN"  # 1h
    ltf_structure: str = "UNKNOWN"  # 15m

    # External
    news_score: int = 0
    onchain_score: float = 0.0
    funding: float | None = None
    open_interest_change: float | None = None
    btc_regime: str = "UNKNOWN"

    # Price
    price: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def as_ml_dict(self) -> dict[str, float | int | str]:
        out: dict[str, float | int | str] = {}
        for k, v in self.__dict__.items():
            if k in {"raw", "htf_trend", "mtf_regime", "ltf_structure", "btc_regime"}:
                out[k] = v if isinstance(v, str) else 0.0
                continue
            if v is None:
                out[k] = 0.0
            else:
                out[k] = v
        return out


class FeatureEngine:
    def __init__(self, config: DecisionConfig | None = None):
        self.config = config or DecisionConfig()

    @staticmethod
    def _arrays(candles: list[models.Candle]):
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        volumes = [float(c.volume) for c in candles]
        return closes, highs, lows, volumes

    def _trend_alignment(self, closes: list[float], ema20, ema50, ema200) -> int:
        if None in (ema20, ema50, ema200):
            return 0
        if ema20 > ema50 > ema200 and closes[-1] > ema20:
            return 1
        if ema20 < ema50 < ema200 and closes[-1] < ema20:
            return -1
        return 0

    def compute(self, ctx: MarketContext) -> Features:
        cfg = self.config
        features = Features(price=float(ctx.current_price))
        primary = ctx.candles_on("1h") or ctx.candles_on("4h")
        if not primary:
            return features

        closes, highs, lows, volumes = self._arrays(primary)
        e20 = ind.ema(closes, cfg.ema_fast)
        e50 = ind.ema(closes, cfg.ema_mid)
        e200 = ind.ema(closes, cfg.ema_slow)
        bb = ind.bollinger_bands(closes, cfg.bb_period, cfg.bb_std)

        features.ema20 = e20
        features.ema50 = e50
        features.ema200 = e200
        features.adx = ind.adx(highs, lows, closes, period=cfg.atr_period)
        features.trend_alignment = self._trend_alignment(closes, e20, e50, e200)

        features.rsi = ind.rsi(closes, 14)
        macd_val = ind.macd(closes)
        if macd_val:
            features.macd_line = macd_val[0]
        features.roc = ind.roc(closes, 10)

        atr_val = ind.atr(highs, lows, closes, cfg.atr_period)
        if atr_val and closes[-1]:
            features.atr_pct = atr_val / closes[-1] * 100
        if bb:
            features.bb_width = bb[3]
        features.realized_vol = ind.realized_volatility(closes, 20)

        avg_vol = ind.sma(volumes[-cfg.volume_period - 1:], cfg.volume_period)
        if avg_vol and volumes[-1]:
            features.volume_ratio = volumes[-1] / avg_vol if avg_vol else 1.0
        obv_total = ind.obv(closes, volumes)
        features.obv_slope = obv_total / max(len(closes), 1)

        features.vwap = ind.vwap(highs, lows, closes, volumes)
        if features.vwap:
            features.above_vwap = closes[-1] > features.vwap

        # Multi-timeframe.
        htf = ctx.candles_on("4h")
        if htf:
            features.htf_trend = self._trend_label(htf)
        mtf = ctx.candles_on("1h")
        if mtf:
            features.mtf_regime = self._trend_label(mtf)
        ltf = ctx.candles_on("15m")
        if ltf:
            features.ltf_structure = self._trend_label(ltf)

        features.news_score = ctx.news_score
        features.onchain_score = ctx.onchain_score
        if "funding" in ctx.derivatives:
            features.funding = float(ctx.derivatives["funding"])
        if "open_interest_change" in ctx.derivatives:
            features.open_interest_change = float(
                ctx.derivatives["open_interest_change"]
            )
        if "btc_regime" in ctx.global_market:
            features.btc_regime = str(ctx.global_market["btc_regime"])

        features.raw = {
            "closes_len": len(closes),
            "bb_lower": bb[2] if bb else None,
            "bb_upper": bb[1] if bb else None,
        }
        return features

    def _trend_label(self, candles: list[models.Candle]) -> str:
        closes, highs, lows, _ = self._arrays(candles)
        if len(closes) < max(self.config.ema_slow + 5, 30):
            return "UNKNOWN"
        e20 = ind.ema(closes, self.config.ema_fast)
        e50 = ind.ema(closes, self.config.ema_mid)
        e200 = ind.ema(closes, self.config.ema_slow)
        adx_val = ind.adx(highs, lows, closes, period=self.config.atr_period) or 0
        if e20 and e50 and e200:
            if e20 > e50 > e200 and adx_val > self.config.adx_trend_threshold:
                return "STRONG_BULL"
            if e20 < e50 < e200 and adx_val > self.config.adx_trend_threshold:
                return "STRONG_BEAR"
            if e20 > e50:
                return "WEAK_BULL"
            if e20 < e50:
                return "WEAK_BEAR"
        if adx_val < 15:
            return "RANGE"
        return "UNKNOWN"
