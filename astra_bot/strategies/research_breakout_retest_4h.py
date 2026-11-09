"""Research-only 4h breakout + retest entry detector.

This is an isolated port of the entry conditions used by
scripts/research_bt_walkforward.py (strat="break"). It is disabled by default
and is deliberately not registered in the live or Zeus paper pipelines.
The matching research exits (1.5R target, 1.5 ATR stop, 24-bar time stop)
must be validated in a separate runner before this candidate is promoted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class ResearchBreakoutRetest4HConfig(StrategyConfig):
    name: str = "research_breakout_retest_4h"
    enabled: bool = False
    warmup_bars: int = 200
    breakout_lookback: int = 20
    atr_period: int = 14
    volume_period: int = 20
    min_volume_ratio: float = 1.3
    breakout_close_tolerance: float = 0.002
    retest_tolerance: float = 0.008
    atr_stop_mult: float = 1.5
    take_rr: float = 1.5


class ResearchBreakoutRetest4HStrategy(
    BaseStrategy[ResearchBreakoutRetest4HConfig]
):
    """Disabled-by-default signal detector for the research 4h setup."""

    preferred_timeframe = "4h"

    def __init__(
        self, config: ResearchBreakoutRetest4HConfig | None = None
    ) -> None:
        super().__init__(config or ResearchBreakoutRetest4HConfig())
        self.config: ResearchBreakoutRetest4HConfig
        self._last_direction: models.TradeDirection | None = None

    @staticmethod
    def _ema(values: list[float], span: int) -> float:
        alpha = 2.0 / (span + 1.0)
        value = values[0]
        for item in values[1:]:
            value = alpha * item + (1.0 - alpha) * value
        return value

    @staticmethod
    def _atr(candles: list[models.Candle], period: int) -> float:
        highs = [float(candle.high) for candle in candles]
        lows = [float(candle.low) for candle in candles]
        closes = [float(candle.close) for candle in candles]
        true_ranges: list[float] = []
        for index, (high, low, close) in enumerate(
            zip(highs, lows, closes, strict=True)
        ):
            previous_close = closes[index - 1] if index else close
            true_ranges.append(
                max(
                    high - low,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )
            )
        window = true_ranges[-period:]
        return sum(window) / len(window) if len(window) == period else 0.0

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        if self.should_skip_signal():
            return None

        try:
            config = self.config
            if len(candles) < max(
                config.warmup_bars + 1,
                config.breakout_lookback + 1,
                config.volume_period,
                config.atr_period + 1,
                51,
            ):
                return None
            if any(candle.timeframe != "4h" for candle in candles):
                return None
            open_times = [int(candle.open_time) for candle in candles]
            if any(right <= left for left, right in zip(open_times, open_times[1:])):
                return None

            highs = [float(candle.high) for candle in candles]
            lows = [float(candle.low) for candle in candles]
            closes = [float(candle.close) for candle in candles]
            volumes = [float(candle.volume) for candle in candles]
            if any(
                not all(value > 0 for value in values)
                for values in (highs, lows, closes, volumes)
            ):
                return None
            if any(high < low for high, low in zip(highs, lows, strict=True)):
                return None

            # The pipeline supplies closed entry candles. The latest one is
            # the research signal bar; its predecessor is the prior-close test.
            level_high = max(highs[-config.breakout_lookback - 1 : -1])
            level_low = min(lows[-config.breakout_lookback - 1 : -1])
            previous_close = closes[-2]
            signal_high = highs[-1]
            signal_low = lows[-1]
            signal_close = closes[-1]

            volume_average = sum(volumes[-config.volume_period :]) / config.volume_period
            if volume_average <= 0:
                return None
            volume_ratio = volumes[-1] / volume_average
            if volume_ratio < config.min_volume_ratio:
                return None

            ema20 = self._ema(closes, 20)
            ema50 = self._ema(closes, 50)
            atr_value = self._atr(candles, config.atr_period)
            if atr_value <= 0:
                return None

            long_signal = (
                previous_close > level_high * (1.0 - config.breakout_close_tolerance)
                and signal_low <= level_high * (1.0 + config.retest_tolerance)
                and signal_close > level_high
                and ema20 > ema50
            )
            short_signal = (
                previous_close < level_low * (1.0 + config.breakout_close_tolerance)
                and signal_high >= level_low * (1.0 - config.retest_tolerance)
                and signal_close < level_low
                and ema20 < ema50
            )
            if not long_signal and not short_signal:
                return None

            price = float(current_price) if current_price is not None else signal_close
            if price <= 0:
                return None
            stop_distance = atr_value * config.atr_stop_mult
            if stop_distance <= 0:
                return None

            if long_signal:
                direction = models.TradeDirection.LONG
                stop = price - stop_distance
                take = price + stop_distance * config.take_rr
                breakout_level = level_high
            else:
                direction = models.TradeDirection.SHORT
                stop = price + stop_distance
                take = price - stop_distance * config.take_rr
                breakout_level = level_low

            if min(price, stop, take) <= 0:
                return None

            self._last_direction = direction
            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MOMENTUM,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(stop)),
                take_profit=Decimal(str(take)),
                position_size=Decimal("0"),
                risk_amount=Decimal("0"),
                confidence=0.5,
                market_regime=market_regime or "UNKNOWN",
                features={
                    "breakout_level": float(breakout_level),
                    "volume_ratio": float(volume_ratio),
                    "ema20": float(ema20),
                    "ema50": float(ema50),
                    "atr14": float(atr_value),
                    "stop_atr": float(config.atr_stop_mult),
                    "take_rr": float(config.take_rr),
                },
            )
        except (ArithmeticError, TypeError, ValueError) as exc:
            logger.warning("%s evaluate invalid market data: %s", self.name, exc)
            return None

    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        candles: list[models.Candle],
        atr: float | None = None,
    ) -> Decimal:
        atr_value = atr or self._atr(candles, self.config.atr_period)
        distance = Decimal(str(atr_value or float(entry_price) * 0.015))
        distance *= Decimal(str(self.config.atr_stop_mult))
        if self._last_direction == models.TradeDirection.SHORT:
            return entry_price + distance
        return entry_price - distance

    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        candles: list[models.Candle],
    ) -> list[dict]:
        risk = abs(entry_price - stop_loss)
        direction = (
            Decimal("1")
            if self._last_direction != models.TradeDirection.SHORT
            else Decimal("-1")
        )
        return [
            {
                "price": entry_price + direction * risk * Decimal(str(self.config.take_rr)),
                "fraction": 1.0,
            }
        ]
