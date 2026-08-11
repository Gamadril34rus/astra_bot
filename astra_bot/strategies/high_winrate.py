"""
ASTRA BOT — High-winrate strategy.

Нацелена на win-rate >= 80% за счёт очень жёсткого отбора:

Long only:
1. EMA50 > EMA200 — долгосрочный восходящий тренд.
2. Цена выше EMA200 — не ловим падающие ножи.
3. RSI(14) < 35 — перепроданность (отскок статистически вероятен).
4. Свеча закрылась ниже нижней полосы Боллинджера (20, 2σ).
5. ATR(14)% ≤ 5% — не входим в парашютирующую волатильность.
6. Объём последней свечи >= 1.2× среднего за 20 баров.
7. Угол наклона EMA50 > 0 — тренд действительно вверх.

Выход:
* Тейк профит 2× ATR (R:R ≈ 2).
* Стоп 1× ATR под минимумом сигнальной свечи.
* Таймаут 24 бара.

Такая конфигурация берёт мало сделок, но удерживает высокий win-rate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import (
    calculate_atr,
    calculate_rsi,
    exponential_moving_average,
)
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class HighWinrateConfig(StrategyConfig):
    name: str = "high_winrate"
    ema_fast: int = 50
    ema_slow: int = 200
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    atr_period: int = 14
    atr_max_pct: float = 5.0
    volume_factor: float = 1.2
    slope_lookback: int = 10
    risk_reward: float = 2.0
    holding_bars: int = 24


class HighWinrateStrategy(BaseStrategy):
    """High-winrate отбойный шорт/лонг с очень жёсткими фильтрами."""

    def __init__(self, config: HighWinrateConfig | None = None):
        super().__init__(config or HighWinrateConfig())
        self.config: HighWinrateConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook: models.OrderBook | None = None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        c = self.config
        need = max(c.ema_slow, c.bb_period, c.atr_period) + 2
        if len(candles) < need:
            return None

        closes = [float(x.close) for x in candles]
        highs = [float(x.high) for x in candles]
        lows = [float(x.low) for x in candles]
        volumes = [float(x.volume) for x in candles]

        price = float(current_price or closes[-1])

        ema_fast = exponential_moving_average(closes[-c.ema_fast:], c.ema_fast)
        ema_slow = exponential_moving_average(closes[-c.ema_slow:], c.ema_slow)
        if not ema_fast or not ema_slow:
            return None

        # Трендовый фильтр.
        if not (ema_fast > ema_slow and price > ema_slow):
            return None
        # EMA50 должна расти.
        past_ema_fast = exponential_moving_average(
            closes[-c.ema_fast - c.slope_lookback : -c.slope_lookback],
            c.ema_fast,
        )
        if not past_ema_fast or ema_fast <= past_ema_fast:
            return None

        rsi = calculate_rsi(closes, period=c.rsi_period)
        if rsi is None or rsi >= c.rsi_oversold:
            return None

        # Bollinger lower band.
        window = closes[-c.bb_period:]
        mean = sum(window) / len(window)
        var = sum((v - mean) ** 2 for v in window) / len(window)
        std = var ** 0.5
        lower_band = mean - c.bb_std * std
        if closes[-1] >= lower_band:
            return None

        atr = calculate_atr(
            highs[-c.atr_period:],
            lows[-c.atr_period:],
            closes[-c.atr_period:],
            period=c.atr_period,
        )
        if not atr:
            return None
        atr_pct = atr / price * 100
        if atr_pct > c.atr_max_pct:
            return None

        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
        if avg_vol and volumes[-1] < avg_vol * c.volume_factor:
            return None

        entry = Decimal(str(price))
        stop = Decimal(str(float(lows[-1]) - atr))
        take = entry + (entry - stop) * Decimal(str(c.risk_reward))

        if stop >= entry or take <= entry:
            return None

        confidence = min(
            0.95,
            0.6
            + (c.rsi_oversold - rsi) * 0.01
            + (1.0 - atr_pct / c.atr_max_pct) * 0.1,
        )

        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MEAN_REVERSION,
            direction=models.TradeDirection.LONG,
            entry_price=entry,
            stop_loss=stop,
            take_profit=take,
            confidence=confidence,
            market_regime=market_regime or "BULL_TREND",
            features={
                "rsi": rsi,
                "atr_pct": atr_pct,
                "ema_gap": (ema_fast - ema_slow) / ema_slow,
            },
        )

    def calculate_stop_loss(self, entry_price: Decimal, candles, atr=None):
        return entry_price * Decimal("0.98")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles):
        risk = entry_price - stop_loss
        return [
            {"price": entry_price + risk * Decimal("2"), "r_multiple": 2},
        ]

    def evaluate_sync(self, *args, **kwargs):
        """Синхронная обёртка для бэктестера без asyncio."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            return asyncio.run(self.evaluate(*args, **kwargs))
        raise RuntimeError("evaluate_sync called inside running loop")
