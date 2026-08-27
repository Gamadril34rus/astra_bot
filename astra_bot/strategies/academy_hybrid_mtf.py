"""Академическая гибридная MTF-стратегия (Academy Hybrid MTF Strategy).

Объединяет формализуемые правила из более чем 30 бесплатных курсов по трейдингу
(Price Action, Smart Money Concepts / Liquidity Sweeps, VSA / Volume, Trend Alignment):

1. **Market Context & Trend (1D / 4H)**:
   - LONG: 1D EMA 50 > EMA 200 (бычий макро-контекст).
   - SHORT: 1D EMA 50 < EMA 200 (медвежий макро-контекст).
2. **Liquidity Sweep & Structure (4H / 1H)**:
   - LONG: Снятие Sell-side ликвидности (Low за последние N баров был пробит свечной тенью, но цена закрылась выше уровня).
   - SHORT: Снятие Buy-side ликвидности (High за последние N баров был пробит свечной тенью, но цена закрылась ниже уровня).
3. **Volume Spread Confirmation (1H)**:
   - Объём свечи снятия ликвидности / подтверждения > SMA(volume, 20).
4. **Risk / Reward & Stop Loss**:
   - Initial Stop Loss: за образованной тенью (sweep wick) + 0.5 ATR.
   - Take Profit: 2.0 R:R (минимальный целевой профит) или фиксированный 2R.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from ..core import models
from ..core.utils import calculate_atr
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


def _calc_ema(series_data: list[float], period: int) -> float:
    s = pd.Series(series_data)
    return float(s.ewm(span=period, adjust=False).mean().iloc[-1])


@dataclass
class AcademyHybridMTFConfig(StrategyConfig):
    """Конфигурация гибридной академической стратегии."""

    name: str = "academy_hybrid_mtf"
    ema_fast_1d: int = 50
    ema_slow_1d: int = 200
    sweep_lookback_4h: int = 10
    volume_period_1h: int = 20
    atr_period_4h: int = 14
    atr_buffer_mult: float = 0.5
    min_rr_ratio: float = 2.0


class AcademyHybridMTFStrategy(BaseStrategy):
    """Гибридная MTF-стратегия по материалам академических курсов."""

    def __init__(self, config: AcademyHybridMTFConfig | None = None):
        super().__init__(config or AcademyHybridMTFConfig())
        self.config: AcademyHybridMTFConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
        btc_candles_1d: list[models.Candle] | None = None,
        candles_1d: list[models.Candle] | None = None,
        candles_4h: list[models.Candle] | None = None,
        candles_1h: list[models.Candle] | None = None,
    ) -> Signal | None:
        if self.should_skip_signal():
            return None

        if not candles_1d or not candles_4h or not candles_1h:
            return None

        if (
            len(candles_1d) < self.config.ema_slow_1d + 5
            or len(candles_1h) < self.config.volume_period_1h + 5
            or len(candles_4h) < self.config.sweep_lookback_4h + 5
        ):
            return None

        # 1. Macro Trend (1D EMA 50 / 200)
        c_1d = [float(c.close) for c in candles_1d]
        ema50_1d = _calc_ema(c_1d, self.config.ema_fast_1d)
        ema200_1d = _calc_ema(c_1d, self.config.ema_slow_1d)

        trend_bullish = ema50_1d > ema200_1d
        trend_bearish = ema50_1d < ema200_1d

        # 2. 4H Structure & Liquidity Levels
        highs_4h = [float(c.high) for c in candles_4h]
        lows_4h = [float(c.low) for c in candles_4h]
        closes_4h = [float(c.close) for c in candles_4h]

        recent_high = max(highs_4h[-(self.config.sweep_lookback_4h + 1):-1])
        recent_low = min(lows_4h[-(self.config.sweep_lookback_4h + 1):-1])

        atr_4h = calculate_atr(highs_4h, lows_4h, closes_4h, period=self.config.atr_period_4h) or (closes_4h[-1] * 0.01)

        # 3. 1H Confirmation & Volume Filter
        v_1h = [float(c.volume) for c in candles_1h]
        v_sma20 = float(pd.Series(v_1h).rolling(self.config.volume_period_1h).mean().iloc[-1])

        if v_1h[-1] <= v_sma20:
            return None  # Объём ниже среднего -> отмена

        c_1h = [float(c.close) for c in candles_1h]
        l_1h = [float(c.low) for c in candles_1h]
        h_1h = [float(c.high) for c in candles_1h]
        price = float(current_price or c_1h[-1])

        # Sell-side liquidity sweep (LONG)
        sell_side_sweep = l_1h[-1] < recent_low and c_1h[-1] > recent_low
        # Buy-side liquidity sweep (SHORT)
        buy_side_sweep = h_1h[-1] > recent_high and c_1h[-1] < recent_high

        if trend_bullish and sell_side_sweep:
            stop_dist = Decimal(str(round((price - l_1h[-1]) + atr_4h * self.config.atr_buffer_mult, 6)))
            if stop_dist <= 0:
                stop_dist = Decimal(str(round(atr_4h * 1.5, 6)))
            entry = Decimal(str(round(price, 6)))
            stop = entry - stop_dist
            take = entry + stop_dist * Decimal(str(self.config.min_rr_ratio))

            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MOMENTUM,
                direction=models.TradeDirection.LONG,
                entry_price=entry,
                stop_loss=stop,
                take_profit=take,
                confidence=0.88,
                market_regime=market_regime or "BULLISH",
                features={"sell_side_sweep": 1.0, "volume_ratio": v_1h[-1] / v_sma20},
            )

        if trend_bearish and buy_side_sweep:
            stop_dist = Decimal(str(round((h_1h[-1] - price) + atr_4h * self.config.atr_buffer_mult, 6)))
            if stop_dist <= 0:
                stop_dist = Decimal(str(round(atr_4h * 1.5, 6)))
            entry = Decimal(str(round(price, 6)))
            stop = entry + stop_dist
            take = entry - stop_dist * Decimal(str(self.config.min_rr_ratio))

            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MOMENTUM,
                direction=models.TradeDirection.SHORT,
                entry_price=entry,
                stop_loss=stop,
                take_profit=take,
                confidence=0.88,
                market_regime=market_regime or "BEARISH",
                features={"buy_side_sweep": 1.0, "volume_ratio": v_1h[-1] / v_sma20},
            )

        return None

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        atr_val = atr or calculate_atr(
            [float(c.high) for c in candles],
            [float(c.low) for c in candles],
            [float(c.close) for c in candles],
            period=self.config.atr_period_4h,
        ) or (float(entry_price) * 0.01)
        dist = Decimal(str(atr_val)) * Decimal(str(self.config.atr_buffer_mult))
        return Decimal(str(entry_price)) - dist

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        stop_dist = abs(Decimal(str(entry_price)) - Decimal(str(stop_loss)))
        take = Decimal(str(entry_price)) + stop_dist * Decimal(str(self.config.min_rr_ratio))
        return [take]
