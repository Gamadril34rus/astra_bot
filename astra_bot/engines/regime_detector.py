"""
ASTRA BOT — Market Regime Detector
Определение режима рынка
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np

from ..core import events, models
from ..core.utils import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_rsi,
    simple_moving_average,
)

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Режим рынка"""
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    PANIC = "PANIC"
    UNKNOWN = "UNKNOWN"


# Совместимость стратегий с режимами
STRATEGY_REGIME_COMPATIBILITY = {
    "momentum": {
        MarketRegime.BULL_TREND: "ON",
        MarketRegime.BEAR_TREND: "ON",
        MarketRegime.RANGE: "REDUCED",
        MarketRegime.BREAKOUT: "ON",
        MarketRegime.HIGH_VOLATILITY: "REDUCED",
        MarketRegime.LOW_VOLATILITY: "ON",
        MarketRegime.PANIC: "OFF",
        MarketRegime.UNKNOWN: "OFF",
    },
    "mean_reversion": {
        MarketRegime.BULL_TREND: "REDUCED",
        MarketRegime.BEAR_TREND: "REDUCED",
        MarketRegime.RANGE: "ON",
        MarketRegime.BREAKOUT: "OFF",
        MarketRegime.HIGH_VOLATILITY: "OFF",
        MarketRegime.LOW_VOLATILITY: "ON",
        MarketRegime.PANIC: "OFF",
        MarketRegime.UNKNOWN: "OFF",
    },
    "adaptive_grid": {
        MarketRegime.BULL_TREND: "OFF",
        MarketRegime.BEAR_TREND: "OFF",
        MarketRegime.RANGE: "ON",
        MarketRegime.BREAKOUT: "OFF",
        MarketRegime.HIGH_VOLATILITY: "OFF",
        MarketRegime.LOW_VOLATILITY: "REDUCED",
        MarketRegime.PANIC: "OFF",
        MarketRegime.UNKNOWN: "OFF",
    },
    "arbitrage": {
        MarketRegime.BULL_TREND: "ON",
        MarketRegime.BEAR_TREND: "ON",
        MarketRegime.RANGE: "ON",
        MarketRegime.BREAKOUT: "REDUCED",
        MarketRegime.HIGH_VOLATILITY: "OFF",
        MarketRegime.LOW_VOLATILITY: "ON",
        MarketRegime.PANIC: "OFF",
        MarketRegime.UNKNOWN: "ON",
    },
}


@dataclass
class RegimeIndicators:
    """Индикаторы для определения режима"""
    # Trend
    ema_20: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    ema_trend_score: float = 0.0  # -1 до 1

    # Trend strength
    adx: float | None = None
    adx_trend_strength: float = 0.0  # 0-100

    # Volatility
    atr: float | None = None
    atr_percent: float = 0.0  # ATR/price * 100
    bb_width: float = 0.0

    # Mean reversion
    rsi: float | None = None
    bb_position: float = 0.0  # -1 до 1

    # Volume
    volume_ratio: float = 1.0  # vs SMA volume
    volume_zscore: float = 0.0

    # Price structure
    price_change_24h: float = 0.0
    price_change_1h: float = 0.0
    high_low_range_pct: float = 0.0

    # Additional
    ob_imbalance: float = 0.0
    trades_per_minute: float = 0.0


@dataclass
class RegimeResult:
    """Результат определения режима"""
    symbol: str
    regime: MarketRegime
    confidence: float  # 0-1
    indicators: RegimeIndicators
    timestamp: datetime = field(default_factory=datetime.utcnow)
    changed: bool = False
    previous_regime: MarketRegime | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "regime": self.regime.value,
            "confidence": self.confidence,
            "indicators": {
                "ema_trend_score": self.indicators.ema_trend_score,
                "adx": self.indicators.adx,
                "atr_percent": self.indicators.atr_percent,
                "rsi": self.indicators.rsi,
                "bb_position": self.indicators.bb_position,
                "volume_ratio": self.indicators.volume_ratio,
            },
            "changed": self.changed,
        }


class MarketRegimeDetector:
    """
    Детектор режима рынка.

    Анализирует рыночные данные и определяет текущий режим:
    - BULL_TREND
    - BEAR_TREND
    - RANGE
    - BREAKOUT
    - HIGH_VOLATILITY
    - LOW_VOLATILITY
    - PANIC
    """

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}

        # Пороги для определения режима
        self.trend_threshold = self.config.get("trend_threshold", 0.1)
        self.adx_trend_threshold = self.config.get("adx_trend_threshold", 25)
        self.atr_volatility_high = self.config.get("atr_volatility_high", 3.0)
        self.atr_volatility_low = self.config.get("atr_volatility_low", 0.5)
        self.rsi_overbought = self.config.get("rsi_overbought", 70)
        self.rsi_oversold = self.config.get("rsi_oversold", 30)
        self.bb_width_tight = self.config.get("bb_width_tight", 0.02)
        self.bb_width_wide = self.config.get("bb_width_wide", 0.1)
        self.volume_spike_threshold = self.config.get("volume_spike_threshold", 2.0)
        self.panic_drawdown = self.config.get("panic_drawdown", 0.10)  # 10%
        self.panic_volume_spike = self.config.get("panic_volume_spike", 5.0)

        # Кэш режимов по символам
        self._regimes: dict[str, MarketRegime] = {}
        self._last_update: dict[str, datetime] = {}

        # Ранее определённые режимы для детекции изменений
        self._previous_regimes: dict[str, MarketRegime] = {}

    def detect(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook: models.OrderBook | None = None,
        current_price: float | None = None,
    ) -> RegimeResult:
        """
        Определить режим рынка для символа.

        Args:
            symbol: Торговый символ
            candles: Список свечей (минимум 200 для EMA200)
            orderbook: Стакан заявок (опционально)
            current_price: Текущая цена (опционально)

        Returns:
            RegimeResult с определённым режимом
        """
        if len(candles) < 50:
            return RegimeResult(
                symbol=symbol,
                regime=MarketRegime.UNKNOWN,
                confidence=0.0,
                indicators=RegimeIndicators(),
            )

        # Расчёт индикаторов
        indicators = self._calculate_indicators(candles, orderbook)

        # Определение режима
        regime = self._classify_regime(symbol, indicators, candles)

        # Проверка изменения режима
        previous = self._previous_regimes.get(symbol)
        changed = regime != previous
        if changed and previous:
            logger.info(
                f"Regime changed for {symbol}: "
                f"{previous.value} -> {regime.value}"
            )
            events.emit_async(
                events.EventType.REGIME_CHANGE,
                {
                    "symbol": symbol,
                    "old_regime": previous.value,
                    "new_regime": regime.value,
                    "confidence": 0.8,  # TODO: рассчитать уверенность изменения
                }
            )

        # Обновление кэша
        self._previous_regimes[symbol] = regime
        self._regimes[symbol] = regime
        self._last_update[symbol] = datetime.utcnow()

        return RegimeResult(
            symbol=symbol,
            regime=regime,
            confidence=self._calculate_confidence(regime, indicators),
            indicators=indicators,
            changed=changed,
            previous_regime=previous,
        )

    def _calculate_indicators(
        self,
        candles: list[models.Candle],
        orderbook: models.OrderBook | None = None,
    ) -> RegimeIndicators:
        """Рассчитать индикаторы для определения режима"""
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        volumes = [float(c.volume) for c in candles]

        indicators = RegimeIndicators()

        # EMA
        if len(closes) >= 20:
            indicators.ema_20 = float(simple_moving_average(closes[-20:], 20))
        if len(closes) >= 50:
            indicators.ema_50 = float(simple_moving_average(closes[-50:], 50))
        if len(closes) >= 200:
            indicators.ema_200 = float(simple_moving_average(closes[-200:], 200))

        # Trend score на основе EMA
        if indicators.ema_20 and indicators.ema_50:
            if indicators.ema_20 > indicators.ema_50:
                indicators.ema_trend_score = min(1.0,
                    (indicators.ema_20 - indicators.ema_50) / indicators.ema_50)
            else:
                indicators.ema_trend_score = max(-1.0,
                    (indicators.ema_20 - indicators.ema_50) / indicators.ema_50)

        # EMA 50 vs 200
        if indicators.ema_50 and indicators.ema_200:
            if indicators.ema_50 > indicators.ema_200:
                indicators.ema_trend_score = max(indicators.ema_trend_score, 0.3)
            else:
                indicators.ema_trend_score = min(indicators.ema_trend_score, -0.3)

        # ATR и волатильность
        if len(highs) >= 15:
            atr = calculate_atr(highs[-15:], lows[-15:], closes[-15:], period=14)
            if atr:
                indicators.atr = atr
                current_price = closes[-1]
                if current_price > 0:
                    indicators.atr_percent = (atr / current_price) * 100

        # Bollinger Bands
        bb = calculate_bollinger_bands(closes)
        if bb:
            indicators.bb_width = bb["bandwidth"]
            if bb["middle"] > 0:
                indicators.bb_position = (closes[-1] - bb["lower"]) / (bb["upper"] - bb["lower"]) * 2 - 1

        # RSI
        if len(closes) >= 15:
            indicators.rsi = calculate_rsi(closes, period=14)

        # Volume analysis
        if len(volumes) >= 20:
            avg_volume = simple_moving_average(volumes, 20)
            if avg_volume and avg_volume > 0:
                indicators.volume_ratio = volumes[-1] / avg_volume
                # Z-score
                volume_std = np.std(volumes[-20:])
                if volume_std > 0:
                    indicators.volume_zscore = (volumes[-1] - avg_volume) / volume_std

        # Price changes
        if len(closes) >= 24:  # 24 часа для 1h timeframe
            indicators.price_change_24h = ((closes[-1] - closes[-24]) / closes[-24]) * 100
        if len(closes) >= 1:
            indicators.price_change_1h = ((closes[-1] - closes[-0]) / closes[-0]) * 100 if closes[-0] > 0 else 0

        # High-Low range
        if len(highs) >= 1 and len(lows) >= 1 and closes[-1] > 0:
            indicators.high_low_range_pct = ((highs[-1] - lows[-1]) / closes[-1]) * 100

        # Order book imbalance
        if orderbook:
            indicators.ob_imbalance = float(orderbook.get_imbalance())

        return indicators

    def _classify_regime(
        self,
        symbol: str,
        indicators: RegimeIndicators,
        candles: list[models.Candle],
    ) -> MarketRegime:
        """Классифицировать режим рынка"""

        # Паника — самая высокая приоритетность
        if self._is_panic(indicators, candles):
            return MarketRegime.PANIC

        # Высокая волатильность
        if indicators.atr_percent > self.atr_volatility_high:
            return MarketRegime.HIGH_VOLATILITY

        # Низкая волатильность
        if indicators.atr_percent < self.atr_volatility_low:
            return MarketRegime.LOW_VOLATILITY

        # Определение тренда
        is_bullish = (
            indicators.ema_trend_score > self.trend_threshold
            and indicators.ema_50 is not None
            and indicators.ema_200 is not None
            and indicators.ema_50 > indicators.ema_200
        )

        is_bearish = (
            indicators.ema_trend_score < -self.trend_threshold
            and indicators.ema_50 is not None
            and indicators.ema_200 is not None
            and indicators.ema_50 < indicators.ema_200
        )

        # Проверка силы тренда через ADX
        strong_trend = indicators.adx_trend_strength > self.adx_trend_threshold if indicators.adx_trend_strength else False

        # Разрыв диапазона (breakout)
        if self._is_breakout(indicators, candles):
            return MarketRegime.BREAKOUT

        # Явный тренд
        if is_bullish and strong_trend:
            return MarketRegime.BULL_TREND

        if is_bearish and strong_trend:
            return MarketRegime.BEAR_TREND

        # Боковик если нет явного тренда
        if self._is_range(indicators):
            return MarketRegime.RANGE

        # Если есть слабый тренд но не сильный
        if is_bullish:
            return MarketRegime.BULL_TREND

        if is_bearish:
            return MarketRegime.BEAR_TREND

        return MarketRegime.UNKNOWN

    def _is_panic(
        self,
        indicators: RegimeIndicators,
        candles: list[models.Candle],
    ) -> bool:
        """Проверить панику"""
        # Быстрое падение цены + огромный объём

        if len(candles) < 24:
            return False

        # Падение более 10% за 24 часа
        if indicators.price_change_24h < -self.panic_drawdown * 100:
            # И объём в 5 раз выше нормы
            if indicators.volume_zscore > self.panic_volume_spike:
                return True

        # Обвал за последние 1-2 часа
        recent_candles = candles[-6:]  # 6 свечей по 1h = 6h
        if len(recent_candles) >= 3:
            drop = (float(recent_candles[0].close) - float(recent_candles[-1].close)) / float(recent_candles[0].open) * 100
            if drop > 5 and indicators.volume_zscore > 3:
                return True

        return False

    def _is_breakout(
        self,
        indicators: RegimeIndicators,
        candles: list[models.Candle],
    ) -> bool:
        """Проверить разрыв диапазона"""
        if len(candles) < 20:
            return False

        # Разрыв при высоком объёме
        if indicators.volume_zscore > 2.0:
            # Цена вышла за пределы Bollinger Bands
            if abs(indicators.bb_position) > 0.9:
                return True

        # Резкий пробой свечи за пределы диапазона
        recent_candles = candles[-5:]
        if len(recent_candles) >= 3:
            old_high = max(float(c.high) for c in recent_candles[:-1])
            old_low = min(float(c.low) for c in recent_candles[:-1])
            current = float(candles[-1].close)
            current_range = float(candles[-1].high) - float(candles[-1].low)

            if current > old_high * 1.01 or current < old_low * 0.99:
                if current_range > sum(float(c.high) - float(c.low) for c in recent_candles[:-1]) / len(recent_candles[:-1]) * 1.5:
                    return True

        return False

    def _is_range(self, indicators: RegimeIndicators) -> bool:
        """Проверить боковик"""
        # Слабая тенденция + узкие BB + RSI в середине

        weak_trend = abs(indicators.ema_trend_score) < self.trend_threshold

        narrow_bands = indicators.bb_width < self.bb_width_tight

        rsi_neutral = (
            indicators.rsi is not None
            and self.rsi_oversold < indicators.rsi < self.rsi_overbought
        )

        return weak_trend and (narrow_bands or rsi_neutral)

    def _calculate_confidence(self, regime: MarketRegime, indicators: RegimeIndicators) -> float:
        """Рассчитать уверенность определения режима"""
        confidence = 0.5  # Базовая

        # Уверенность на основе индикаторов
        if abs(indicators.ema_trend_score) > 0.2:
            confidence += 0.1

        if indicators.adx_trend_strength and indicators.adx_trend_strength > 30:
            confidence += 0.15
        elif indicators.adx_trend_strength and indicators.adx_trend_strength > 20:
            confidence += 0.05

        if indicators.atr_percent > 0:
            if regime in [MarketRegime.HIGH_VOLATILITY, MarketRegime.PANIC]:
                confidence += 0.1

        if indicators.volume_zscore > 2:
            confidence += 0.1

        # Ограничиваем
        return min(0.95, max(0.3, confidence))

    def get_strategy_compatibility(
        self,
        strategy_name: str,
        regime: MarketRegime,
    ) -> str:
        """Получить уровень совместимости стратегии с режимом"""
        return STRATEGY_REGIME_COMPATIBILITY.get(
            strategy_name, {}
        ).get(regime, "OFF")

    def is_strategy_allowed(
        self,
        strategy_name: str,
        regime: MarketRegime,
    ) -> bool:
        """Проверить разрешена ли стратегия в текущем режиме"""
        compatibility = self.get_strategy_compatibility(strategy_name, regime)
        return compatibility in ["ON", "REDUCED"]

    def get_last_update(self, symbol: str) -> datetime | None:
        """Получить время последнего обновления"""
        return self._last_update.get(symbol)


# Глобальный детектор
_regime_detector: MarketRegimeDetector | None = None


def get_regime_detector() -> MarketRegimeDetector:
    """Получить глобальный детектор режима"""
    global _regime_detector
    if _regime_detector is None:
        _regime_detector = MarketRegimeDetector()
    return _regime_detector


def reset_regime_detector():
    """Сбросить детектор (для тестов)"""
    global _regime_detector
    _regime_detector = None
