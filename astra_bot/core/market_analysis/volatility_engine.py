"""
ASTRA BOT - Volatility Engine

Движок анализа волатильности (ТЗ Пункты 6, 36)

Исследует:
- realized volatility
- ATR
- volatility percentile
- volatility expansion
- volatility contraction

Создает VOLATILITY STATE.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

import numpy as np

from ...core import models, utils

logger = logging.getLogger(__name__)


class VolatilityState(str, Enum):
    """Состояния волатильности"""
    EXTREME_HIGH = "EXTREME_HIGH"  # Экстремально высокая
    HIGH = "HIGH"  # Высокая
    NORMAL = "NORMAL"  # Нормальная
    LOW = "LOW"  # Низкая
    EXTREME_LOW = "EXTREME_LOW"  # Экстремально низкая
    UNKNOWN = "UNKNOWN"


class VolatilityTrend(str, Enum):
    """Тренды волатильности"""
    INCREASING = "INCREASING"  # Растущая
    DECREASING = "DECREASING"  # Падающая
    STABLE = "STABLE"  # Стабильная
    UNKNOWN = "UNKNOWN"


@dataclass
class VolatilityMetrics:
    """Метрики волатильности"""
    symbol: str
    timestamp: datetime
    timeframe: str
    
    # ATR
    atr: float | None = None
    atr_percent: float = 0.0
    atr_trend: VolatilityTrend = VolatilityTrend.UNKNOWN
    
    # Standard Deviation
    std_returns: float | None = None
    std_20: float | None = None
    std_50: float | None = None
    std_100: float | None = None
    
    # Realized Volatility
    realized_volatility: float = 0.0
    realized_volatility_annualized: float = 0.0
    
    # Volatility Percentile
    volatility_percentile: float = 0.0
    
    # Historical comparison
    historical_avg_volatility: float = 0.0
    historical_std_volatility: float = 0.0
    volatility_zscore: float = 0.0
    
    # Range
    high_low_range: float = 0.0
    high_low_range_pct: float = 0.0
    
    # State
    state: VolatilityState = VolatilityState.UNKNOWN
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "atr": self.atr,
            "atr_percent": self.atr_percent,
            "atr_trend": self.atr_trend.value,
            "std_returns": self.std_returns,
            "std_20": self.std_20,
            "std_50": self.std_50,
            "std_100": self.std_100,
            "realized_volatility": self.realized_volatility,
            "realized_volatility_annualized": self.realized_volatility_annualized,
            "volatility_percentile": self.volatility_percentile,
            "historical_avg_volatility": self.historical_avg_volatility,
            "historical_std_volatility": self.historical_std_volatility,
            "volatility_zscore": self.volatility_zscore,
            "high_low_range": self.high_low_range,
            "high_low_range_pct": self.high_low_range_pct,
            "state": self.state.value,
        }


@dataclass
class VolatilityExpansion:
    """Расширение волатильности"""
    symbol: str
    timestamp: datetime
    timeframe: str
    
    # Expansion metrics
    expansion_rate: float = 0.0  # Скорость расширения
    expansion_acceleration: float = 0.0  # Ускорение расширения
    is_expanding: bool = False
    is_contracting: bool = False
    
    # Historical comparison
    historical_expansion_rate: float = 0.0
    expansion_zscore: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "expansion_rate": self.expansion_rate,
            "expansion_acceleration": self.expansion_acceleration,
            "is_expanding": self.is_expanding,
            "is_contracting": self.is_contracting,
            "historical_expansion_rate": self.historical_expansion_rate,
            "expansion_zscore": self.expansion_zscore,
        }


@dataclass
class VolatilityAnalysis:
    """Полный анализ волатильности"""
    symbol: str
    timestamp: datetime
    timeframe: str
    
    # Метрики
    metrics: VolatilityMetrics
    
    # Расширение/сжатие
    expansion: VolatilityExpansion
    
    # История
    volatility_history: list[float] = field(default_factory=list)
    
    # Статистика
    statistics: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "metrics": self.metrics.to_dict(),
            "expansion": self.expansion.to_dict(),
            "statistics": self.statistics,
        }


class VolatilityEngine:
    """
    Движок анализа волатильности.
    
    Рассчитывает различные метрики волатильности и определяет состояние.
    """
    
    def __init__(self):
        # Пороги для классификации
        self.thresholds = {
            "extreme_high_volatility": 8.0,  # ATR/price > 8%
            "high_volatility": 4.0,  # ATR/price > 4%
            "low_volatility": 1.0,  # ATR/price < 1%
            "extreme_low_volatility": 0.5,  # ATR/price < 0.5%
            
            "volatility_percentile_high": 80.0,
            "volatility_percentile_low": 20.0,
            
            "volatility_zscore_high": 2.0,
            "volatility_zscore_low": -2.0,
        }
        
        # История волатильности
        self._volatility_history: dict[str, list[float]] = {}
    
    def calculate_atr(
        self,
        candles: list[models.Candle],
        period: int = 14,
    ) -> float | None:
        """
        Рассчитать ATR.
        
        Args:
            candles: Список свечей
            period: Период
        
        Returns:
            ATR
        """
        if len(candles) < period:
            return None
        
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]
        
        return utils.calculate_atr(highs, lows, closes, period=period)
    
    def calculate_realized_volatility(
        self,
        returns: list[float],
        period: int = 14,
        annualized: bool = True,
    ) -> float:
        """
        Рассчитать реализованную волатильность.
        
        Args:
            returns: Список доходностей
            period: Период
            annualized: Аннуализировать
        
        Returns:
            Реализованная волатильность
        """
        if not returns or len(returns) < 2:
            return 0.0
        
        # Стандартное отклонение доходностей
        std_dev = float(np.std(returns))
        
        if annualized:
            # Аннуализация (предполагая 252 торговых дня в году)
            # Для внутридневных данных: sqrt(252 * 24 * 60 / period_minutes)
            # Упрощённо: sqrt(252) для дневных данных
            std_dev *= np.sqrt(252)
        
        return std_dev
    
    def calculate_volatility_percentile(
        self,
        current_volatility: float,
        historical_volatility: list[float],
    ) -> float:
        """
        Рассчитать перцентиль волатильности.
        
        Args:
            current_volatility: Текущая волатильность
            historical_volatility: Историческая волатильность
        
        Returns:
            Перцентиль (0-100)
        """
        if not historical_volatility:
            return 50.0
        
        percentile = sum(1 for v in historical_volatility if v <= current_volatility) / len(historical_volatility) * 100
        return percentile
    
    def analyze_volatility(
        self,
        symbol: str,
        candles: list[models.Candle],
        timeframe: str = "1h",
        timestamp: datetime | None = None,
    ) -> VolatilityAnalysis:
        """
        Полный анализ волатильности.
        
        Args:
            symbol: Символ
            candles: Список свечей
            timeframe: Таймфрейм
            timestamp: Временная метка
        
        Returns:
            Полный анализ волатильности
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        if not candles:
            return VolatilityAnalysis(
                symbol=symbol,
                timestamp=timestamp,
                timeframe=timeframe,
                metrics=VolatilityMetrics(
                    symbol=symbol,
                    timestamp=timestamp,
                    timeframe=timeframe,
                ),
                expansion=VolatilityExpansion(
                    symbol=symbol,
                    timestamp=timestamp,
                    timeframe=timeframe,
                ),
            )
        
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        
        # Рассчитать возвраты
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        
        # Рассчитать ATR
        atr = self.calculate_atr(candles, period=14)
        atr_pct = (atr / closes[-1] * 100) if atr and closes[-1] > 0 else 0.0
        
        # Рассчитать стандартные отклонения
        std_returns = float(np.std(returns)) if returns else 0.0
        std_20 = float(np.std(closes[-20:])) if len(closes) >= 20 else 0.0
        std_50 = float(np.std(closes[-50:])) if len(closes) >= 50 else 0.0
        std_100 = float(np.std(closes[-100:])) if len(closes) >= 100 else 0.0
        
        # Рассчитать реализованную волатильность
        realized_volatility = self.calculate_realized_volatility(returns, period=14, annualized=False)
        realized_volatility_annualized = self.calculate_realized_volatility(returns, period=14, annualized=True)
        
        # Рассчитать high-low range
        if highs and lows and closes:
            high_low_range = highs[-1] - lows[-1]
            high_low_range_pct = (high_low_range / closes[-1] * 100) if closes[-1] > 0 else 0.0
        else:
            high_low_range = 0.0
            high_low_range_pct = 0.0
        
        # Рассчитать перцентиль волатильности
        key = f"{symbol}_{timeframe}"
        if key in self._volatility_history and self._volatility_history[key]:
            volatility_percentile = self.calculate_volatility_percentile(
                atr_pct, self._volatility_history[key]
            )
        else:
            volatility_percentile = 50.0
        
        # Рассчитать историческую статистику
        if key in self._volatility_history and self._volatility_history[key]:
            historical_avg = float(np.mean(self._volatility_history[key]))
            historical_std = float(np.std(self._volatility_history[key]))
            volatility_zscore = (atr_pct - historical_avg) / historical_std if historical_std > 0 else 0.0
        else:
            historical_avg = 0.0
            historical_std = 0.0
            volatility_zscore = 0.0
        
        # Определить состояние волатильности
        if atr_pct >= self.thresholds["extreme_high_volatility"]:
            state = VolatilityState.EXTREME_HIGH
        elif atr_pct >= self.thresholds["high_volatility"]:
            state = VolatilityState.HIGH
        elif atr_pct <= self.thresholds["extreme_low_volatility"]:
            state = VolatilityState.EXTREME_LOW
        elif atr_pct <= self.thresholds["low_volatility"]:
            state = VolatilityState.LOW
        else:
            state = VolatilityState.NORMAL
        
        # Создать метрики
        metrics = VolatilityMetrics(
            symbol=symbol,
            timestamp=timestamp,
            timeframe=timeframe,
            atr=atr,
            atr_percent=atr_pct,
            std_returns=std_returns,
            std_20=std_20,
            std_50=std_50,
            std_100=std_100,
            realized_volatility=realized_volatility,
            realized_volatility_annualized=realized_volatility_annualized,
            volatility_percentile=volatility_percentile,
            historical_avg_volatility=historical_avg,
            historical_std_volatility=historical_std,
            volatility_zscore=volatility_zscore,
            high_low_range=high_low_range,
            high_low_range_pct=high_low_range_pct,
            state=state,
        )
        
        # Рассчитать расширение волатильности
        expansion = self._calculate_volatility_expansion(symbol, timeframe, atr_pct)
        
        # Обновить историю
        if key not in self._volatility_history:
            self._volatility_history[key] = []
        self._volatility_history[key].append(atr_pct)
        
        # Ограничить историю
        if len(self._volatility_history[key]) > 1000:
            self._volatility_history[key] = self._volatility_history[key][-1000:]
        
        # Статистика
        statistics = {
            "current_volatility_pct": atr_pct,
            "volatility_state": state.value,
            "volatility_percentile": volatility_percentile,
            "volatility_zscore": volatility_zscore,
            "is_expanding": expansion.is_expanding,
            "is_contracting": expansion.is_contracting,
        }
        
        return VolatilityAnalysis(
            symbol=symbol,
            timestamp=timestamp,
            timeframe=timeframe,
            metrics=metrics,
            expansion=expansion,
            volatility_history=self._volatility_history.get(key, []),
            statistics=statistics,
        )
    
    def _calculate_volatility_expansion(
        self,
        symbol: str,
        timeframe: str,
        current_volatility: float,
    ) -> VolatilityExpansion:
        """
        Рассчитать расширение волатильности.
        
        Args:
            symbol: Символ
            timeframe: Таймфрейм
            current_volatility: Текущая волатильность
        
        Returns:
            Расширение волатильности
        """
        key = f"{symbol}_{timeframe}"
        
        if key not in self._volatility_history or len(self._volatility_history[key]) < 2:
            return VolatilityExpansion(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                timeframe=timeframe,
            )
        
        history = self._volatility_history[key]
        
        # Рассчитать скорость изменения
        recent_history = history[-10:]  # Последние 10 значений
        if len(recent_history) >= 2:
            expansion_rate = (current_volatility - recent_history[0]) / recent_history[0] * 100
        else:
            expansion_rate = 0.0
        
        # Рассчитать ускорение
        if len(recent_history) >= 3:
            # Изменение скорости
            prev_rate = (recent_history[-1] - recent_history[-2]) / recent_history[-2] * 100
            current_rate = (current_volatility - recent_history[-1]) / recent_history[-1] * 100
            expansion_acceleration = current_rate - prev_rate
        else:
            expansion_acceleration = 0.0
        
        # Определить расширение/сжатие
        is_expanding = expansion_rate > 5.0  # Более 5% увеличение
        is_contracting = expansion_rate < -5.0  # Более 5% уменьшение
        
        # Рассчитать историческую скорость расширения
        if len(recent_history) >= 10:
            historical_rates = []
            for i in range(1, len(recent_history)):
                rate = (recent_history[i] - recent_history[i-1]) / recent_history[i-1] * 100
                historical_rates.append(rate)
            historical_expansion_rate = float(np.mean(historical_rates))
            expansion_zscore = (expansion_rate - historical_expansion_rate) / np.std(historical_rates) if len(historical_rates) > 1 else 0.0
        else:
            historical_expansion_rate = 0.0
            expansion_zscore = 0.0
        
        return VolatilityExpansion(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            timeframe=timeframe,
            expansion_rate=expansion_rate,
            expansion_acceleration=expansion_acceleration,
            is_expanding=is_expanding,
            is_contracting=is_contracting,
            historical_expansion_rate=historical_expansion_rate,
            expansion_zscore=expansion_zscore,
        )
    
    def get_volatility_state(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> VolatilityState:
        """
        Получить текущее состояние волатильности.
        
        Args:
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Состояние волатильности
        """
        key = f"{symbol}_{timeframe}"
        
        if key not in self._volatility_history or not self._volatility_history[key]:
            return VolatilityState.UNKNOWN
        
        current_volatility = self._volatility_history[key][-1]
        
        if current_volatility >= self.thresholds["extreme_high_volatility"]:
            return VolatilityState.EXTREME_HIGH
        elif current_volatility >= self.thresholds["high_volatility"]:
            return VolatilityState.HIGH
        elif current_volatility <= self.thresholds["extreme_low_volatility"]:
            return VolatilityState.EXTREME_LOW
        elif current_volatility <= self.thresholds["low_volatility"]:
            return VolatilityState.LOW
        else:
            return VolatilityState.NORMAL


# Глобальный экземпляр
_volatility_engine: VolatilityEngine | None = None


def get_volatility_engine() -> VolatilityEngine:
    """Получить глобальный Volatility Engine"""
    global _volatility_engine
    if _volatility_engine is None:
        _volatility_engine = VolatilityEngine()
    return _volatility_engine


def reset_volatility_engine():
    """Сбросить Volatility Engine (для тестов)"""
    global _volatility_engine
    _volatility_engine = VolatilityEngine()
