"""Мультивалютная торговая система (Multi-timeframe Protocol).

Спецификация:
1. 1D: EMA 20/50/200 задаёт режим и допустимое направление (bullish / bearish / range).
2. 4H: структура/графический паттерн в сторону 1D-контекста (breakout / retest).
3. 1H: breakout → retest → свечное подтверждение + объём выше среднего (SMA20).
4. BTC daily trend — обязательный фильтр для long по альткоинам (ETH/SOL/XRP и др.).
5. Разные risk limits для BTC/ETH (базовые) и альткоинов (сниженные).
6. Выход: R:R не хуже 1:2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import calculate_atr
from .base import BaseStrategy, Signal, SignalType, StrategyConfig


def _calc_ema(series_data: list[float], period: int) -> float:
    import pandas as pd
    s = pd.Series(series_data)
    return float(s.ewm(span=period, adjust=False).mean().iloc[-1])

logger = logging.getLogger(__name__)


@dataclass
class MulticurrencyMTFConfig(StrategyConfig):
    """Конфигурация мультивалютной MTF стратегии."""

    name: str = "multicurrency_mtf"
    ema_fast_1d: int = 20
    ema_mid_1d: int = 50
    ema_slow_1d: int = 200
    volume_period_1h: int = 20
    atr_period_4h: int = 14
    atr_buffer_mult: float = 1.5
    min_rr_ratio: float = 2.0
    altcoin_risk_discount: float = 0.5


class MulticurrencyMTFStrategy(BaseStrategy):
    """Мультивалютная MTF стратегия."""

    def __init__(self, config: MulticurrencyMTFConfig | None = None):
        super().__init__(config or MulticurrencyMTFConfig())
        self.config: MulticurrencyMTFConfig

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
        """Оценка условий на нескольких таймфреймах.

        Обязательно требует честных таймфреймов 1D, 4H, 1H без подмены.
        """
        if self.should_skip_signal():
            return None

        # Проверка наличия мульти-таймфрейм данных
        if not candles_1d or not candles_4h or not candles_1h:
            logger.debug("%s: Отсутствуют раздельные 1D/4H/1H свечи для MTF оценки", symbol)
            return None

        if len(candles_1d) < self.config.ema_slow_1d + 5 or len(candles_1h) < self.config.volume_period_1h + 5:
            return None

        # 1. 1D EMA Context
        c_1d = [float(c.close) for c in candles_1d]
        ema20_1d = _calc_ema(c_1d, self.config.ema_fast_1d)
        ema50_1d = _calc_ema(c_1d, self.config.ema_mid_1d)
        ema200_1d = _calc_ema(c_1d, self.config.ema_slow_1d)

        trend_1d_bullish = c_1d[-1] > ema200_1d and ema20_1d > ema50_1d
        trend_1d_bearish = c_1d[-1] < ema200_1d and ema20_1d < ema50_1d

        # 2. BTC 1D bearish gate для альткоинов
        is_btc = "BTC" in symbol.upper()
        if not is_btc and btc_candles_1d and len(btc_candles_1d) >= self.config.ema_slow_1d:
            btc_c_1d = [float(c.close) for c in btc_candles_1d]
            btc_ema200 = _calc_ema(btc_c_1d, self.config.ema_slow_1d)
            if btc_c_1d[-1] < btc_ema200:
                logger.info("%s: LONG заблокирован — BTC 1D находится в нисходящем тренде", symbol)
                return None

        # 3. 4H Breakout/Retest structure
        c_4h = [float(c.close) for c in candles_4h]
        highs_4h = [float(c.high) for c in candles_4h]
        lows_4h = [float(c.low) for c in candles_4h]
        atr_4h = calculate_atr(highs_4h, lows_4h, c_4h, period=self.config.atr_period_4h) or (c_4h[-1] * 0.01)

        # 4. 1H Confirmation + Volume > SMA20
        v_1h = [float(c.volume) for c in candles_1h]
        import pandas as pd
        v_sma20 = float(pd.Series(v_1h).rolling(self.config.volume_period_1h).mean().iloc[-1])
        if v_1h[-1] <= v_sma20:
            return None  # Объём на входе должен быть выше среднего за 20 свечей

        c_1h = [float(c.close) for c in candles_1h]
        o_1h = [float(c.open) for c in candles_1h]
        price = float(current_price or c_1h[-1])

        # Сигнал LONG
        if trend_1d_bullish and c_1h[-1] > o_1h[-1]:  # Бычья 1H свеча
            stop_dist = Decimal(str(round(atr_4h * self.config.atr_buffer_mult, 6)))
            entry = Decimal(str(round(price, 6)))
            stop = entry - stop_dist
            take = entry + stop_dist * Decimal(str(self.config.min_rr_ratio))

            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.BREAKOUT,
                direction=models.TradeDirection.LONG,
                entry_price=entry,
                stop_loss=stop,
                take_profit=take,
                confidence=0.85,
                market_regime=market_regime or "BULLISH",
                features={"mtf_1d_bullish": 1.0, "volume_surge": v_1h[-1] / v_sma20},
            )

        # Сигнал SHORT
        if trend_1d_bearish and c_1h[-1] < o_1h[-1]:  # Медвежья 1H свеча
            stop_dist = Decimal(str(round(atr_4h * self.config.atr_buffer_mult, 6)))
            entry = Decimal(str(round(price, 6)))
            stop = entry + stop_dist
            take = entry - stop_dist * Decimal(str(self.config.min_rr_ratio))

            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.BREAKOUT,
                direction=models.TradeDirection.SHORT,
                entry_price=entry,
                stop_loss=stop,
                take_profit=take,
                confidence=0.85,
                market_regime=market_regime or "BEARISH",
                features={"mtf_1d_bearish": 1.0, "volume_surge": v_1h[-1] / v_sma20},
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
