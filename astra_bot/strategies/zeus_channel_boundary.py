"""Zeus Channel Boundary (4h) — lessons 4/5/7.

Parallel channel: entry from boundary (not mid).
Paper research. enabled=False by default.

Zeus fixes (paper-only):
- min_stop_pct: no micro-stops
- HTF bias: mean-reversion only with trend, breakouts allowed with trend
- multi-level TP (partial + runner)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


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


def _htf_bias(closes: list[float]) -> str:
    """Simple 4h bias from EMA8 vs EMA21 on channel window."""
    if len(closes) < 8:
        return "neutral"

    def ema(vals: list[float], n: int) -> float:
        k = 2.0 / (n + 1)
        e = vals[0]
        for v in vals[1:]:
            e = v * k + e * (1 - k)
        return e

    fast = ema(closes, 8)
    slow = ema(closes, min(21, len(closes)))
    if fast > slow * 1.001:
        return "up"
    if fast < slow * 0.999:
        return "down"
    return "neutral"


def _enforce_min_stop(
    direction: str, price: float, stop: float, min_stop_pct: float
) -> float:
    if price <= 0 or min_stop_pct <= 0:
        return stop
    if direction == "long":
        floor = price * (1.0 - min_stop_pct)
        return min(stop, floor)
    ceil = price * (1.0 + min_stop_pct)
    return max(stop, ceil)


@dataclass
class ZeusChannelBoundaryConfig(StrategyConfig):
    name: str = "zeus_channel_boundary_4h"
    enabled: bool = False
    preferred_timeframe: str = "4h"
    # Proxy v1 (owner 01.11): author silent on numbers — bot assumptions, labeled.
    lookback: int = 48  # bot_proxy; author: no fixed window
    min_touches: int = 3
    parallel_tol: float = 0.25  # tighter vs ideal clone (author: clone line)
    use_parallel_clone: bool = True  # mean slope, dual intercepts
    min_width_pct: float = 0.012
    max_width_pct: float = 0.12
    # near = fraction of CHANNEL WIDTH (not price %). Author frames ~0–10% width; owner 20%.
    near_boundary_width_frac: float = 0.20
    near_boundary_pct: float = 0.015  # legacy price-% fallback if width_frac<=0
    stop_buffer_pct: float = 0.003
    min_rr: float = 1.5
    enable_breakout_hold: bool = True  # naked hold still on (author: risky ok)
    min_hold_bars: int = 2
    max_hold_bars: int = 8
    # Retest path: breakout → first return within 0.15*ATR → bounce
    enable_breakout_retest: bool = True
    retest_atr_mult: float = 0.15  # bot_proxy
    retest_done_width_frac: float = 0.50  # retreat ≥0.5×width → setup dead
    min_stop_pct: float = 0.01  # owner proxy v1
    require_htf_bias: bool = True
    tp1_rr: float = 1.5
    tp2_rr: float = 3.0
    tp1_fraction: float = 0.4
    tp2_fraction: float = 0.6



def _atr14(highs: list[float], lows: list[float], closes: list[float]) -> float:
    n = len(closes)
    if n < 2:
        return max(highs[-1] - lows[-1], 1e-12) if highs else 1e-12
    trs: list[float] = []
    start = max(1, n - 14)
    for i in range(start, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else max(highs[-1] - lows[-1], 1e-12)


def _retest_mode() -> str:
    """ZEUS_RETEST_V1: shadow=log only, live=signal, else off for would_signal."""
    import os
    v = (os.environ.get("ZEUS_RETEST_V1") or "shadow").strip().lower()
    if v in ("1", "true", "yes", "live"):
        return "live"
    if v in ("0", "false", "no", "off"):
        return "off"
    return "shadow"


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
        if len(candles) < n:
            out["reject_reason"] = "not_enough_bars"
            out["stage"] = "data"
            return out

        window = candles[-n:]
        highs = [float(x.high) for x in window]
        lows = [float(x.low) for x in window]
        closes = [float(x.close) for x in window]
        last = window[-1]
        price = float(current_price) if current_price is not None else float(last.close)
        bias = _htf_bias(closes)
        out["htf_bias"] = bias

        slope_h, int_h = _linreg(highs)
        slope_l, int_l = _linreg(lows)
        if getattr(c, "use_parallel_clone", True):
            # Author: clone one line — shared slope, separate intercepts (bot_proxy fit).
            slope = 0.5 * (slope_h + slope_l)
            n_w = float(len(window))
            xs = list(range(len(window)))
            # intercepts so mean residual on each side is 0 under common slope
            int_h = sum(highs[i] - slope * xs[i] for i in xs) / n_w
            int_l = sum(lows[i] - slope * xs[i] for i in xs) / n_w
            slope_h = slope_l = slope
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

        width_abs = max(upper - lower, 1e-12)
        frac = float(getattr(c, "near_boundary_width_frac", 0) or 0)
        if frac > 0:
            near_lo = price <= lower + frac * width_abs
            near_hi = price >= upper - frac * width_abs
        else:
            near_lo = price <= lower * (1 + c.near_boundary_pct)
            near_hi = price >= upper * (1 - c.near_boundary_pct)
        out["near_lo"] = near_lo
        out["near_hi"] = near_hi

        bars_out = 0
        for bar in reversed(window):
            cl = float(bar.close)
            if cl > upper * 1.001 or cl < lower * 0.999:
                bars_out += 1
            else:
                break
        out["bars_outside"] = bars_out

        # --- breakout + first retest (ZEUS priority path) ---
        atr = _atr14(highs, lows, closes)
        retest_tol = float(getattr(c, "retest_atr_mult", 0.15)) * atr
        mode = _retest_mode()
        out["retest_mode"] = mode
        if getattr(c, "enable_breakout_retest", True) and mode != "off":
            # scan last bars: was outside, now near line from outside, close still ok
            retest_hit = None  # (side long|short)
            for k in range(2, min(12, len(window))):
                past = window[-k]
                pc = float(past.close)
                # prior close beyond upper then current near upper from above zone
                if pc > upper * 1.001 and abs(price - upper) <= retest_tol and price >= upper - retest_tol:
                    # not already run away: distance from upper < 0.5 width
                    if abs(price - upper) <= float(getattr(c, "retest_done_width_frac", 0.5)) * width_abs:
                        retest_hit = "long"
                        break
                if pc < lower * 0.999 and abs(price - lower) <= retest_tol and price <= lower + retest_tol:
                    if abs(price - lower) <= float(getattr(c, "retest_done_width_frac", 0.5)) * width_abs:
                        retest_hit = "short"
                        break
            if retest_hit == "long":
                if not (c.require_htf_bias and bias == "down"):
                    stop = _enforce_min_stop(
                        "long", price, lower * (1 - c.stop_buffer_pct), c.min_stop_pct
                    )
                    risk = price - stop
                    if risk > 0:
                        target = price + risk * c.min_rr
                        out["pattern_retest"] = "channel_breakout_up_retest"
                        out["shadow_retest"] = {
                            "side": "long",
                            "entry": price,
                            "stop": stop,
                            "target": target,
                            "upper": upper,
                            "lower": lower,
                        }
                        if mode == "live":
                            out.update(
                                would_signal=True,
                                direction="long",
                                pattern="channel_breakout_up_retest",
                                entry=price,
                                stop=stop,
                                target=target,
                            )
                            return out
            if retest_hit == "short":
                if not (c.require_htf_bias and bias == "up"):
                    stop = _enforce_min_stop(
                        "short", price, upper * (1 + c.stop_buffer_pct), c.min_stop_pct
                    )
                    risk = stop - price
                    if risk > 0:
                        target = price - risk * c.min_rr
                        out["pattern_retest"] = "channel_breakout_down_retest"
                        out["shadow_retest"] = {
                            "side": "short",
                            "entry": price,
                            "stop": stop,
                            "target": target,
                            "upper": upper,
                            "lower": lower,
                        }
                        if mode == "live":
                            out.update(
                                would_signal=True,
                                direction="short",
                                pattern="channel_breakout_down_retest",
                                entry=price,
                                stop=stop,
                                target=target,
                            )
                            return out

        if c.enable_breakout_hold and c.min_hold_bars <= bars_out <= c.max_hold_bars:
            if price > upper:
                if c.require_htf_bias and bias == "down":
                    out["reject_reason"] = "counter_trend_breakout_up"
                    out["stage"] = "bias"
                    return out
                stop = _enforce_min_stop(
                    "long", price, lower * (1 - c.stop_buffer_pct), c.min_stop_pct
                )
                risk = price - stop
                if risk <= 0:
                    out["reject_reason"] = "invalid_risk"
                    return out
                target = price + risk * c.min_rr
                out.update(
                    would_signal=True,
                    direction="long",
                    pattern="channel_breakout_up_hold",
                    entry=price,
                    stop=stop,
                    target=target,
                )
                return out
            if price < lower:
                if c.require_htf_bias and bias == "up":
                    out["reject_reason"] = "counter_trend_breakout_down"
                    out["stage"] = "bias"
                    return out
                stop = _enforce_min_stop(
                    "short", price, upper * (1 + c.stop_buffer_pct), c.min_stop_pct
                )
                risk = stop - price
                if risk <= 0:
                    out["reject_reason"] = "invalid_risk"
                    return out
                target = price - risk * c.min_rr
                out.update(
                    would_signal=True,
                    direction="short",
                    pattern="channel_breakout_down_hold",
                    entry=price,
                    stop=stop,
                    target=target,
                )
                return out

        if abs(price - mid) / mid < 0.35 * width_pct:
            out["reject_reason"] = "middle_of_channel"
            return out

        if near_lo:
            if c.require_htf_bias and bias == "down":
                out["reject_reason"] = "counter_trend_long_bias"
                out["stage"] = "bias"
                return out
            stop = _enforce_min_stop(
                "long", price, lower * (1 - c.stop_buffer_pct), c.min_stop_pct
            )
            risk = price - stop
            target = upper
            if risk > 0 and (target - price) / risk >= c.min_rr:
                out.update(
                    would_signal=True,
                    direction="long",
                    pattern="channel_bounce_lower",
                    entry=price,
                    stop=stop,
                    target=target,
                )
                return out
            out["reject_reason"] = "rr_fail_long"
            return out

        if near_hi:
            if c.require_htf_bias and bias == "up":
                out["reject_reason"] = "counter_trend_short_bias"
                out["stage"] = "bias"
                return out
            stop = _enforce_min_stop(
                "short", price, upper * (1 + c.stop_buffer_pct), c.min_stop_pct
            )
            risk = stop - price
            target = lower
            if risk > 0 and (price - target) / risk >= c.min_rr:
                out.update(
                    would_signal=True,
                    direction="short",
                    pattern="channel_bounce_upper",
                    entry=price,
                    stop=stop,
                    target=target,
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
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
        **kwargs: Any,
    ) -> Signal | None:
        if not self.config.enabled:
            return None
        try:
            price = float(current_price) if current_price is not None else (
                float(candles[-1].close) if candles else 0.0
            )
            diag = self.diagnose(candles, current_price=price)
            if not diag.get("would_signal"):
                return None

            entry = float(diag.get("entry") or price)
            stop = float(diag.get("stop") or 0)
            target = float(diag.get("target") or 0)
            if entry <= 0 or stop <= 0:
                return None
            c = self.config
            risk = abs(entry - stop)
            if risk / entry < c.min_stop_pct * 0.95:
                return None
            if target <= 0:
                if diag.get("direction") == "long":
                    target = entry + risk * float(c.min_rr)
                else:
                    target = entry - risk * float(c.min_rr)

            direction = (
                models.TradeDirection.LONG
                if diag.get("direction") == "long"
                else models.TradeDirection.SHORT
            )
            pattern = str(diag.get("pattern") or "channel")
            if direction == models.TradeDirection.LONG:
                tp1 = entry + risk * float(c.tp1_rr)
                tp2 = entry + risk * float(c.tp2_rr)
            else:
                tp1 = entry - risk * float(c.tp1_rr)
                tp2 = entry - risk * float(c.tp2_rr)
            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MEAN_REVERSION,
                direction=direction,
                entry_price=Decimal(str(entry)),
                stop_loss=Decimal(str(stop)),
                take_profit=Decimal(str(tp2)),
                position_size=Decimal("0"),
                risk_amount=Decimal("0"),
                confidence=0.55,
                market_regime=market_regime or "UNKNOWN",
                features={
                    "zeus_pattern": pattern,
                    "upper": diag.get("upper"),
                    "lower": diag.get("lower"),
                    "width_pct": diag.get("width_pct"),
                    "htf_bias": diag.get("htf_bias"),
                    "zeus_tp_levels": [tp1, tp2],
                    "zeus_tp_fractions": [c.tp1_fraction, c.tp2_fraction],
                    "min_stop_pct": c.min_stop_pct,
                },
            )
        except Exception as exc:
            logger.warning("%s evaluate error: %s", self.name, exc)
            return None

    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        candles: list[models.Candle],
        atr: float | None = None,
    ) -> Decimal:
        pct = Decimal(str(self.config.min_stop_pct or 0.008))
        return entry_price * (Decimal("1") - pct)

    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        candles: list[models.Candle],
    ) -> list[dict]:
        risk = abs(entry_price - stop_loss)
        c = self.config
        tp1 = entry_price + risk * Decimal(str(c.tp1_rr))
        tp2 = entry_price + risk * Decimal(str(c.tp2_rr))
        return [
            {"price": tp1, "fraction": float(c.tp1_fraction)},
            {"price": tp2, "fraction": float(c.tp2_fraction)},
        ]
