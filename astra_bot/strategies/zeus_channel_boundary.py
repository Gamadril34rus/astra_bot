"""Zeus Channel Boundary (4h) — lessons 4/5/7.

Parallel channel: entry from boundary (not mid).
Paper research. enabled=False by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


def _linreg(ys: list[float]) -> tuple[float, float]:
    n = len(ys)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    xs = list(range(n))
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys, strict=True))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return 0.0, sy / n
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n
    return b, a


@dataclass
class ZeusChannelBoundaryConfig(StrategyConfig):
    name: str = "zeus_channel_boundary_4h"
    enabled: bool = False
    preferred_timeframe: str = "4h"
    lookback: int = 30
    min_touches: int = 3
    parallel_tol: float = 0.45
    min_width_pct: float = 0.012
    max_width_pct: float = 0.12
    near_boundary_pct: float = 0.015
    stop_buffer_pct: float = 0.003
    min_rr: float = 1.5
    enable_breakout_hold: bool = True
    min_hold_bars: int = 2
    max_hold_bars: int = 6


class ZeusChannelBoundaryStrategy(BaseStrategy[ZeusChannelBoundaryConfig]):
    preferred_timeframe: str = "4h"

    def __init__(self, config: ZeusChannelBoundaryConfig | None = None):
        super().__init__(config or ZeusChannelBoundaryConfig())
        self.config: ZeusChannelBoundaryConfig

    def diagnose(
        self,
        candles: list[models.Candle],
        current_price: float | None = None,
    ) -> dict[str, Any]:
        c = self.config
        out: dict[str, Any] = {
            "has_channel": False,
            "would_signal": False,
            "pattern": None,
            "direction": None,
            "reject_reason": None,
            "stage": "init",
            "strategy": c.name,
        }
        n = int(c.lookback)
        if len(candles) < n + 2:
            out["reject_reason"] = "not_enough_bars"
            out["stage"] = "data"
            return out

        window = candles[-(n + 1) : -1]
        highs = [float(x.high) for x in window]
        lows = [float(x.low) for x in window]
        last = window[-1]
        price = float(current_price) if current_price is not None else float(last.close)

        slope_h, int_h = _linreg(highs)
        slope_l, int_l = _linreg(lows)
        x_last = len(window) - 1
        upper = int_h + slope_h * x_last
        lower = int_l + slope_l * x_last
        if upper <= lower or lower <= 0:
            out["reject_reason"] = "invalid_bounds"
            return out

        mid = 0.5 * (upper + lower)
        width_pct = (upper - lower) / mid
        out.update({"upper": upper, "lower": lower, "width_pct": width_pct})

        if width_pct < c.min_width_pct or width_pct > c.max_width_pct:
            out["reject_reason"] = "width_out_of_band"
            return out

        scale = max(abs(slope_h), abs(slope_l), 1e-9)
        parallel_score = abs(slope_h - slope_l) / scale
        out["parallel_score"] = parallel_score
        if parallel_score > c.parallel_tol:
            out["reject_reason"] = "not_parallel_channel"
            return out

        thr = mid * 0.008
        touch_hi = sum(
            1 for i, h in enumerate(highs) if abs(h - (int_h + slope_h * i)) <= thr
        )
        touch_lo = sum(
            1 for i, lo in enumerate(lows) if abs(lo - (int_l + slope_l * i)) <= thr
        )
        out["touch_hi"] = touch_hi
        out["touch_lo"] = touch_lo
        if touch_hi < c.min_touches or touch_lo < c.min_touches:
            out["reject_reason"] = "few_touches"
            return out

        out["has_channel"] = True
        out["stage"] = "pattern"

        near_lo = price <= lower * (1 + c.near_boundary_pct)
        near_hi = price >= upper * (1 - c.near_boundary_pct)

        bars_out = 0
        for bar in reversed(window):
            cl = float(bar.close)
            if cl > upper * 1.001 or cl < lower * 0.999:
                bars_out += 1
            else:
                break

        if c.enable_breakout_hold and c.min_hold_bars <= bars_out <= c.max_hold_bars:
            if price > upper:
                out.update(
                    would_signal=True,
                    direction="long",
                    pattern="channel_breakout_up_hold",
                    entry=price,
                    stop=lower * (1 - c.stop_buffer_pct),
                )
                return out
            if price < lower:
                out.update(
                    would_signal=True,
                    direction="short",
                    pattern="channel_breakout_down_hold",
                    entry=price,
                    stop=upper * (1 + c.stop_buffer_pct),
                )
                return out

        if abs(price - mid) / mid < 0.35 * width_pct:
            out["reject_reason"] = "middle_of_channel"
            return out

        if near_lo:
            stop = lower * (1 - c.stop_buffer_pct)
            risk = price - stop
            if risk > 0 and (upper - price) / risk >= c.min_rr:
                out.update(
                    would_signal=True,
                    direction="long",
                    pattern="channel_bounce_lower",
                    entry=price,
                    stop=stop,
                    target=upper,
                )
                return out
            out["reject_reason"] = "rr_fail_long"
            return out

        if near_hi:
            stop = upper * (1 + c.stop_buffer_pct)
            risk = stop - price
            if risk > 0 and (price - lower) / risk >= c.min_rr:
                out.update(
                    would_signal=True,
                    direction="short",
                    pattern="channel_bounce_upper",
                    entry=price,
                    stop=stop,
                    target=lower,
                )
                return out
            out["reject_reason"] = "rr_fail_short"
            return out

        out["reject_reason"] = "not_at_boundary"
        return out

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        current_price: float | None = None,
        **kwargs: Any,
    ) -> Signal | None:
        if not self.config.enabled:
            return None
        closed = list(candles)
        if len(closed) >= 2:
            closed = closed[:-1]
        price = float(current_price) if current_price is not None else (
            float(closed[-1].close) if closed else 0.0
        )
        diag = self.diagnose(closed, current_price=price)
        if not diag.get("would_signal"):
            return None
        entry = float(diag.get("entry") or price)
        stop = float(diag.get("stop") or 0)
        if entry <= 0 or stop <= 0:
            return None
        pattern = str(diag.get("pattern") or "channel")
        side = SignalType.LONG if diag.get("direction") == "long" else SignalType.SHORT
        return Signal(
            symbol=symbol,
            signal_type=side,
            strategy=self.config.name,
            entry_price=Decimal(str(entry)),
            stop_loss=Decimal(str(stop)),
            confidence=0.55,
            metadata={"zeus_pattern": pattern, "reason": f"{pattern} channel"},
        )
