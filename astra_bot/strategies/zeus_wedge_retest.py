"""
Zeus Wedge False-Break + Retest (4h) — research / paper-clock.

Урок 8: ложный выход из клина → ретест → close inside → разворот.
Уроки 5/7 + иерархия ТФ: 4h — основной ход структуры; если пробой
**удержался снаружи** (true breakout) — вход по направлению пробоя,
а не только short после возврата внутрь.

enabled=False по умолчанию (live/prod). Paper-clock runner включает явно.

diagnose() — снимок структуры + reason отказа (память, без ордера).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


def _htf_bias_closes(closes: list[float]) -> str:
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


def _enforce_min_stop(direction: str, price: float, stop: float, min_stop_pct: float) -> float:
    if price <= 0 or min_stop_pct <= 0:
        return stop
    if direction == "long":
        return min(stop, price * (1.0 - min_stop_pct))
    return max(stop, price * (1.0 + min_stop_pct))


def _linreg_slope(ys: list[float]) -> tuple[float, float]:
    """Simple OLS slope + intercept for y ~ a + b*x, x = 0..n-1."""
    n = len(ys)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    xs = list(range(n))
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys, strict=True))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return 0.0, sy / n
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n
    return b, a


@dataclass
class ZeusWedgeRetestConfig(StrategyConfig):
    name: str = "zeus_wedge_retest_4h"
    enabled: bool = False  # prod default off; paper-clock sets True
    preferred_timeframe: str = "4h"
    lookback: int = 24
    min_touches: int = 3
    max_width_pct: float = 0.08
    min_width_pct: float = 0.008
    breakout_buffer_pct: float = 0.001
    retest_tolerance_pct: float = 0.003
    stop_buffer_pct: float = 0.002
    min_rr: float = 1.5
    max_bars_outside: int = 5
    # True-breakout: пробой удержался снаружи → вход по тренду пробоя.
    enable_true_breakout: bool = True
    min_hold_bars_breakout: int = 2
    max_bars_true_breakout: int = 8
    min_stop_pct: float = 0.008
    require_htf_bias: bool = True
    tp1_rr: float = 1.5
    tp2_rr: float = 3.0
    tp1_fraction: float = 0.4
    tp2_fraction: float = 0.6


class ZeusWedgeRetestStrategy(BaseStrategy[ZeusWedgeRetestConfig]):
    """Клин 4h: false-break retest (разворот) + true-breakout hold (продолжение)."""

    preferred_timeframe: str = "4h"

    def __init__(self, config: ZeusWedgeRetestConfig | None = None):
        super().__init__(config or ZeusWedgeRetestConfig())
        self.config: ZeusWedgeRetestConfig
        self.preferred_timeframe = getattr(
            self.config, "preferred_timeframe", "4h"
        )

    def diagnose(
        self,
        candles: list[models.Candle],
        current_price: float | None = None,
    ) -> dict[str, Any]:
        """Снимок 4h-структуры + reason, почему сигнала нет (research memory)."""
        out: dict[str, Any] = {
            "has_wedge": False,
            "would_signal": False,
            "reject_reason": "insufficient_data",
            "stage": "data",
        }
        c = self.config
        min_need = c.lookback + 3
        if not candles or len(candles) < min_need:
            return out

        tail_max = max(
            c.max_bars_outside + 1,
            int(getattr(c, "max_bars_true_breakout", 6)) + 1,
        )
        if len(candles) < c.lookback + 1:
            return out

        tail_len = min(tail_max, max(1, len(candles) - c.lookback))
        formation = candles[-(c.lookback + tail_len) : -tail_len]
        if len(formation) < c.lookback:
            out["reject_reason"] = "formation_short"
            return out

        highs = [float(x.high) for x in formation]
        lows = [float(x.low) for x in formation]
        slope_hi, intercept_hi = _linreg_slope(highs)
        slope_lo, intercept_lo = _linreg_slope(lows)
        n = len(formation)
        upper_end = intercept_hi + slope_hi * (n - 1)
        lower_end = intercept_lo + slope_lo * (n - 1)
        recent_hi = max(highs[-3:])
        recent_lo = min(lows[-3:])
        upper = max(upper_end, recent_hi * 0.999)
        lower = min(lower_end, recent_lo * 1.001)
        width = upper - lower
        mid = (upper + lower) / 2.0
        if mid <= 0:
            out["reject_reason"] = "invalid_mid"
            return out
        width_pct = width / mid

        snap = {
            "wedge_upper": round(upper, 6),
            "wedge_lower": round(lower, 6),
            "width_pct": round(width_pct, 6),
            "slope_hi": round(slope_hi, 8),
            "slope_lo": round(slope_lo, 8),
        }
        out.update(snap)

        if width_pct > c.max_width_pct or width_pct < c.min_width_pct:
            out["reject_reason"] = "width_out_of_band"
            out["stage"] = "structure"
            return out

        is_rising = slope_lo > 0 and slope_hi > 0 and slope_hi <= slope_lo * 1.05
        is_falling = slope_hi < 0 and slope_lo < 0 and slope_lo >= slope_hi * 1.05
        out["is_rising_wedge"] = is_rising
        out["is_falling_wedge"] = is_falling

        if not (is_rising or is_falling):
            first_width = intercept_hi - intercept_lo
            if first_width <= 0 or width >= first_width * 0.95:
                out["reject_reason"] = "not_converging_wedge"
                out["stage"] = "structure"
                return out

        out["has_wedge"] = True
        out["stage"] = "pattern"

        tail = candles[-tail_len:]
        if not tail:
            out["reject_reason"] = "empty_tail"
            return out

        buf = c.breakout_buffer_pct
        tol = c.retest_tolerance_pct
        broken_up = False
        broken_down = False
        extreme_hi = upper
        extreme_lo = lower
        breakout_idx = -1

        for i, bar in enumerate(tail):
            cl = float(bar.close)
            hi = float(bar.high)
            lo = float(bar.low)
            if not broken_up and not broken_down:
                if cl > upper * (1.0 + buf):
                    broken_up = True
                    breakout_idx = i
                    extreme_hi = hi
                elif cl < lower * (1.0 - buf):
                    broken_down = True
                    breakout_idx = i
                    extreme_lo = lo
            else:
                if broken_up:
                    extreme_hi = max(extreme_hi, hi)
                if broken_down:
                    extreme_lo = min(extreme_lo, lo)

        if breakout_idx < 0:
            out["reject_reason"] = "no_false_break"
            out["stage"] = "breakout"
            return out

        last = tail[-1]
        last_close = float(last.close)
        last_high = float(last.high)
        last_low = float(last.low)
        bars_after = len(tail) - 1 - breakout_idx
        out["bars_outside"] = bars_after
        out["broken_up"] = broken_up
        out["broken_down"] = broken_down

        if bars_after < 0:
            out["reject_reason"] = "bars_outside_limit"
            out["stage"] = "breakout"
            return out

        price = float(last_close)

        if broken_up:
            retest_ok = last_high >= upper * (1.0 - tol)
            closed_inside = (
                last_close <= upper * (1.0 + tol * 0.5) and last_close >= lower
            )
            still_outside = last_close > upper * (1.0 + buf * 0.5)

            if (
                getattr(c, "enable_true_breakout", True)
                and still_outside
                and bars_after >= int(getattr(c, "min_hold_bars_breakout", 2))
                and bars_after <= int(getattr(c, "max_bars_true_breakout", 6))
            ):
                stop_price = lower * (1.0 - c.stop_buffer_pct)
                brk_low = min(
                    float(b.low) for b in tail[breakout_idx : breakout_idx + 1]
                )
                stop_price = min(stop_price, brk_low * (1.0 - c.stop_buffer_pct))
                stop_price = _enforce_min_stop(
                    "long", price, stop_price, float(getattr(c, "min_stop_pct", 0.008))
                )
                closes_all = [float(x.close) for x in candles[-(c.lookback + tail_len) :]]
                bias = _htf_bias_closes(closes_all)
                out["htf_bias"] = bias
                if getattr(c, "require_htf_bias", True) and bias == "down":
                    out["reject_reason"] = "counter_trend_true_breakout_up"
                    out["stage"] = "bias"
                    return out
                risk = price - stop_price
                if risk <= 0:
                    out["reject_reason"] = "invalid_risk"
                    out["stage"] = "risk"
                    return out
                if risk / price < float(getattr(c, "min_stop_pct", 0.008)) * 0.95:
                    out["reject_reason"] = "stop_too_tight"
                    out["stage"] = "risk"
                    return out
                target = price + risk * c.min_rr
                rr = abs(target - price) / risk if risk else 0.0
                if rr < c.min_rr:
                    out["reject_reason"] = "rr_too_low"
                    out["stage"] = "risk"
                    return out
                out["would_signal"] = True
                out["reject_reason"] = ""
                out["direction"] = "long"
                out["pattern"] = "true_breakout_up_hold"
                out["stage"] = "signal"
                out["structure_role"] = "4h_main"
                out["entry"] = price
                out["stop"] = stop_price
                out["target"] = target
                return out

            if bars_after > c.max_bars_outside:
                out["reject_reason"] = "bars_outside_limit"
                out["stage"] = "breakout"
                return out
            if not (retest_ok and closed_inside and last_close < upper):
                if still_outside and bars_after < int(
                    getattr(c, "min_hold_bars_breakout", 2)
                ):
                    out["reject_reason"] = "waiting_true_breakout_hold"
                    out["stage"] = "breakout"
                else:
                    out["reject_reason"] = "no_retest_close_inside"
                    out["stage"] = "retest"
                return out
            stop_price = extreme_hi * (1.0 + c.stop_buffer_pct)
            stop_price = _enforce_min_stop(
                "short", price, stop_price, float(getattr(c, "min_stop_pct", 0.008))
            )
            risk = stop_price - price
            if risk <= 0:
                out["reject_reason"] = "invalid_risk"
                return out
            target = price - risk * c.min_rr
            rr = abs(target - price) / risk if risk else 0.0
            if rr < c.min_rr:
                out["reject_reason"] = "rr_too_low"
                out["stage"] = "risk"
                return out
            out["would_signal"] = True
            out["reject_reason"] = ""
            out["direction"] = "short"
            out["pattern"] = "false_break_up_retest_inside"
            out["stage"] = "signal"
            return out

        if broken_down:
            retest_ok = last_low <= lower * (1.0 + tol)
            closed_inside = (
                last_close >= lower * (1.0 - tol * 0.5) and last_close <= upper
            )
            still_outside = last_close < lower * (1.0 - buf * 0.5)

            if (
                getattr(c, "enable_true_breakout", True)
                and still_outside
                and bars_after >= int(getattr(c, "min_hold_bars_breakout", 2))
                and bars_after <= int(getattr(c, "max_bars_true_breakout", 6))
            ):
                stop_price = upper * (1.0 + c.stop_buffer_pct)
                brk_hi = max(
                    float(b.high) for b in tail[breakout_idx : breakout_idx + 1]
                )
                stop_price = max(stop_price, brk_hi * (1.0 + c.stop_buffer_pct))
                stop_price = _enforce_min_stop(
                    "short", price, stop_price, float(getattr(c, "min_stop_pct", 0.008))
                )
                closes_all = [float(x.close) for x in candles[-(c.lookback + tail_len) :]]
                bias = _htf_bias_closes(closes_all)
                out["htf_bias"] = bias
                if getattr(c, "require_htf_bias", True) and bias == "up":
                    out["reject_reason"] = "counter_trend_true_breakout_down"
                    out["stage"] = "bias"
                    return out
                risk = stop_price - price
                if risk <= 0:
                    out["reject_reason"] = "invalid_risk"
                    out["stage"] = "risk"
                    return out
                if risk / price < float(getattr(c, "min_stop_pct", 0.008)) * 0.95:
                    out["reject_reason"] = "stop_too_tight"
                    out["stage"] = "risk"
                    return out
                target = price - risk * c.min_rr
                rr = abs(target - price) / risk if risk else 0.0
                if rr < c.min_rr:
                    out["reject_reason"] = "rr_too_low"
                    out["stage"] = "risk"
                    return out
                out["would_signal"] = True
                out["reject_reason"] = ""
                out["direction"] = "short"
                out["pattern"] = "true_breakout_down_hold"
                out["stage"] = "signal"
                out["structure_role"] = "4h_main"
                out["entry"] = price
                out["stop"] = stop_price
                out["target"] = target
                return out

            if bars_after > c.max_bars_outside:
                out["reject_reason"] = "bars_outside_limit"
                out["stage"] = "breakout"
                return out
            if not (retest_ok and closed_inside and last_close > lower):
                if still_outside and bars_after < int(
                    getattr(c, "min_hold_bars_breakout", 2)
                ):
                    out["reject_reason"] = "waiting_true_breakout_hold"
                    out["stage"] = "breakout"
                else:
                    out["reject_reason"] = "no_retest_close_inside"
                    out["stage"] = "retest"
                return out
            stop_price = extreme_lo * (1.0 - c.stop_buffer_pct)
            stop_price = _enforce_min_stop(
                "long", price, stop_price, float(getattr(c, "min_stop_pct", 0.008))
            )
            risk = price - stop_price
            if risk <= 0:
                out["reject_reason"] = "invalid_risk"
                return out
            target = price + risk * c.min_rr
            rr = abs(target - price) / risk if risk else 0.0
            if rr < c.min_rr:
                out["reject_reason"] = "rr_too_low"
                out["stage"] = "risk"
                return out
            out["would_signal"] = True
            out["reject_reason"] = ""
            out["direction"] = "long"
            out["pattern"] = "false_break_down_retest_inside"
            out["stage"] = "signal"
            return out

        out["reject_reason"] = "no_direction"
        return out

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        if not self.config.enabled:
            logger.debug("%s: Strategy disabled", self.name)
            return None

        try:
            diag = self.diagnose(candles, current_price=current_price)
            if not diag.get("would_signal"):
                return None

            c = self.config
            tail_max = max(
                c.max_bars_outside + 1,
                int(getattr(c, "max_bars_true_breakout", 6)) + 1,
            )
            tail_len = min(tail_max, max(1, len(candles) - c.lookback))
            formation = candles[-(c.lookback + tail_len) : -tail_len]
            highs = [float(x.high) for x in formation]
            lows = [float(x.low) for x in formation]
            slope_hi, intercept_hi = _linreg_slope(highs)
            slope_lo, intercept_lo = _linreg_slope(lows)
            n = len(formation)
            upper = max(
                intercept_hi + slope_hi * (n - 1), max(highs[-3:]) * 0.999
            )
            lower = min(
                intercept_lo + slope_lo * (n - 1), min(lows[-3:]) * 1.001
            )
            width_pct = float(diag.get("width_pct") or 0.0)
            tail = candles[-tail_len:]
            last = tail[-1]
            price = float(last.close)
            pattern = str(diag.get("pattern") or "")
            bars_after = int(diag.get("bars_outside") or 0)

            if diag.get("direction") == "short":
                direction = models.TradeDirection.SHORT
                if pattern.startswith("true_breakout"):
                    stop_price = upper * (1.0 + c.stop_buffer_pct)
                else:
                    extreme_hi = max(float(b.high) for b in tail)
                    stop_price = extreme_hi * (1.0 + c.stop_buffer_pct)
                risk = stop_price - price
                target_price = price - risk * c.min_rr
            else:
                direction = models.TradeDirection.LONG
                if pattern.startswith("true_breakout"):
                    stop_price = lower * (1.0 - c.stop_buffer_pct)
                else:
                    extreme_lo = min(float(b.low) for b in tail)
                    stop_price = extreme_lo * (1.0 - c.stop_buffer_pct)
                risk = price - stop_price
                target_price = price + risk * c.min_rr

            if risk <= 0:
                logger.warning(
                    "%s: would_signal but risk<=0 at signal close=%.4f stop=%.4f",
                    self.name,
                    price,
                    stop_price,
                )
                return None

            confidence = min(0.85, 0.55 + width_pct * 2.0)
            features = {
                "zeus_pattern": pattern,
                "reason": (
                    f"{pattern}: wedge upper={upper:.4f} lower={lower:.4f} "
                    f"width_pct={width_pct:.4f} bars_outside={bars_after}"
                ),
                "wedge_upper": round(upper, 6),
                "wedge_lower": round(lower, 6),
                "width_pct": round(width_pct, 6),
                "bars_outside": bars_after,
                "slope_hi": round(slope_hi, 8),
                "slope_lo": round(slope_lo, 8),
                "is_rising_wedge": bool(diag.get("is_rising_wedge")),
                "is_falling_wedge": bool(diag.get("is_falling_wedge")),
                "structure_role": diag.get("structure_role") or "4h_main",
                "stop_structure": (
                    "beyond_breakout_extreme"
                    if pattern.startswith("true_breakout")
                    else "beyond_false_break_extreme"
                ),
                "min_rr": c.min_rr,
            }

            stop_price = _enforce_min_stop(
                "long" if direction == models.TradeDirection.LONG else "short",
                price,
                stop_price,
                float(getattr(c, "min_stop_pct", 0.008)),
            )
            risk = abs(price - stop_price)
            if risk <= 0 or risk / price < float(getattr(c, "min_stop_pct", 0.008)) * 0.95:
                return None
            if direction == models.TradeDirection.LONG:
                target_price = price + risk * float(getattr(c, "tp2_rr", c.min_rr))
                tp1 = price + risk * float(getattr(c, "tp1_rr", 1.5))
                tp2 = target_price
            else:
                target_price = price - risk * float(getattr(c, "tp2_rr", c.min_rr))
                tp1 = price - risk * float(getattr(c, "tp1_rr", 1.5))
                tp2 = target_price
            features["zeus_tp_levels"] = [tp1, tp2]
            features["zeus_tp_fractions"] = [
                float(getattr(c, "tp1_fraction", 0.4)),
                float(getattr(c, "tp2_fraction", 0.6)),
            ]
            features["min_stop_pct"] = float(getattr(c, "min_stop_pct", 0.008))
            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MEAN_REVERSION,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(stop_price)),
                take_profit=Decimal(str(target_price)),
                position_size=Decimal("0"),
                risk_amount=Decimal("0"),
                confidence=confidence,
                market_regime=market_regime or "UNKNOWN",
                features=features,
            )
        except Exception as exc:
            logger.warning("%s evaluate error: %s", self.name, exc)
            return None

    def calculate_stop_loss(self, entry_price, direction, atr_value=None, **kwargs):
        return None

    def calculate_take_profit(self, entry_price, stop_loss, direction, **kwargs):
        return None
