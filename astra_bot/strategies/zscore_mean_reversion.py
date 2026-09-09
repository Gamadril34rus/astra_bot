"""
Z-Score Mean Reversion Strategy.

z = (close - mean) / std, окно window=50.
Вход против отклонения при |z| > 2.5 (z > 2.5 -> SHORT, z < -2.5 -> LONG).
Выход / фильтр: только при ADX < 25.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from ..core import models
from ..engines.regime_detector import MarketRegimeDetector
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class ZScoreMeanReversionConfig(StrategyConfig):
    name: str = "zscore_mean_reversion"
    enabled: bool = True
    window: int = 50
    entry_z: float = 2.5
    exit_z: float = 0.3
    adx_threshold: float = 25.0
    stop_pct: float = 0.01
    min_rr: float = 1.5


class ZScoreMeanReversionStrategy(BaseStrategy[ZScoreMeanReversionConfig]):
    """Стратегия возврата к среднему по Z-score."""

    def __init__(self, config: ZScoreMeanReversionConfig | None = None):
        super().__init__(config or ZScoreMeanReversionConfig())
        self.config: ZScoreMeanReversionConfig

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
            if not candles or len(candles) < c.window:
                return None

            history = candles[:-1]
            if len(history) < c.window:
                return None

            highs = [float(x.high) for x in history]
            lows = [float(x.low) for x in history]
            closes = [float(x.close) for x in history]

            adx_val = MarketRegimeDetector._calculate_adx(highs, lows, closes, period=14)
            if adx_val >= c.adx_threshold:
                return None

            curr_candle = candles[-1]
            price = float(current_price or curr_candle.close)

            window_closes = [*closes[-c.window:], price]
            mean = float(np.mean(window_closes))
            std = float(np.std(window_closes))
            if std <= 0:
                return None

            z_score = (price - mean) / std

            direction = None
            stop_price = 0.0
            target_price = mean

            if z_score < -c.entry_z:
                direction = models.TradeDirection.LONG
                stop_price = price * (1.0 - c.stop_pct)
            elif z_score > c.entry_z:
                direction = models.TradeDirection.SHORT
                stop_price = price * (1.0 + c.stop_pct)

            if direction is None:
                return None

            risk = abs(price - stop_price)
            reward = abs(target_price - price)
            if risk <= 0 or round(reward / risk, 4) < c.min_rr:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 0.1 * (abs(z_score) - c.entry_z)))

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
        return entry_price * Decimal("0.99")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.015"), "fraction": 1.0}]
