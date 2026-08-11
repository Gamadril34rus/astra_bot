"""Price structure engine: HH/HL/LH/LL, support/resistance, breakouts."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import models


@dataclass
class StructureReport:
    pattern: str  # HH_HL / LH_LL / MIXED / UNKNOWN
    support: float | None = None
    resistance: float | None = None
    recent_high: float | None = None
    recent_low: float | None = None
    breakout_long: bool = False
    breakout_short: bool = False
    fakeout_long: bool = False
    fakeout_short: bool = False
    swing_highs: list[float] = field(default_factory=list)
    swing_lows: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__


class StructureEngine:
    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def _swings(self, highs: list[float], lows: list[float]):
        swing_highs: list[float] = []
        swing_lows: list[float] = []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
                swing_highs.append(highs[i])
            if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
                swing_lows.append(lows[i])
        return swing_highs[-20:], swing_lows[-20:]

    def analyse(
        self,
        candles: list[models.Candle],
        volume_confirmed: bool = True,
    ) -> StructureReport:
        if len(candles) < self.lookback + 5:
            return StructureReport(pattern="UNKNOWN")
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        volumes = [float(c.volume) for c in candles]
        sh, sl = self._swings(highs, lows)
        rep = StructureReport(
            pattern="MIXED",
            recent_high=max(highs[-self.lookback:]),
            recent_low=min(lows[-self.lookback:]),
            swing_highs=sh,
            swing_lows=sl,
        )
        if len(sh) >= 2 and len(sl) >= 2:
            if sh[-1] > sh[-2] and sl[-1] > sl[-2]:
                rep.pattern = "HH_HL"
            elif sh[-1] < sh[-2] and sl[-1] < sl[-2]:
                rep.pattern = "LH_LL"

        # Support/resistance — last swing zones.
        if sh:
            rep.resistance = sum(sh[-3:]) / min(3, len(sh[-3:]))
        if sl:
            rep.support = sum(sl[-3:]) / min(3, len(sl[-3:]))

        # Breakout detection: close above recent high with volume, no immediate reversal.
        prior_high = max(highs[-21:-1]) if len(highs) > 21 else max(highs[:-1])
        prior_low = min(lows[-21:-1]) if len(lows) > 21 else min(lows[:-1])
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1.0
        if closes[-1] > prior_high and volumes[-1] > avg_vol:
            if closes[-1] < highs[-1] * 0.998:  # закрылись не у хая
                rep.fakeout_long = True
            else:
                rep.breakout_long = True and volume_confirmed
        if closes[-1] < prior_low and volumes[-1] > avg_vol:
            if closes[-1] > lows[-1] * 1.002:
                rep.fakeout_short = True
            else:
                rep.breakout_short = True and volume_confirmed
        return rep
