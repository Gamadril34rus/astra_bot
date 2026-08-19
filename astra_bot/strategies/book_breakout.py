"""Стратегия из «Простой книги торговли» (раздел 3, стратегии 1–7).

Все семь сетапов книги сводятся к одному принципу:

1. Цена консолидируется у уровня (декольте / сопротивление / поддержка).
2. Пробой уровня. КНИГА ЗАПРЕЩАЕТ входить сразу на пробое — типичная
   FOMO-ошибка («Каждый раз, когда цена пробивает декольте — не надо
   делать немедленную покупку», стр. 29).
3. Ждём РЕТЕСТ пробитого уровня (цена возвращается к уровню).
4. Ждём подтверждающую свечу в сторону пробоя («после повторного
   тестирования нам нужна бычья свеча, после этого мы можем купить на
   следующей свече», стр. 29).
5. Стоп — за экстремумом ретеста / противоположной стороной паттерна
   (стр. 30–31), цель — высота консолидации от точки пробоя
   (measured move, как на схемах раздела 4).

Направления:
* LONG — пробой сопротивления консолидации + ретест + бычья свеча
  (стратегии 1, 2, 6, 7; паттерны double bottom, falling wedge, bull flag…).
* SHORT — пробой поддержки + ретест + медвежья свеча
  (стратегии 3, 4, 5; паттерны rising wedge, bear flag, H&S…).

Свечное подтверждение перекликается с разделом 2: модель разворота
считается надёжной, когда подтверждается вторым индикатором (книга
упоминает RSI для «трёх белых солдат», стр. 16) — поэтому лонг не берётся
при перекупленном RSI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import calculate_atr, calculate_rsi
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class BookBreakoutConfig(StrategyConfig):
    """Конфигурация стратегии «пробой → ретест → подтверждение» из книги."""

    name: str = "book_breakout"

    # Уровень строится по консолидации до пробоя.
    level_lookback: int = 48  # баров консолидации
    breakout_lookback: int = 12  # пробой должен случиться в последние K баров

    # Ретест: цена вернулась в зону уровня ± tolerance*ATR.
    retest_tolerance_atr: float = 0.75

    # Минимальная чистота ретеста: цена не должна уходить глубоко обратно
    # за уровень (иначе это ложный пробой, а не ретест).
    max_retest_depth_atr: float = 1.5

    # Стоп за экстремумом ретеста + буфер (книга: стоп за уровнем/экстремумом).
    stop_buffer_atr: float = 0.5

    # Минимальный R:R: цель = high(entry + высота консолидации), но не ниже rr.
    min_rr: float = 1.0

    # RSI-подтверждение (стр. 16: паттерн надёжен при подтверждении RSI).
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0

    min_bars: int = 80

    def __post_init__(self):
        need = self.level_lookback + self.breakout_lookback + 5
        if self.min_bars < need:
            self.min_bars = need


class BookBreakoutStrategy(BaseStrategy):
    """Breakout–retest–confirmation по правилам «Простой книги торговли»."""

    def __init__(self, config: BookBreakoutConfig | None = None):
        super().__init__(config or BookBreakoutConfig())
        self.config: BookBreakoutConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        c = self.config
        if len(candles) < c.min_bars:
            return None

        opens = [float(x.open) for x in candles]
        highs = [float(x.high) for x in candles]
        lows = [float(x.low) for x in candles]
        closes = [float(x.close) for x in candles]
        price = float(current_price or closes[-1])

        atr = calculate_atr(highs, lows, closes, period=14)
        if not atr or atr <= 0:
            return None
        rsi_val = calculate_rsi(closes, period=14) or 50.0

        last_open, last_close = opens[-1], closes[-1]
        bull_candle = last_close > last_open  # подтверждающая свеча (стр. 29)
        bear_candle = last_close < last_open

        long_signal = self._check_long(
            highs, lows, closes, price, atr, rsi_val, bull_candle
        )
        if long_signal is not None:
            direction = models.TradeDirection.LONG
            stop, take, meta = long_signal
        else:
            short_signal = self._check_short(
                highs, lows, closes, price, atr, rsi_val, bear_candle
            )
            if short_signal is None:
                return None
            direction = models.TradeDirection.SHORT
            stop, take, meta = short_signal

        entry = Decimal(str(price))
        stop_dec = Decimal(str(round(stop, 10)))
        take_dec = Decimal(str(round(take, 10)))

        signal = Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MOMENTUM,  # пробой-продолжение по тренду
            direction=direction,
            entry_price=entry,
            stop_loss=stop_dec,
            take_profit=take_dec,
            position_size=Decimal("0"),
            risk_amount=Decimal("0"),
            confidence=meta["confidence"],
            market_regime=market_regime or "UNKNOWN",
            features={
                "rsi": round(rsi_val, 2),
                "candle_bull": float(bull_candle),
                "retest_depth_atr": meta["retest_depth_atr"],
                "consolidation_height_pct": meta["height_pct"],
                "breakout_age_bars": float(meta["breakout_age_bars"]),
                "rr": signal_rr(entry, stop_dec, take_dec),
                "pattern": 1.0,  # book_breakout_retest
            },
        )
        logger.info(
            "book_breakout %s %s: entry=%s stop=%s take=%s (ретест %s баров после пробоя)",
            symbol, direction.value, entry, stop_dec, take_dec, meta["breakout_age_bars"],
        )
        return signal

    # ------------------------------------------------------------------ long
    def _check_long(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        price: float,
        atr: float,
        rsi_val: float,
        bull_candle: bool,
    ):
        c = self.config
        # Консолидация — окно до зоны возможного пробоя.
        cons_end = len(closes) - c.breakout_lookback
        cons_start = cons_end - c.level_lookback
        resistance = max(highs[cons_start:cons_end])
        support = min(lows[cons_start:cons_end])
        height = resistance - support
        if height <= 0:
            return None

        tol = c.retest_tolerance_atr * atr

        # Пробой: в breakout-зоне была свеча, закрывшаяся выше уровня.
        breakout_idx = None
        for i in range(cons_end, len(closes)):
            if closes[i] > resistance + atr * 0.1:
                breakout_idx = i
                break  # первый пробой; дальше смотрим именно ретест
        if breakout_idx is None:
            return None

        # АНТИ-FOMO (стр. 29): пробой на последней свече — ретеста ещё не
        # было, входить нельзя.
        breakout_age = len(closes) - 1 - breakout_idx
        if breakout_age < 1:
            return None

        # Ретест: после пробоя цена возвращалась в зону уровня, но не
        # проваливалась глубоко обратно (иначе ложный пробой).
        post_lows = lows[breakout_idx:]
        touched = min(post_lows) <= resistance + tol
        depth = (resistance - min(post_lows)) / atr
        if not touched or depth > c.max_retest_depth_atr:
            return None

        # Цена сейчас у уровня или выше него, и последняя свеча — бычья.
        if price < resistance - tol:
            return None
        if not bull_candle:
            return None

        # RSI-подтверждение: не догоняем перекупленность (стр. 16).
        if rsi_val >= c.rsi_overbought:
            return None

        stop = min(post_lows) - c.stop_buffer_atr * atr
        if stop >= price:
            return None
        take = max(price + (price - stop) * c.min_rr, resistance + height)

        # Чем чище ретест (не глубокий) и моложе пробой — тем выше уверенность.
        clean = 1.0 - min(1.0, depth / c.max_retest_depth_atr)
        fresh = 1.0 - min(1.0, breakout_age / c.breakout_lookback)
        rsi_ok = 1.0 - max(0.0, (rsi_val - 50.0)) / 20.0
        confidence = min(0.9, 0.45 + 0.2 * clean + 0.15 * fresh + 0.1 * max(rsi_ok, 0.0))

        return stop, take, {
            "confidence": confidence,
            "retest_depth_atr": round(depth, 3),
            "height_pct": round(height / price * 100.0, 3),
            "breakout_age_bars": breakout_age,
        }

    # ----------------------------------------------------------------- short
    def _check_short(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        price: float,
        atr: float,
        rsi_val: float,
        bear_candle: bool,
    ):
        c = self.config
        cons_end = len(closes) - c.breakout_lookback
        cons_start = cons_end - c.level_lookback
        resistance = max(highs[cons_start:cons_end])
        support = min(lows[cons_start:cons_end])
        height = resistance - support
        if height <= 0:
            return None

        tol = c.retest_tolerance_atr * atr

        breakout_idx = None
        for i in range(cons_end, len(closes)):
            if closes[i] < support - atr * 0.1:
                breakout_idx = i
                break
        if breakout_idx is None:
            return None

        breakout_age = len(closes) - 1 - breakout_idx
        if breakout_age < 1:
            return None

        post_highs = highs[breakout_idx:]
        touched = max(post_highs) >= support - tol
        depth = (max(post_highs) - support) / atr
        if not touched or depth > c.max_retest_depth_atr:
            return None

        if price > support + tol:
            return None
        if not bear_candle:
            return None

        if rsi_val <= c.rsi_oversold:
            return None

        # Книга (стр. 30): стоп выше предыдущего максимума.
        stop = max(post_highs) + c.stop_buffer_atr * atr
        if stop <= price:
            return None
        take = min(price - (stop - price) * c.min_rr, support - height)

        clean = 1.0 - min(1.0, depth / c.max_retest_depth_atr)
        fresh = 1.0 - min(1.0, breakout_age / c.breakout_lookback)
        rsi_ok = 1.0 - max(0.0, (50.0 - rsi_val)) / 20.0
        confidence = min(0.9, 0.45 + 0.2 * clean + 0.15 * fresh + 0.1 * max(rsi_ok, 0.0))

        return stop, take, {
            "confidence": confidence,
            "retest_depth_atr": round(depth, 3),
            "height_pct": round(height / price * 100.0, 3),
            "breakout_age_bars": breakout_age,
        }

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        return entry_price * (Decimal("1") - Decimal("0.012"))

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        risk = abs(entry_price - stop_loss)
        return [
            {"price": entry_price + risk, "r_multiple": 1.0},
            {"price": entry_price + risk * 2, "r_multiple": 2.0},
        ]


def signal_rr(entry: Decimal, stop: Decimal, take: Decimal) -> float:
    risk = abs(float(entry - stop))
    if risk <= 0:
        return 0.0
    return round(abs(float(take - entry)) / risk, 3)
