"""
Zeus Wedge False-Break + Retest (4h) — research / paper-clock.

Урок 8: ложный выход из клина → ретест границы → закрытие обратно внутрь
→ разворот в сторону возврата.

enabled=False по умолчанию (live/prod). Paper-clock runner включает явно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


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
    max_bars_outside: int = 6


class ZeusWedgeRetestStrategy(BaseStrategy[ZeusWedgeRetestConfig]):
    """Ложный пробой клина + ретест + закрытие внутрь → разворот."""

    preferred_timeframe: str = "4h"

    def __init__(self, config: ZeusWedgeRetestConfig | None = None):
        super().__init__(config or ZeusWedgeRetestConfig())
        self.config: ZeusWedgeRetestConfig
        self.preferred_timeframe = getattr(
            self.config, "preferred_timeframe", "4h"
        )

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
            c = self.config
            min_need = c.lookback + 3
            if not candles or len(candles) < min_need:
                return None

            tail_max = c.max_bars_outside + 1
            if len(candles) < c.lookback + 1:
                return None

            tail_len = min(tail_max, max(1, len(candles) - c.lookback))
            formation = candles[-(c.lookback + tail_len) : -tail_len]
            if len(formation) < c.lookback:
                return None

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
                return None
            width_pct = width / mid
            if width_pct > c.max_width_pct or width_pct < c.min_width_pct:
                return None

            is_rising = slope_lo > 0 and slope_hi > 0 and slope_hi <= slope_lo * 1.05
            is_falling = slope_hi < 0 and slope_lo < 0 and slope_lo >= slope_hi * 1.05
            if not (is_rising or is_falling):
                first_width = intercept_hi - intercept_lo
                if first_width <= 0 or width >= first_width * 0.95:
                    return None

            tail = candles[-tail_len:]
            if not tail:
                return None

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
                return None

            last = tail[-1]
            last_close = float(last.close)
            last_high = float(last.high)
            last_low = float(last.low)
            bars_after = len(tail) - 1 - breakout_idx
            if bars_after < 0 or bars_after > c.max_bars_outside:
                return None

            price = float(current_price or last_close)
            direction = None
            stop_price = 0.0
            target_price = 0.0
            pattern = ""

            if broken_up:
                retest_ok = last_high >= upper * (1.0 - tol)
                closed_inside = (
                    last_close <= upper * (1.0 + tol * 0.5) and last_close >= lower
                )
                if retest_ok and closed_inside and last_close < upper:
                    direction = models.TradeDirection.SHORT
                    stop_price = extreme_hi * (1.0 + c.stop_buffer_pct)
                    risk = stop_price - price
                    if risk <= 0:
                        return None
                    target_price = price - risk * c.min_rr
                    pattern = "false_break_up_retest_inside"

            elif broken_down:
                retest_ok = last_low <= lower * (1.0 + tol)
                closed_inside = (
                    last_close >= lower * (1.0 - tol * 0.5) and last_close <= upper
                )
                if retest_ok and closed_inside and last_close > lower:
                    direction = models.TradeDirection.LONG
                    stop_price = extreme_lo * (1.0 - c.stop_buffer_pct)
                    risk = price - stop_price
                    if risk <= 0:
                        return None
                    target_price = price + risk * c.min_rr
                    pattern = "false_break_down_retest_inside"

            if direction is None:
                return None

            risk = abs(price - stop_price)
            reward = abs(target_price - price)
            if risk <= 0 or round(reward / risk, 4) < c.min_rr:
                return None

            confidence = min(
                0.8, max(0.5, 0.55 + 0.1 * (1.0 - width_pct / c.max_width_pct))
            )

            features = {
                "zeus_pattern": pattern,
                "zeus_reason": (
                    f"{pattern}: wedge upper={upper:.4f} lower={lower:.4f} "
                    f"width_pct={width_pct:.4f} bars_outside={bars_after}"
                ),
                "wedge_upper": round(upper, 6),
                "wedge_lower": round(lower, 6),
                "width_pct": round(width_pct, 6),
                "bars_outside": bars_after,
                "slope_hi": round(slope_hi, 8),
                "slope_lo": round(slope_lo, 8),
                "is_rising_wedge": is_rising,
                "is_falling_wedge": is_falling,
                "stop_structure": "beyond_false_break_extreme",
                "min_rr": c.min_rr,
            }

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

    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        candles: list[models.Candle],
        atr: float | None = None,
    ) -> Decimal:
        return entry_price * Decimal("0.995")

    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        candles: list[models.Candle],
    ) -> list[dict]:
        return [{"price": entry_price * Decimal("1.01"), "fraction": 1.0}]
