"""
Volatility Breakout Strategy.

Сжатие волатильности: ATR(7) / ATR(28) < 0.7 минимум 10 баров.
Первый пробой хая/лоу сжатия с объёмом > 1.3x SMA20, направление по ROC14.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import calculate_atr, simple_moving_average
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class VolatilityBreakoutConfig(StrategyConfig):
    name: str = "volatility_breakout"
    enabled: bool = True
    squeeze_ratio: float = 0.7
    squeeze_min_bars: int = 10
    volume_mult: float = 1.3
    roc_period: int = 14
    stop_buffer_pct: float = 0.002
    min_rr: float = 1.5


class VolatilityBreakoutStrategy(BaseStrategy[VolatilityBreakoutConfig]):
    """Стратегия пробоя волатильности после сжатия."""

    def __init__(self, config: VolatilityBreakoutConfig | None = None):
        super().__init__(config or VolatilityBreakoutConfig())
        self.config: VolatilityBreakoutConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        try:
            c = self.config
            if not candles or len(candles) < 28 + c.squeeze_min_bars:
                return None

            history = candles[:-1]
            if len(history) < 28 + c.squeeze_min_bars:
                return None

            highs = [float(x.high) for x in history]
            lows = [float(x.low) for x in history]
            closes = [float(x.close) for x in history]
            vols = [float(x.volume) for x in history]

            squeeze_bars_count = 0
            for i in range(len(history), len(history) - c.squeeze_min_bars - 5, -1):
                h_sub = highs[:i]
                l_sub = lows[:i]
                c_sub = closes[:i]
                if len(c_sub) < 28:
                    break
                atr7 = calculate_atr(h_sub[-15:], l_sub[-15:], c_sub[-15:], period=7)
                atr28 = calculate_atr(h_sub[-30:], l_sub[-30:], c_sub[-30:], period=28)
                if atr7 and atr28 and atr28 > 0 and (atr7 / atr28) < c.squeeze_ratio:
                    squeeze_bars_count += 1
                else:
                    if squeeze_bars_count >= c.squeeze_min_bars:
                        break

            if squeeze_bars_count < c.squeeze_min_bars:
                return None

            squeeze_range = history[-c.squeeze_min_bars:]
            sq_high = max(float(x.high) for x in squeeze_range)
            sq_low = min(float(x.low) for x in squeeze_range)

            curr_candle = candles[-1]
            price = float(current_price or curr_candle.close)
            curr_vol = float(curr_candle.volume)

            avg_vol = simple_moving_average(vols[-20:], 20)
            if not avg_vol or curr_vol < avg_vol * c.volume_mult:
                return None

            if len(closes) < c.roc_period:
                return None
            roc14 = (price - closes[-c.roc_period]) / closes[-c.roc_period]

            direction = None
            stop_price = 0.0
            target_price = 0.0

            if price > sq_high and roc14 > 0:
                direction = models.TradeDirection.LONG
                stop_price = sq_low * (1.0 - c.stop_buffer_pct)
                target_price = price + (price - stop_price) * c.min_rr
            elif price < sq_low and roc14 < 0:
                direction = models.TradeDirection.SHORT
                stop_price = sq_high * (1.0 + c.stop_buffer_pct)
                target_price = price - (stop_price - price) * c.min_rr

            if direction is None:
                return None

            risk = abs(price - stop_price)
            reward = abs(target_price - price)
            if risk <= 0 or round(reward / risk, 4) < c.min_rr:
                return None

            vol_ratio = curr_vol / avg_vol
            confidence = min(0.85, max(0.5, 0.5 + 0.1 * (vol_ratio - c.volume_mult)))

            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MOMENTUM,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(stop_price)),
                take_profit=Decimal(str(target_price)),
                position_size=Decimal("0"),
                risk_amount=Decimal("0"),
                confidence=confidence,
                market_regime=market_regime or "UNKNOWN",
            )
        except Exception:
            return None

    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        return entry_price * Decimal("0.995")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.01"), "fraction": 1.0}]
