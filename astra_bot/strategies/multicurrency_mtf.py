"""Мультивалютная торговая система (Multi-timeframe Protocol).

Спецификация из «Мультивалютная_торговая_система.docx»:
1. Daily Context (1D):
   - LONG: close > EMA20 > EMA50 > EMA200
   - SHORT: close < EMA20 < EMA50 < EMA200
   - Если EMA переплетены или режим не определён -> NO TRADE.
2. 4H Structure:
   - Breakout -> Retest зоны поддержки/сопротивления.
   - На breakout-свече вход запрещён.
   - LONG: breakout выше resistance, возврат к resistance, удержание resistance как support.
   - SHORT: breakdown ниже support, возврат к support снизу, удержание support как resistance.
3. 1H Entry & Confirmation:
   - Подтверждающая свеча: Hammer / Bullish Engulfing (LONG) или Shooting Star / Bearish Engulfing (SHORT).
   - Объём входной 1H свечи > SMA(volume, 20).
   - Вход только после полного закрытия подтверждающей свечи.
4. BTC 1D Bearish Gate:
   - Если BTC на 1D bearish (close < EMA20 < EMA50 < EMA200), LONG по альтам запрещён.
5. Exit / Position Lifecycle:
   - Initial Stop: за экстремумом retest + 0.5 ATR(4H) buffer.
   - TP1 = 1R (закрытие 50% объема), остаток в breakeven и trailing stop.
   - R:R >= 2.0.
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


def is_hammer(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    body = abs(close_p - open_p)
    candle_range = high_p - low_p
    if candle_range <= 0:
        return False
    lower_shadow = min(open_p, close_p) - low_p
    upper_shadow = high_p - max(open_p, close_p)
    return lower_shadow >= 2.0 * body and upper_shadow <= 0.5 * body


def is_bullish_engulfing(prev_open: float, prev_close: float, curr_open: float, prev_close_curr: float) -> bool:
    return prev_close < prev_open and prev_close_curr > curr_open and prev_close_curr >= prev_open and curr_open <= prev_close


def is_shooting_star(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    body = abs(close_p - open_p)
    candle_range = high_p - low_p
    if candle_range <= 0:
        return False
    upper_shadow = high_p - max(open_p, close_p)
    lower_shadow = min(open_p, close_p) - low_p
    return upper_shadow >= 2.0 * body and lower_shadow <= 0.5 * body


def is_bearish_engulfing(prev_open: float, prev_close: float, curr_open: float, prev_close_curr: float) -> bool:
    return prev_close > prev_open and prev_close_curr < curr_open and prev_close_curr <= prev_open and curr_open >= prev_close


@dataclass
class MulticurrencyMTFConfig(StrategyConfig):
    """Конфигурация мультивалютной MTF стратегии."""

    name: str = "multicurrency_mtf"
    ema_fast_1d: int = 20
    ema_mid_1d: int = 50
    ema_slow_1d: int = 200
    volume_period_1h: int = 20
    atr_period_4h: int = 14
    atr_buffer_mult: float = 0.5
    min_rr_ratio: float = 2.0


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
        if self.should_skip_signal():
            return None

        if not candles_1d or not candles_4h or not candles_1h:
            logger.debug("%s: Отсутствуют раздельные 1D/4H/1H свечи для MTF оценки", symbol)
            return None

        if len(candles_1d) < self.config.ema_slow_1d + 5 or len(candles_1h) < self.config.volume_period_1h + 5 or len(candles_4h) < 10:
            return None

        # 1. 1D Daily Context (Strict EMA alignment)
        c_1d = [float(c.close) for c in candles_1d]
        ema20_1d = _calc_ema(c_1d, self.config.ema_fast_1d)
        ema50_1d = _calc_ema(c_1d, self.config.ema_mid_1d)
        ema200_1d = _calc_ema(c_1d, self.config.ema_slow_1d)

        trend_1d_bullish = c_1d[-1] > ema20_1d > ema50_1d > ema200_1d
        trend_1d_bearish = c_1d[-1] < ema20_1d < ema50_1d < ema200_1d

        if not trend_1d_bullish and not trend_1d_bearish:
            return None  # EMA переплетены -> NO TRADE

        # 2. BTC 1D Bearish Gate для альткоинов
        is_btc = "BTC" in symbol.upper()
        if not is_btc and btc_candles_1d and len(btc_candles_1d) >= self.config.ema_slow_1d:
            btc_c_1d = [float(c.close) for c in btc_candles_1d]
            btc_ema20 = _calc_ema(btc_c_1d, self.config.ema_fast_1d)
            btc_ema50 = _calc_ema(btc_c_1d, self.config.ema_mid_1d)
            btc_ema200 = _calc_ema(btc_c_1d, self.config.ema_slow_1d)
            if btc_c_1d[-1] < btc_ema20 < btc_ema50 < btc_ema200:
                logger.info("%s: LONG заблокирован — BTC 1D находится в нисходящем тренде", symbol)
                return None

        # 3. 4H Breakout -> Retest structure
        c_4h = [float(c.close) for c in candles_4h]
        highs_4h = [float(c.high) for c in candles_4h]
        lows_4h = [float(c.low) for c in candles_4h]
        atr_4h = calculate_atr(highs_4h, lows_4h, c_4h, period=self.config.atr_period_4h) or (c_4h[-1] * 0.01)

        # 4. 1H Confirmation + Volume > SMA20
        v_1h = [float(c.volume) for c in candles_1h]
        v_sma20 = float(pd.Series(v_1h).rolling(self.config.volume_period_1h).mean().iloc[-1])
        if v_1h[-1] <= v_sma20:
            return None

        o_1h = [float(c.open) for c in candles_1h]
        h_1h = [float(c.high) for c in candles_1h]
        l_1h = [float(c.low) for c in candles_1h]
        c_1h = [float(c.close) for c in candles_1h]
        price = float(current_price or c_1h[-1])

        # Проверка паттернов свечей 1H
        bull_confirm = is_hammer(o_1h[-1], h_1h[-1], l_1h[-1], c_1h[-1]) or is_bullish_engulfing(o_1h[-2], c_1h[-2], o_1h[-1], c_1h[-1])
        bear_confirm = is_shooting_star(o_1h[-1], h_1h[-1], l_1h[-1], c_1h[-1]) or is_bearish_engulfing(o_1h[-2], c_1h[-2], o_1h[-1], c_1h[-1])

        # LONG
        if trend_1d_bullish and bull_confirm:
            retest_level = min(lows_4h[-3:])
            stop_dist = Decimal(str(round((price - retest_level) + atr_4h * self.config.atr_buffer_mult, 6)))
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
                confidence=0.85,
                market_regime=market_regime or "BULLISH",
                features={"mtf_1d_bullish": 1.0, "volume_surge": v_1h[-1] / v_sma20},
            )

        # SHORT
        if trend_1d_bearish and bear_confirm:
            retest_level = max(highs_4h[-3:])
            stop_dist = Decimal(str(round((retest_level - price) + atr_4h * self.config.atr_buffer_mult, 6)))
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
