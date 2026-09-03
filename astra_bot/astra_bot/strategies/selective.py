"""Selective mean-reversion / trend-pullback strategy.

Отбирает только сетапы с совпадением нескольких факторов, чтобы
поднять win-rate в self-play. Использует:

* EMA20/50/200 alignment;
* RSI экстремум против позиций (покупка при RSI < 35 в бычьем тренде);
* ATR как нормализатор стопа;
* Bollinger z-score для входа;
* объёмное подтверждение;
* R:R не меньше 2.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import (
    calculate_atr,
    calculate_rsi,
    exponential_moving_average,
)
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


@dataclass
class SelectiveConfig(StrategyConfig):
    name: str = "selective"
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    bb_period: int = 20
    atr_period: int = 14
    min_volume_ratio: float = 1.1
    rr: float = 2.0
    holding_bars: int = 24


class SelectiveStrategy(BaseStrategy):
    def __init__(self, config: SelectiveConfig | None = None):
        super().__init__(config or SelectiveConfig())
        self.config: SelectiveConfig

    async def evaluate(self, symbol, candles, orderbook=None, current_price=None, market_regime=None):
        c = self.config
        need = max(c.ema_slow, c.bb_period, c.atr_period) + 2
        if len(candles) < need:
            return None
        closes = [float(x.close) for x in candles]
        highs = [float(x.high) for x in candles]
        lows = [float(x.low) for x in candles]
        vols = [float(x.volume) for x in candles]
        price = float(current_price or closes[-1])

        e20 = exponential_moving_average(closes[-c.ema_fast:], c.ema_fast)
        e50 = exponential_moving_average(closes[-c.ema_mid:], c.ema_mid)
        e200 = exponential_moving_average(closes[-c.ema_slow:], c.ema_slow)
        if not (e20 and e50 and e200):
            return None
        rsi = calculate_rsi(closes, period=c.rsi_period)
        atr = calculate_atr(highs[-c.atr_period:], lows[-c.atr_period:],
                            closes[-c.atr_period:], period=c.atr_period)
        if not rsi or not atr:
            return None
        avg_vol = sum(vols[-20:]) / 20
        if avg_vol <= 0:
            return None
        volume_ratio = vols[-1] / avg_vol
        if volume_ratio < c.min_volume_ratio:
            return None

        w = closes[-c.bb_period:]
        mean = sum(w) / len(w)
        var = sum((v - mean) ** 2 for v in w) / len(w)
        std = var ** 0.5 if var > 0 else 0
        z = (closes[-1] - mean) / std if std else 0.0

        # LONG: бычий тренд + перепроданность + отрицательный z-score.
        long_align = e20 > e50 > e200 and price > e200
        short_align = e20 < e50 < e200 and price < e200

        direction = None
        if long_align and rsi < c.rsi_oversold and z < -0.8:
            direction = models.TradeDirection.LONG
        elif short_align and rsi > c.rsi_overbought and z > 0.8:
            direction = models.TradeDirection.SHORT
        if direction is None:
            return None

        entry = Decimal(str(price))
        stop_dist = Decimal(str(max(atr, price * 0.004)))
        if direction == models.TradeDirection.LONG:
            stop = entry - stop_dist
            take = entry + stop_dist * Decimal(str(c.rr))
        else:
            stop = entry + stop_dist
            take = entry - stop_dist * Decimal(str(c.rr))

        # confidence от экстремальности RSI/z-score.
        confidence = min(0.95, 0.55
                         + (c.rsi_oversold - rsi if direction == models.TradeDirection.LONG
                            else rsi - c.rsi_overbought) * 0.01
                         + min(0.2, abs(z) * 0.1))

        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MEAN_REVERSION,
            direction=direction,
            entry_price=entry,
            stop_loss=stop,
            take_profit=take,
            position_size=Decimal("0"),
            risk_amount=Decimal("0"),
            confidence=confidence,
            market_regime=market_regime or "UNKNOWN",
            features={"rsi": rsi, "z": z, "atr_pct": atr / price * 100,
                      "volume_ratio": volume_ratio},
        )

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        return entry_price - Decimal(str(atr or 0.0 or entry_price * 0.004))

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        risk = abs(entry_price - stop_loss)
        return [{"price": entry_price + risk * Decimal("2.0"), "r_multiple": 2.0}]
