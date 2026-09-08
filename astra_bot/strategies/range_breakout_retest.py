"""
Range Breakout and Retest Strategy.

Боковик: ADX < 20 минимум range_min_bars баров.
Пробой закрытием границы боковика + ретест снаружи (возврат к уровню без закрытия внутри боковика).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..engines.regime_detector import MarketRegimeDetector
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class RangeBreakoutRetestConfig(StrategyConfig):
    name: str = "range_breakout_retest"
    enabled: bool = True
    range_min_bars: int = 15
    adx_threshold: float = 20.0
    retest_tolerance: float = 0.002
    stop_buffer_pct: float = 0.002
    min_rr: float = 1.5


class RangeBreakoutRetestStrategy(BaseStrategy[RangeBreakoutRetestConfig]):
    """Стратегия пробоя и ретеста границы боковика (Range Breakout & Retest)."""

    def __init__(self, config: RangeBreakoutRetestConfig | None = None):
        super().__init__(config or RangeBreakoutRetestConfig())
        self.config: RangeBreakoutRetestConfig

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
            if not candles or len(candles) < c.range_min_bars + 5:
                return None

            history = candles[:-1]
            if len(history) < c.range_min_bars + 5:
                return None

            highs = [float(x.high) for x in history]
            lows = [float(x.low) for x in history]
            closes = [float(x.close) for x in history]

            adx_val = MarketRegimeDetector._calculate_adx(highs, lows, closes, period=14)
            if adx_val > c.adx_threshold:
                return None

            range_candles = history[-c.range_min_bars - 1 : -1]
            if len(range_candles) < c.range_min_bars:
                return None

            range_high = max(float(x.high) for x in range_candles)
            range_low = min(float(x.low) for x in range_candles)

            breakout_candle = history[-1]
            breakout_close = float(breakout_candle.close)

            current_candle = candles[-1]
            price = float(current_price or current_candle.close)
            curr_low = float(current_candle.low)
            curr_high = float(current_candle.high)
            curr_close = float(current_candle.close)

            direction = None
            stop_price = 0.0
            target_price = 0.0

            if breakout_close > range_high:
                if curr_low <= range_high * (1.0 + c.retest_tolerance) and curr_close >= range_high:
                    direction = models.TradeDirection.LONG
                    stop_price = range_high * (1.0 - c.stop_buffer_pct)
                    target_price = price + (price - stop_price) * c.min_rr

            elif breakout_close < range_low:
                if curr_high >= range_low * (1.0 - c.retest_tolerance) and curr_close <= range_low:
                    direction = models.TradeDirection.SHORT
                    stop_price = range_low * (1.0 + c.stop_buffer_pct)
                    target_price = price - (stop_price - price) * c.min_rr

            if direction is None:
                return None

            risk = abs(price - stop_price)
            reward = abs(target_price - price)
            if risk <= 0 or round(reward / risk, 4) < c.min_rr:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 0.01 * (c.adx_threshold - adx_val)))

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
