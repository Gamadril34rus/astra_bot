"""Zeus Wedge Retest (1h) — shadow-only research strategy."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from astra_bot.zeus_wedge_1h_core import evaluate_bars

from .base import BaseStrategy, Signal, StrategyConfig


class ZeusWedge1hConfig(StrategyConfig):
    name: str = "zeus_wedge_retest_1h"
    enabled: bool = False
    preferred_timeframe: str = "1h"
    lookback: int = 48
    min_touches: int = 2


class ZeusWedgeRetest1hStrategy(BaseStrategy[ZeusWedge1hConfig]):
    def __init__(self, config: ZeusWedge1hConfig | None = None) -> None:
        super().__init__(config or ZeusWedge1hConfig())

    def generate_signal(self, *args: Any, **kwargs: Any) -> Signal | None:
        return None

    def diagnose(self, bars: Sequence[Any], *, lb: int | None = None) -> dict[str, Any]:
        if not bars:
            return {"status": "insufficient_bars"}
        opens, highs, lows, closes = [], [], [], []
        for b in bars:
            if hasattr(b, "open"):
                opens.append(float(b.open))
                highs.append(float(b.high))
                lows.append(float(b.low))
                closes.append(float(b.close))
            else:
                opens.append(float(b["open"]))
                highs.append(float(b["high"]))
                lows.append(float(b["low"]))
                closes.append(float(b["close"]))
        return evaluate_bars(opens, highs, lows, closes, lb=lb or self.config.lookback)
