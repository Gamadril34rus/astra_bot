"""
Volume Delta Divergence Strategy.

buy_vol = volume * (close - low) / (high - low), дельта за delta_window=10 баров.
Дивергенция цены и кумулятивной дельты -> контртренд.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class VolumeDeltaConfig(StrategyConfig):
    name: str = "volume_delta"
    enabled: bool = True
    delta_window: int = 10
    divergence_threshold: float = 0.3
    stop_pct: float = 0.01
    min_rr: float = 1.5


class VolumeDeltaStrategy(BaseStrategy[VolumeDeltaConfig]):
    """Стратегия кумулятивной дельты объёма и дивергенции с ценой."""

    def __init__(self, config: VolumeDeltaConfig | None = None):
        super().__init__(config or VolumeDeltaConfig())
        self.config: VolumeDeltaConfig

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
            if not candles or len(candles) < c.delta_window * 2:
                return None

            history = candles[:-1]
            if len(history) < c.delta_window * 2 - 1:
                return None

            def _calc_delta(candle: models.Candle) -> float:
                h = float(candle.high)
                l = float(candle.low)
                c_val = float(candle.close)
                v = float(candle.volume)
                if h <= l:
                    return 0.0
                buy_vol = v * (c_val - l) / (h - l)
                sell_vol = v - buy_vol
                return buy_vol - sell_vol

            sub_curr = [*history[-c.delta_window + 1:], candles[-1]]
            sub_prev = history[-c.delta_window * 2 + 1 : -c.delta_window + 1]

            delta_curr = sum(_calc_delta(x) for x in sub_curr)
            delta_prev = sum(_calc_delta(x) for x in sub_prev)

            price_curr = float(sub_curr[-1].close)
            price_prev = float(sub_prev[-1].close)

            price_change_pct = (price_curr - price_prev) / price_prev
            total_vol = sum(float(x.volume) for x in sub_curr)
            if total_vol <= 0:
                return None
            norm_delta_change = (delta_curr - delta_prev) / total_vol

            direction = None
            stop_price = 0.0
            target_price = 0.0

            if price_change_pct > 0.005 and norm_delta_change < -c.divergence_threshold:
                direction = models.TradeDirection.SHORT
                stop_price = price_curr * (1.0 + c.stop_pct)
                target_price = price_curr * (1.0 - c.stop_pct * c.min_rr)

            elif price_change_pct < -0.005 and norm_delta_change > c.divergence_threshold:
                direction = models.TradeDirection.LONG
                stop_price = price_curr * (1.0 - c.stop_pct)
                target_price = price_curr * (1.0 + c.stop_pct * c.min_rr)

            if direction is None:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 0.5 * abs(norm_delta_change)))

            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MEAN_REVERSION,
                direction=direction,
                entry_price=Decimal(str(price_curr)),
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
        return entry_price * Decimal("0.99")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.015"), "fraction": 1.0}]
