"""
VWAP Deviation Strategy.

Сессионный VWAP (сброс в 00:00 UTC) ±2σ: касание с разворотной свечой (поглощение/пин-бар) -> вход, выход у VWAP.
ТФ 15m–1h.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class VWAPDeviationConfig(StrategyConfig):
    name: str = "vwap_deviation"
    enabled: bool = True
    sigma_entry: float = 2.0
    sigma_exit: float = 0.5
    stop_buffer_pct: float = 0.002
    min_rr: float = 1.2


class VWAPDeviationStrategy(BaseStrategy[VWAPDeviationConfig]):
    """Стратегия отклонения от сессионного VWAP."""

    def __init__(self, config: VWAPDeviationConfig | None = None):
        super().__init__(config or VWAPDeviationConfig())
        self.config: VWAPDeviationConfig

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
            if not candles or len(candles) < 5:
                return None

            curr_time = datetime.fromtimestamp(candles[-1].open_time / 1000.0, tz=UTC)
            session_candles = [
                x for x in candles
                if datetime.fromtimestamp(x.open_time / 1000.0, tz=UTC).date() == curr_time.date()
            ]

            if len(session_candles) < 3:
                session_candles = candles[-20:]

            cum_pv = 0.0
            cum_vol = 0.0
            typical_prices = []
            volumes = []

            for x in session_candles:
                tp = (float(x.high) + float(x.low) + float(x.close)) / 3.0
                vol = float(x.volume)
                cum_pv += tp * vol
                cum_vol += vol
                typical_prices.append(tp)
                volumes.append(vol)

            if cum_vol <= 0:
                return None

            vwap = cum_pv / cum_vol

            variance = sum(v * ((tp - vwap) ** 2) for tp, v in zip(typical_prices, volumes, strict=False)) / cum_vol
            std_dev = math.sqrt(variance) if variance > 0 else 0.0

            if std_dev <= 0:
                return None

            upper_band = vwap + c.sigma_entry * std_dev
            lower_band = vwap - c.sigma_entry * std_dev

            curr_candle = candles[-1]
            price = float(current_price or curr_candle.close)
            curr_open = float(curr_candle.open)
            curr_high = float(curr_candle.high)
            curr_low = float(curr_candle.low)
            curr_close = float(curr_candle.close)

            is_pinbar_bull = (min(curr_open, curr_close) - curr_low) > 2.0 * abs(curr_close - curr_open)
            is_pinbar_bear = (curr_high - max(curr_open, curr_close)) > 2.0 * abs(curr_close - curr_open)

            prev_candle = candles[-2] if len(candles) >= 2 else None
            is_engulfing_bull = False
            is_engulfing_bear = False
            if prev_candle:
                p_open = float(prev_candle.open)
                p_close = float(prev_candle.close)
                if p_close < p_open and curr_close > curr_open and curr_close > p_open and curr_open < p_close:
                    is_engulfing_bull = True
                elif p_close > p_open and curr_close < curr_open and curr_close < p_open and curr_open > p_close:
                    is_engulfing_bear = True

            direction = None
            stop_price = 0.0
            target_price = vwap

            if curr_low <= lower_band and (is_pinbar_bull or is_engulfing_bull or curr_close > curr_open):
                direction = models.TradeDirection.LONG
                stop_price = curr_low * (1.0 - c.stop_buffer_pct)

            elif curr_high >= upper_band and (is_pinbar_bear or is_engulfing_bear or curr_close < curr_open):
                direction = models.TradeDirection.SHORT
                stop_price = curr_high * (1.0 + c.stop_buffer_pct)

            if direction is None:
                return None

            risk = abs(price - stop_price)
            reward = abs(target_price - price)
            if risk <= 0 or (reward / risk) < c.min_rr:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 0.1 * abs(price - vwap) / std_dev))

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
        except Exception:
            return None

    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        return entry_price * Decimal("0.995")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.01"), "fraction": 1.0}]
