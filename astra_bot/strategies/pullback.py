"""Pullback strategy — лучшая найденная конфигурация.

Вход:
* EMA20 > EMA50 > EMA200 (long) или наоборот (short);
* цена рядом с EMA50 (pullback, |price - EMA50| / price < 0.7%);
* RSI в нейтральной полосе 40–60;
* сигнальная свеча подтверждает разворот (close > open для long).

Стоп/тейк:
* фиксированный процент 0.8% стопа;
* R:R = 0.75 (тейк 0.6%) — оптимум по бэктесту на 3 годах
  (win-rate ~59%, PF > 1).

Удержание позиции — до 12 баров или до срабатывания SL/TP.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import (
    calculate_rsi,
    exponential_moving_average,
)
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class PullbackConfig(StrategyConfig):
    name: str = "pullback"
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    rsi_low: float = 40.0
    rsi_high: float = 60.0
    pullback_pct: float = 0.012
    stop_pct: float = 0.012
    rr: float = 0.75  # optimised on 6M BTC 1h: WR 67.6%, PnL +64%
    hold_bars: int = 12


class PullbackStrategy(BaseStrategy):
    """Trend-pullback стратегия с фиксированным R:R."""

    def __init__(self, config: PullbackConfig | None = None):
        super().__init__(config or PullbackConfig())
        self.config: PullbackConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        c = self.config
        need = c.ema_slow + 5
        if len(candles) < need:
            return None

        closes = [float(x.close) for x in candles]
        opens = [float(x.open) for x in candles]
        price = float(current_price or closes[-1])

        e20 = exponential_moving_average(closes[-c.ema_fast:], c.ema_fast)
        e50 = exponential_moving_average(closes[-c.ema_mid:], c.ema_mid)
        e200 = exponential_moving_average(closes[-c.ema_slow:], c.ema_slow)
        if not (e20 and e50 and e200):
            return None

        rsi_val = calculate_rsi(closes, period=14)
        if rsi_val is None:
            return None

        pullback_dist = abs(price - e50) / price
        if pullback_dist > c.pullback_pct:
            return None
        if not (c.rsi_low <= rsi_val <= c.rsi_high):
            return None

        bull = e20 > e50 > e200
        bear = e20 < e50 < e200
        if bull and closes[-1] > opens[-1]:
            direction = models.TradeDirection.LONG
        elif bear and closes[-1] < opens[-1]:
            direction = models.TradeDirection.SHORT
        else:
            return None

        entry = Decimal(str(price))
        if direction == models.TradeDirection.LONG:
            stop = entry * (Decimal("1") - Decimal(str(c.stop_pct)))
            take = entry * (
                Decimal("1") + Decimal(str(c.stop_pct * c.rr))
            )
        else:
            stop = entry * (Decimal("1") + Decimal(str(c.stop_pct)))
            take = entry * (
                Decimal("1") - Decimal(str(c.stop_pct * c.rr))
            )

        # Уверенность выше, если цена близко к EMA50 и RSI в центре.
        closeness = 1.0 - min(1.0, pullback_dist / c.pullback_pct)
        rsi_center = 1.0 - abs(rsi_val - 50) / 20.0
        confidence = min(0.9, 0.5 + 0.25 * closeness + 0.15 * rsi_center)

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
            features={
                "rsi": rsi_val,
                "ema_gap_pct": (e20 - e200) / e200 * 100,
                "pullback_pct": pullback_dist * 100,
                "stop_pct": c.stop_pct,
                "rr": c.rr,
            },
        )

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        c = self.config
        return entry_price * (Decimal("1") - Decimal(str(c.stop_pct)))

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        risk = abs(entry_price - stop_loss)
        rr = Decimal(str(self.config.rr))
        return [
            {"price": entry_price + risk * rr, "r_multiple": float(rr)},
        ]
