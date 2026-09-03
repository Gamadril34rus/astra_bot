"""
Scalp-стратегия для 5-минутного таймфрейма.

Более короткие EMA и широкие зоны RSI, чтобы генерировать больше
сигналов на 5m (для быстрого накопления реальных уроков). Стоп/тейк
короткие (0.5%/0.6%), что ограничивает риск на мелком таймфрейме.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import calculate_rsi, exponential_moving_average
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class Scalp5mConfig(StrategyConfig):
    name: str = "scalp5m"
    # TZ P0-4: отключена по умолчанию (PF 0.39, −4 086 USDT, 0/15 дней).
    # Включается только ручным флагом после re-validation.
    enabled: bool = False
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 50
    rsi_low: float = 35.0
    rsi_high: float = 70.0
    pullback_pct: float = 0.008
    stop_pct: float = 0.005
    rr: float = 1.2
    hold_bars: int = 8


class Scalp5mStrategy(BaseStrategy):
    """Частые трендовые входы на 5m."""

    def __init__(self, config: Scalp5mConfig | None = None):
        super().__init__(config or Scalp5mConfig())
        self.config: Scalp5mConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        c = self.config
        if len(candles) < c.ema_slow + 5:
            return None

        closes = [float(x.close) for x in candles]
        opens = [float(x.open) for x in candles]
        price = float(current_price or closes[-1])

        e9 = exponential_moving_average(closes[-c.ema_fast:], c.ema_fast)
        e21 = exponential_moving_average(closes[-c.ema_mid:], c.ema_mid)
        e50 = exponential_moving_average(closes[-c.ema_slow:], c.ema_slow)
        if not (e9 and e21 and e50):
            return None

        rsi_val = calculate_rsi(closes, period=14)
        if rsi_val is None:
            return None

        if abs(price - e21) / price > c.pullback_pct:
            return None
        if not (c.rsi_low <= rsi_val <= c.rsi_high):
            return None

        bull = e9 > e21 > e50
        bear = e9 < e21 < e50
        if bull and closes[-1] > opens[-1]:
            direction = models.TradeDirection.LONG
        elif bear and closes[-1] < opens[-1]:
            direction = models.TradeDirection.SHORT
        else:
            return None

        entry = Decimal(str(price))
        if direction == models.TradeDirection.LONG:
            stop = entry * (Decimal("1") - Decimal(str(c.stop_pct)))
            take = entry * (Decimal("1") + Decimal(str(c.stop_pct * c.rr)))
        else:
            stop = entry * (Decimal("1") + Decimal(str(c.stop_pct)))
            take = entry * (Decimal("1") - Decimal(str(c.stop_pct * c.rr)))

        closeness = 1.0 - min(1.0, abs(price - e21) / price / c.pullback_pct)
        confidence = min(0.85, 0.5 + 0.25 * closeness)

        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MOMENTUM,
            direction=direction,
            entry_price=entry,
            stop_loss=stop,
            take_profit=take,
            position_size=Decimal("0"),
            risk_amount=Decimal("0"),
            confidence=confidence,
            market_regime=market_regime or "UNKNOWN",
        )

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        return entry_price * Decimal("0.995")

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        return [{"price": entry_price * Decimal("1.006"), "fraction": 1.0}]
