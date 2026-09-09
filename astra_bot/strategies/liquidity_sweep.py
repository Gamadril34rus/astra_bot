"""
Liquidity Sweep Strategy.

Ложный пробой свинг-хая или свинг-лоу (тень выйдет за уровень, но закрытие внутри)
с подтверждением объёма > 1.5x SMA20. Вход против пробоя (контртренд/разворот).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import simple_moving_average
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class LiquiditySweepConfig(StrategyConfig):
    name: str = "liquidity_sweep"
    enabled: bool = True
    swing_lookback: int = 20
    sweep_tolerance: float = 0.003
    volume_multiplier: float = 1.5
    stop_buffer_pct: float = 0.002
    min_rr: float = 1.5


class LiquiditySweepStrategy(BaseStrategy[LiquiditySweepConfig]):
    """Стратегия ликвиднити свипа (снятие ликвидности / ложный пробой)."""

    def __init__(self, config: LiquiditySweepConfig | None = None):
        super().__init__(config or LiquiditySweepConfig())
        self.config: LiquiditySweepConfig

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
            if not candles or len(candles) < c.swing_lookback + 5:
                return None

            history = candles[:-1]
            if len(history) < c.swing_lookback:
                return None

            sweep_candle = candles[-1]
            c_high = float(sweep_candle.high)
            c_low = float(sweep_candle.low)
            c_close = float(sweep_candle.close)
            c_vol = float(sweep_candle.volume)

            vols = [float(x.volume) for x in history[-20:]]
            avg_vol = simple_moving_average(vols, len(vols))
            if not avg_vol or avg_vol <= 0:
                return None

            if c_vol < avg_vol * c.volume_multiplier:
                return None

            swing_high = max(float(x.high) for x in history[-c.swing_lookback:])
            swing_low = min(float(x.low) for x in history[-c.swing_lookback:])

            price = float(current_price or c_close)

            direction = None
            stop_price = 0.0
            target_price = 0.0

            if c_low < swing_low and c_close >= swing_low:
                direction = models.TradeDirection.LONG
                stop_price = c_low * (1.0 - c.stop_buffer_pct)
                target_price = swing_high
            elif c_high > swing_high and c_close <= swing_high:
                direction = models.TradeDirection.SHORT
                stop_price = c_high * (1.0 + c.stop_buffer_pct)
                target_price = swing_low

            if direction is None:
                return None

            risk = abs(price - stop_price)
            reward = abs(target_price - price)
            if risk <= 0 or round(reward / risk, 4) < c.min_rr:
                return None

            vol_ratio = c_vol / avg_vol
            confidence = min(0.85, max(0.5, 0.5 + 0.1 * (vol_ratio - c.volume_multiplier)))

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
            )
        except Exception as exc:
            logger.warning("%s evaluate error: %s", self.name, exc)
            return None

    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        return entry_price * Decimal("0.995")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.01"), "fraction": 1.0}]
