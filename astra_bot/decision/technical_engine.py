"""Technical Analysis Engine: trend / momentum / volatility / volume."""

from __future__ import annotations

from dataclasses import dataclass

from ..core import models
from . import indicators as ind


@dataclass
class TechnicalReport:
    trend: int  # -1 / 0 / +1
    momentum: float  # z-like score
    volatility: str  # LOW/NORMAL/HIGH/EXTREME
    volume_confirmed: bool
    above_vwap: bool | None
    rsi: float | None
    atr_pct: float | None

    def to_dict(self) -> dict:
        return self.__dict__


class TechnicalEngine:
    def __init__(
        self,
        high_vol_atr_pct: float = 4.0,
        extreme_vol_atr_pct: float = 7.0,
        volume_factor: float = 1.5,
    ):
        self.high_vol_atr_pct = high_vol_atr_pct
        self.extreme_vol_atr_pct = extreme_vol_atr_pct
        self.volume_factor = volume_factor

    def analyse(self, candles: list[models.Candle]) -> TechnicalReport:
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        vols = [float(c.volume) for c in candles]

        e20 = ind.ema(closes, 20)
        e50 = ind.ema(closes, 50)
        e200 = ind.ema(closes, 200)
        trend = 0
        if e20 and e50 and e200:
            if e20 > e50 > e200:
                trend = 1
            elif e20 < e50 < e200:
                trend = -1

        rsi_val = ind.rsi(closes) or 50.0
        atr_val = ind.atr(highs, lows, closes)
        atr_pct = (atr_val / closes[-1] * 100) if atr_val and closes[-1] else 0.0
        if atr_pct >= self.extreme_vol_atr_pct:
            volatility = "EXTREME"
        elif atr_pct >= self.high_vol_atr_pct:
            volatility = "HIGH"
        elif atr_pct < 1.5:
            volatility = "LOW"
        else:
            volatility = "NORMAL"

        avg_vol = sum(vols[-20:]) / 20 if len(vols) >= 20 else 1.0
        volume_confirmed = vols[-1] >= avg_vol * self.volume_factor

        vwap_val = ind.vwap(highs, lows, closes, vols)
        above_vwap = closes[-1] > vwap_val if vwap_val else None

        macd_line = 0.0
        m = ind.macd(closes)
        if m:
            macd_line = m[0]

        # Простой momentum score: RSI-50 + MACD*100.
        momentum = (rsi_val - 50) / 50 + max(-1, min(1, macd_line * 100))

        return TechnicalReport(
            trend=trend,
            momentum=momentum,
            volatility=volatility,
            volume_confirmed=volume_confirmed,
            above_vwap=above_vwap,
            rsi=rsi_val,
            atr_pct=atr_pct,
        )
