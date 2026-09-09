"""
Time Series Momentum Cross Strategy.

EMA21 cross EMA55 + подтверждение ROC14 > 0 для LONG (ROC14 < 0 для SHORT) + ADX > 20 filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import exponential_moving_average
from ..engines.regime_detector import MarketRegimeDetector
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class TSMomentumCrossConfig(StrategyConfig):
    name: str = "ts_momentum_cross"
    enabled: bool = True
    fast: int = 21
    slow: int = 55
    roc_period: int = 14
    adx_min: float = 20.0
    stop_pct: float = 0.015
    rr: float = 1.5


class TSMomentumCrossStrategy(BaseStrategy[TSMomentumCrossConfig]):
    """Стратегия пересечения трендовых ЕМА с подтверждением импульса и ADX."""

    def __init__(self, config: TSMomentumCrossConfig | None = None):
        super().__init__(config or TSMomentumCrossConfig())
        self.config: TSMomentumCrossConfig

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
            if not candles or len(candles) < c.slow:
                return None

            history = candles[:-1]
            if len(history) < c.slow:
                return None

            highs = [float(x.high) for x in history]
            lows = [float(x.low) for x in history]
            closes = [float(x.close) for x in history]

            adx_val = MarketRegimeDetector._calculate_adx(highs, lows, closes, period=14)
            if adx_val < c.adx_min:
                return None

            e_fast_prev = exponential_moving_average(closes[-c.fast - 1 : -1], c.fast)
            e_slow_prev = exponential_moving_average(closes[-c.slow - 1 : -1], c.slow)

            curr_candle = candles[-1]
            price = float(current_price or curr_candle.close)
            closes_curr = [*closes, price]

            e_fast_curr = exponential_moving_average(closes_curr[-c.fast:], c.fast)
            e_slow_curr = exponential_moving_average(closes_curr[-c.slow:], c.slow)

            if not (e_fast_prev and e_slow_prev and e_fast_curr and e_slow_curr):
                return None

            roc14 = (price - closes[-c.roc_period]) / closes[-c.roc_period]

            direction = None
            stop_price = 0.0
            target_price = 0.0

            if e_fast_prev <= e_slow_prev and e_fast_curr > e_slow_curr and roc14 > 0:
                direction = models.TradeDirection.LONG
                stop_price = price * (1.0 - c.stop_pct)
                target_price = price * (1.0 + c.stop_pct * c.rr)
            elif e_fast_prev >= e_slow_prev and e_fast_curr < e_slow_curr and roc14 < 0:
                direction = models.TradeDirection.SHORT
                stop_price = price * (1.0 + c.stop_pct)
                target_price = price * (1.0 - c.stop_pct * c.rr)

            if direction is None:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 0.01 * (adx_val - c.adx_min)))

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
        except Exception as exc:
            logger.warning("%s evaluate error: %s", self.name, exc)
            return None

    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        return entry_price * Decimal("0.985")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.0225"), "fraction": 1.0}]
