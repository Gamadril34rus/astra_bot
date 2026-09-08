"""
Order Block Strategy.

Последняя медвежья свеча перед бычьим импульсом (≥3 свечей вверх, рост ≥1.5%) = бычий Order Block (OB).
Зеркально: последняя бычья свеча перед медвежьим импульсом = медвежий OB.
Вход на ретесте тела OB, стоп за тенью OB.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class OrderBlockConfig(StrategyConfig):
    name: str = "order_block"
    enabled: bool = True
    impulse_bars: int = 3
    impulse_min_pct: float = 0.015
    max_age_bars: int = 50
    stop_buffer_pct: float = 0.002
    min_rr: float = 1.2


class OrderBlockStrategy(BaseStrategy[OrderBlockConfig]):
    """Стратегия институциональных блоков ордеров (Order Block)."""

    def __init__(self, config: OrderBlockConfig | None = None):
        super().__init__(config or OrderBlockConfig())
        self.config: OrderBlockConfig

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
            if not candles or len(candles) < c.impulse_bars + 5:
                return None

            history = candles[:-1]
            if len(history) < c.impulse_bars + 1:
                return None

            current_candle = candles[-1]
            price = float(current_price or current_candle.close)

            bullish_ob = None
            bearish_ob = None

            start_idx = max(1, len(history) - c.max_age_bars)
            for i in range(len(history) - c.impulse_bars, start_idx - 1, -1):
                impulse_candles = history[i : i + c.impulse_bars]
                ob_candle = history[i - 1]

                is_bull_impulse = all(float(x.close) > float(x.open) for x in impulse_candles)
                if is_bull_impulse:
                    imp_start = float(impulse_candles[0].open)
                    imp_end = float(impulse_candles[-1].close)
                    imp_pct = (imp_end - imp_start) / imp_start
                    if imp_pct >= c.impulse_min_pct and float(ob_candle.close) <= float(ob_candle.open):
                        ob_body_high = max(float(ob_candle.open), float(ob_candle.close))
                        ob_body_low = min(float(ob_candle.open), float(ob_candle.close))
                        ob_low = float(ob_candle.low)
                        bullish_ob = (ob_body_low, ob_body_high, ob_low, imp_pct)
                        break

                is_bear_impulse = all(float(x.close) < float(x.open) for x in impulse_candles)
                if is_bear_impulse:
                    imp_start = float(impulse_candles[0].open)
                    imp_end = float(impulse_candles[-1].close)
                    imp_pct = (imp_start - imp_end) / imp_start
                    if imp_pct >= c.impulse_min_pct and float(ob_candle.close) >= float(ob_candle.open):
                        ob_body_high = max(float(ob_candle.open), float(ob_candle.close))
                        ob_body_low = min(float(ob_candle.open), float(ob_candle.close))
                        ob_high = float(ob_candle.high)
                        bearish_ob = (ob_body_low, ob_body_high, ob_high, imp_pct)
                        break

            direction = None
            stop_price = 0.0
            target_price = 0.0
            impulse_pct = 0.0

            if bullish_ob:
                ob_body_low, ob_body_high, ob_low, imp_p = bullish_ob
                if float(current_candle.low) <= ob_body_high and float(current_candle.high) >= ob_body_low:
                    direction = models.TradeDirection.LONG
                    stop_price = ob_low * (1.0 - c.stop_buffer_pct)
                    target_price = price + (price - stop_price) * c.min_rr
                    impulse_pct = imp_p

            elif bearish_ob:
                ob_body_low, ob_body_high, ob_high, imp_p = bearish_ob
                if float(current_candle.high) >= ob_body_low and float(current_candle.low) <= ob_body_high:
                    direction = models.TradeDirection.SHORT
                    stop_price = ob_high * (1.0 + c.stop_buffer_pct)
                    target_price = price - (stop_price - price) * c.min_rr
                    impulse_pct = imp_p

            if direction is None:
                return None

            risk = abs(price - stop_price)
            reward = abs(target_price - price)
            if risk <= 0 or (reward / risk) < c.min_rr:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 10.0 * (impulse_pct - c.impulse_min_pct)))

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
