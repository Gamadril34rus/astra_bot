"""
Fair Value Gap (FVG) Strategy.

Бычий FVG: low[i] > high[i-2]
Медвежий FVG: high[i] < low[i-2]
Вход на ретесте зоны FVG по тренду EMA50. Зона протухает через max_age_bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import exponential_moving_average
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class FairValueGapConfig(StrategyConfig):
    name: str = "fair_value_gap"
    enabled: bool = True
    min_gap_pct: float = 0.002
    max_age_bars: int = 30
    ema_period: int = 50
    stop_buffer_pct: float = 0.002
    min_rr: float = 1.2


class FairValueGapStrategy(BaseStrategy[FairValueGapConfig]):
    """Стратегия Fair Value Gap (дисбаланс ликвидности)."""

    def __init__(self, config: FairValueGapConfig | None = None):
        super().__init__(config or FairValueGapConfig())
        self.config: FairValueGapConfig

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
            if not candles or len(candles) < c.ema_period + 5:
                return None

            history = candles[:-1]
            if len(history) < 3:
                return None

            closes = [float(x.close) for x in candles]
            ema_val = exponential_moving_average(closes, c.ema_period)
            if not ema_val:
                return None

            current_candle = candles[-1]
            price = float(current_price or current_candle.close)

            bullish_fvg = None
            bearish_fvg = None

            start_idx = max(2, len(history) - c.max_age_bars)
            for i in range(len(history) - 1, start_idx - 1, -1):
                high_i_2 = float(history[i - 2].high)
                low_i_2 = float(history[i - 2].low)
                high_i = float(history[i].high)
                low_i = float(history[i].low)

                if low_i > high_i_2:
                    gap_pct = (low_i - high_i_2) / high_i_2
                    if gap_pct >= c.min_gap_pct:
                        bullish_fvg = (high_i_2, low_i, gap_pct)
                        break

                if high_i < low_i_2:
                    gap_pct = (low_i_2 - high_i) / low_i_2
                    if gap_pct >= c.min_gap_pct:
                        bearish_fvg = (high_i, low_i_2, gap_pct)
                        break

            direction = None
            stop_price = 0.0
            target_price = 0.0
            gap_pct = 0.0

            if bullish_fvg and price >= ema_val:
                fvg_bottom, fvg_top, gap_p = bullish_fvg
                if float(current_candle.low) <= fvg_top and float(current_candle.high) >= fvg_bottom:
                    direction = models.TradeDirection.LONG
                    stop_price = fvg_bottom * (1.0 - c.stop_buffer_pct)
                    target_price = price + (price - stop_price) * c.min_rr
                    gap_pct = gap_p

            elif bearish_fvg and price <= ema_val:
                fvg_bottom, fvg_top, gap_p = bearish_fvg
                if float(current_candle.high) >= fvg_bottom and float(current_candle.low) <= fvg_top:
                    direction = models.TradeDirection.SHORT
                    stop_price = fvg_top * (1.0 + c.stop_buffer_pct)
                    target_price = price - (stop_price - price) * c.min_rr
                    gap_pct = gap_p

            if direction is None:
                return None

            risk = abs(price - stop_price)
            reward = abs(target_price - price)
            if risk <= 0 or (reward / risk) < c.min_rr:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 20.0 * gap_pct))

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
