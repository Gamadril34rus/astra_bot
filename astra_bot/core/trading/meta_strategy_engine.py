"""
ASTRA BOT - Meta-Strategy Engine

Движок мета-стратегий (ТЗ Пункты 33-34, 46-48, 67-68, 72, 75-77, 79-80, 85, 91, 98-99)

Создает мета-стратегии из:
- signal aggregation
- strategy combination
- regime switching
- volatility targeting
- drawdown control
- correlation filtering
- ensemble methods
- multi-timeframe
- cross-asset

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class MetaStrategyType(str, Enum):
    """Типы мета-стратегий"""
    SIGNAL_AGGREGATION = "signal_aggregation"
    STRATEGY_COMBINATION = "strategy_combination"
    REGIME_SWITCHING = "regime_switching"
    VOLATILITY_TARGETING = "volatility_targeting"
    DRAWDOWN_CONTROL = "drawdown_control"
    CORRELATION_FILTERING = "correlation_filtering"
    ENSEMBLE = "ensemble"
    MULTI_TIMEFRAME = "multi_timeframe"
    CROSS_ASSET = "cross_asset"


class AggregationMethod(str, Enum):
    """Методы агрегации сигналов"""
    VOTING = "voting"
    WEIGHTED_VOTING = "weighted_voting"
    AVERAGE = "average"
    WEIGHTED_AVERAGE = "weighted_average"
    MEDIAN = "median"
    MAXIMUM = "maximum"
    MINIMUM = "minimum"


class Regime(str, Enum):
    """Режимы рынка"""
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    REVERSAL = "reversal"
    UNKNOWN = "unknown"


@dataclass
class Signal:
    """Сигнал"""
    signal_id: str
    strategy_id: str
    symbol: str
    timestamp: datetime
    
    # Значение сигнала
    value: float  # -1 до 1, или 0 для нейтрального
    strength: float = 0.0  # 0-1
    direction: str = "neutral"  # buy/sell/neutral
    
    # Уверенность
    confidence: float = 0.0
    
    # Временной горизонт
    timeframe: str = "1h"
    
    # Дополнительная информация
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "value": self.value,
            "strength": self.strength,
            "direction": self.direction,
            "confidence": self.confidence,
            "timeframe": self.timeframe,
            "metadata": self.metadata,
        }


@dataclass
class StrategyPerformance:
    """Производительность стратегии"""
    strategy_id: str
    symbol: str
    
    # Метрики
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    
    # Временной горизонт
    timeframe: str = "1h"
    
    # Уверенность
    confidence: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "total_return": self.total_return,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "timeframe": self.timeframe,
            "confidence": self.confidence,
        }


@dataclass
class AggregatedSignal:
    """Агрегированный сигнал"""
    aggregated_id: str
    symbol: str
    timestamp: datetime
    
    # Агрегированное значение
    value: float = 0.0
    strength: float = 0.0
    direction: str = "neutral"
    
    # Уверенность
    confidence: float = 0.0
    
    # Метод агрегации
    method: AggregationMethod = AggregationMethod.VOTING
    
    # Исходные сигналы
    signals: list[Signal] = field(default_factory=list)
    
    # Веса
    weights: dict[str, float] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregated_id": self.aggregated_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "value": self.value,
            "strength": self.strength,
            "direction": self.direction,
            "confidence": self.confidence,
            "method": self.method.value,
            "signals": [s.to_dict() for s in self.signals],
            "weights": self.weights,
        }


@dataclass
class MetaStrategy:
    """Мета-стратегия"""
    meta_strategy_id: str
    meta_strategy_type: MetaStrategyType
    symbol: str
    
    # Стратегии
    strategy_ids: list[str] = field(default_factory=list)
    
    # Параметры
    parameters: dict[str, Any] = field(default_factory=dict)
    
    # Текущий сигнал
    current_signal: AggregatedSignal | None = None
    
    # Производительность
    performance: StrategyPerformance = field(default_factory=StrategyPerformance)
    
    # Статус
    active: bool = True
    
    # Временная метка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "meta_strategy_id": self.meta_strategy_id,
            "meta_strategy_type": self.meta_strategy_type.value,
            "symbol": self.symbol,
            "strategy_ids": self.strategy_ids,
            "parameters": self.parameters,
            "current_signal": self.current_signal.to_dict() if self.current_signal else None,
            "performance": self.performance.to_dict(),
            "active": self.active,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class MetaStrategyResult:
    """Результат мета-стратегии"""
    meta_strategy_id: str
    symbol: str
    timestamp: datetime
    
    # Агрегированный сигнал
    aggregated_signal: AggregatedSignal | None = None
    
    # Индивидуальные сигналы
    signals: list[Signal] = field(default_factory=list)
    
    # Производительность стратегий
    strategy_performances: list[StrategyPerformance] = field(default_factory=list)
    
    # Текущий режим
    current_regime: Regime = Regime.UNKNOWN
    
    # Целевая волатильность
    target_volatility: float = 0.0
    current_volatility: float = 0.0
    
    # Текущая просадка
    current_drawdown: float = 0.0
    max_drawdown_limit: float = 0.0
    
    # Корреляции
    correlations: dict[str, float] = field(default_factory=dict)
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    # Уверенность
    confidence: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "meta_strategy_id": self.meta_strategy_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "aggregated_signal": self.aggregated_signal.to_dict() if self.aggregated_signal else None,
            "signals": [s.to_dict() for s in self.signals],
            "strategy_performances": [p.to_dict() for p in self.strategy_performances],
            "current_regime": self.current_regime.value,
            "target_volatility": self.target_volatility,
            "current_volatility": self.current_volatility,
            "current_drawdown": self.current_drawdown,
            "max_drawdown_limit": self.max_drawdown_limit,
            "correlations": self.correlations,
            "recommendations": self.recommendations,
            "confidence": self.confidence,
        }


class MetaStrategyEngine:
    """
    Движок мета-стратегий.
    
    Создает и управляет мета-стратегиями из различных стратегий.
    """
    
    def __init__(self):
        # Мета-стратегии
        self._meta_strategies: dict[str, MetaStrategy] = {}
        
        # Сигналы
        self._signals: dict[str, Signal] = {}
        
        # Производительность стратегий
        self._strategy_performances: dict[str, StrategyPerformance] = {}
        
        # Пороги
        self.thresholds = {
            "min_confidence": 0.1,
            "max_confidence": 1.0,
            "min_strength": 0.1,
            "max_strength": 1.0,
            "high_volatility_pct": 5.0,
            "low_volatility_pct": 1.0,
            "max_drawdown_limit": 20.0,
            "correlation_threshold": 0.7,
        }
    
    def create_meta_strategy(
        self,
        meta_strategy_id: str,
        meta_strategy_type: MetaStrategyType,
        symbol: str,
        strategy_ids: list[str],
        parameters: dict[str, Any] | None = None,
    ) -> MetaStrategy:
        """
        Создать мета-стратегию.
        
        Args:
            meta_strategy_id: ID мета-стратегии
            meta_strategy_type: Тип мета-стратегии
            symbol: Символ
            strategy_ids: ID стратегий
            parameters: Параметры
        
        Returns:
            Мета-стратегия
        """
        meta_strategy = MetaStrategy(
            meta_strategy_id=meta_strategy_id,
            meta_strategy_type=meta_strategy_type,
            symbol=symbol,
            strategy_ids=strategy_ids,
            parameters=parameters or {},
        )
        
        self._meta_strategies[meta_strategy_id] = meta_strategy
        
        return meta_strategy
    
    def add_signal(
        self,
        signal_id: str,
        strategy_id: str,
        symbol: str,
        value: float,
        strength: float = 0.0,
        direction: str = "neutral",
        confidence: float = 0.5,
        timeframe: str = "1h",
        metadata: dict[str, Any] | None = None,
    ) -> Signal:
        """
        Добавить сигнал.
        
        Args:
            signal_id: ID сигнала
            strategy_id: ID стратегии
            symbol: Символ
            value: Значение сигнала
            strength: Сила сигнала
            direction: Направление
            confidence: Уверенность
            timeframe: Временной горизонт
            metadata: Метаданные
        
        Returns:
            Сигнал
        """
        signal = Signal(
            signal_id=signal_id,
            strategy_id=strategy_id,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            value=value,
            strength=strength,
            direction=direction,
            confidence=confidence,
            timeframe=timeframe,
            metadata=metadata or {},
        )
        
        self._signals[signal_id] = signal
        
        return signal
    
    def add_strategy_performance(
        self,
        strategy_id: str,
        symbol: str,
        total_return: float,
        sharpe_ratio: float,
        max_drawdown: float,
        win_rate: float,
        profit_factor: float,
        timeframe: str = "1h",
        confidence: float = 0.5,
    ) -> StrategyPerformance:
        """
        Добавить производительность стратегии.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            total_return: Общий возврат
            sharpe_ratio: Коэффициент Шарпа
            max_drawdown: Максимальная просадка
            win_rate: Процент выигрышных сделок
            profit_factor: Коэффициент прибыльности
            timeframe: Временной горизонт
            confidence: Уверенность
        
        Returns:
            Производительность стратегии
        """
        performance = StrategyPerformance(
            strategy_id=strategy_id,
            symbol=symbol,
            total_return=total_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            profit_factor=profit_factor,
            timeframe=timeframe,
            confidence=confidence,
        )
        
        self._strategy_performances[strategy_id] = performance
        
        return performance
    
    def aggregate_signals(
        self,
        symbol: str,
        strategy_ids: list[str],
        method: AggregationMethod = AggregationMethod.VOTING,
        weights: dict[str, float] | None = None,
    ) -> AggregatedSignal:
        """
        Агрегировать сигналы.
        
        Args:
            symbol: Символ
            strategy_ids: ID стратегий
            method: Метод агрегации
            weights: Веса стратегий
        
        Returns:
            Агрегированный сигнал
        """
        # Получить сигналы
        signals = [s for s in self._signals.values() 
                  if s.symbol == symbol and s.strategy_id in strategy_ids]
        
        if not signals:
            return AggregatedSignal(
                aggregated_id=f"agg_{symbol}_{datetime.now(timezone.utc).isoformat()}",
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                method=method,
            )
        
        if weights is None:
            weights = {s.strategy_id: 1.0 for s in signals}
        
        # Агрегация
        if method == AggregationMethod.VOTING:
            # Голосование: считаем количество buy/sell/neutral
            buy_votes = sum(1 for s in signals if s.direction == "buy")
            sell_votes = sum(1 for s in signals if s.direction == "sell")
            neutral_votes = sum(1 for s in signals if s.direction == "neutral")
            
            total = len(signals)
            
            if buy_votes > sell_votes and buy_votes > neutral_votes:
                direction = "buy"
                value = buy_votes / total
            elif sell_votes > buy_votes and sell_votes > neutral_votes:
                direction = "sell"
                value = -sell_votes / total
            else:
                direction = "neutral"
                value = 0.0
            
            strength = max(buy_votes, sell_votes, neutral_votes) / total
            confidence = np.mean([s.confidence for s in signals])
        
        elif method == AggregationMethod.WEIGHTED_VOTING:
            # Взвешенное голосование
            buy_weight = sum(weights.get(s.strategy_id, 1.0) for s in signals if s.direction == "buy")
            sell_weight = sum(weights.get(s.strategy_id, 1.0) for s in signals if s.direction == "sell")
            neutral_weight = sum(weights.get(s.strategy_id, 1.0) for s in signals if s.direction == "neutral")
            
            total_weight = sum(weights.get(s.strategy_id, 1.0) for s in signals)
            
            if buy_weight > sell_weight and buy_weight > neutral_weight:
                direction = "buy"
                value = buy_weight / total_weight
            elif sell_weight > buy_weight and sell_weight > neutral_weight:
                direction = "sell"
                value = -sell_weight / total_weight
            else:
                direction = "neutral"
                value = 0.0
            
            strength = max(buy_weight, sell_weight, neutral_weight) / total_weight
            confidence = np.mean([s.confidence * weights.get(s.strategy_id, 1.0) for s in signals])
        
        elif method == AggregationMethod.AVERAGE:
            # Среднее значение
            value = float(np.mean([s.value for s in signals]))
            strength = float(np.mean([s.strength for s in signals]))
            confidence = float(np.mean([s.confidence for s in signals]))
            
            if value > 0.2:
                direction = "buy"
            elif value < -0.2:
                direction = "sell"
            else:
                direction = "neutral"
        
        elif method == AggregationMethod.WEIGHTED_AVERAGE:
            # Взвешенное среднее
            total_weight = sum(weights.get(s.strategy_id, 1.0) for s in signals)
            value = sum(s.value * weights.get(s.strategy_id, 1.0) for s in signals) / total_weight
            strength = sum(s.strength * weights.get(s.strategy_id, 1.0) for s in signals) / total_weight
            confidence = sum(s.confidence * weights.get(s.strategy_id, 1.0) for s in signals) / total_weight
            
            if value > 0.2:
                direction = "buy"
            elif value < -0.2:
                direction = "sell"
            else:
                direction = "neutral"
        
        elif method == AggregationMethod.MEDIAN:
            # Медиана
            values = sorted([s.value for s in signals])
            value = float(np.median(values))
            strength = float(np.median([s.strength for s in signals]))
            confidence = float(np.median([s.confidence for s in signals]))
            
            if value > 0.2:
                direction = "buy"
            elif value < -0.2:
                direction = "sell"
            else:
                direction = "neutral"
        
        elif method == AggregationMethod.MAXIMUM:
            # Максимум
            value = float(np.max([s.value for s in signals]))
            strength = float(np.max([s.strength for s in signals]))
            confidence = float(np.max([s.confidence for s in signals]))
            
            if value > 0.2:
                direction = "buy"
            elif value < -0.2:
                direction = "sell"
            else:
                direction = "neutral"
        
        elif method == AggregationMethod.MINIMUM:
            # Минимум
            value = float(np.min([s.value for s in signals]))
            strength = float(np.min([s.strength for s in signals]))
            confidence = float(np.min([s.confidence for s in signals]))
            
            if value > 0.2:
                direction = "buy"
            elif value < -0.2:
                direction = "sell"
            else:
                direction = "neutral"
        
        else:
            value = 0.0
            strength = 0.0
            direction = "neutral"
            confidence = 0.0
        
        aggregated_signal = AggregatedSignal(
            aggregated_id=f"agg_{symbol}_{datetime.now(timezone.utc).isoformat()}",
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            value=value,
            strength=strength,
            direction=direction,
            confidence=confidence,
            method=method,
            signals=signals,
            weights=weights or {},
        )
        
        return aggregated_signal
    
    def determine_regime(
        self,
        symbol: str,
        market_data: dict[str, Any],
    ) -> Regime:
        """
        Определить текущий режим рынка.
        
        Args:
            symbol: Символ
            market_data: Данные рынка
        
        Returns:
            Режим рынка
        """
        # Упрощённая логика определения режима
        # В реальности нужно использовать более сложные методы
        
        volatility = market_data.get("volatility", 0.0)
        trend = market_data.get("trend", 0.0)
        
        if volatility > self.thresholds["high_volatility_pct"]:
            if trend > 0.5:
                return Regime.TREND_UP
            elif trend < -0.5:
                return Regime.TREND_DOWN
            else:
                return Regime.HIGH_VOLATILITY
        elif volatility < self.thresholds["low_volatility_pct"]:
            return Regime.LOW_VOLATILITY
        elif trend > 0.3:
            return Regime.TREND_UP
        elif trend < -0.3:
            return Regime.TREND_DOWN
        else:
            return Regime.RANGE
    
    def apply_regime_switching(
        self,
        meta_strategy_id: str,
        current_regime: Regime,
    ) -> dict[str, Any]:
        """
        Применить переключение режимов.
        
        Args:
            meta_strategy_id: ID мета-стратегии
            current_regime: Текущий режим
        
        Returns:
            Рекомендации по стратегиям
        """
        meta_strategy = self._meta_strategies.get(meta_strategy_id)
        if not meta_strategy:
            return {"error": "Meta strategy not found"}
        
        # Определить, какие стратегии использовать в текущем режиме
        recommendations = {}
        
        for strategy_id in meta_strategy.strategy_ids:
            performance = self._strategy_performances.get(strategy_id)
            
            if not performance:
                recommendations[strategy_id] = {
                    "enabled": False,
                    "reason": "No performance data",
                }
                continue
            
            # Упрощённая логика
            if current_regime == Regime.TREND_UP:
                # В тренде вверх использовать стратегии с высоким Sharpe
                if performance.sharpe_ratio > 1.0:
                    recommendations[strategy_id] = {
                        "enabled": True,
                        "weight": 1.0,
                        "reason": "High Sharpe ratio in uptrend",
                    }
                else:
                    recommendations[strategy_id] = {
                        "enabled": False,
                        "weight": 0.0,
                        "reason": "Low Sharpe ratio",
                    }
            elif current_regime == Regime.TREND_DOWN:
                # В тренде вниз использовать стратегии с высоким Sharpe
                if performance.sharpe_ratio > 1.0:
                    recommendations[strategy_id] = {
                        "enabled": True,
                        "weight": 1.0,
                        "reason": "High Sharpe ratio in downtrend",
                    }
                else:
                    recommendations[strategy_id] = {
                        "enabled": False,
                        "weight": 0.0,
                        "reason": "Low Sharpe ratio",
                    }
            elif current_regime == Regime.HIGH_VOLATILITY:
                # В высокой волатильности использовать стратегии с низкой просадкой
                if performance.max_drawdown < 10.0:
                    recommendations[strategy_id] = {
                        "enabled": True,
                        "weight": 1.0,
                        "reason": "Low max drawdown in high volatility",
                    }
                else:
                    recommendations[strategy_id] = {
                        "enabled": False,
                        "weight": 0.0,
                        "reason": "High max drawdown",
                    }
            elif current_regime == Regime.LOW_VOLATILITY:
                # В низкой волатильности использовать стратегии с высоким win rate
                if performance.win_rate > 0.6:
                    recommendations[strategy_id] = {
                        "enabled": True,
                        "weight": 1.0,
                        "reason": "High win rate in low volatility",
                    }
                else:
                    recommendations[strategy_id] = {
                        "enabled": False,
                        "weight": 0.0,
                        "reason": "Low win rate",
                    }
            else:
                # В остальных случаях использовать все стратегии
                recommendations[strategy_id] = {
                    "enabled": True,
                    "weight": 1.0,
                    "reason": "Default",
                }
        
        return recommendations
    
    def apply_volatility_targeting(
        self,
        current_volatility: float,
        target_volatility: float,
        position_size: float,
    ) -> float:
        """
        Применить таргетирование волатильности.
        
        Args:
            current_volatility: Текущая волатильность
            target_volatility: Целевая волатильность
            position_size: Текущий размер позиции
        
        Returns:
            Скорректированный размер позиции
        """
        if target_volatility <= 0:
            return position_size
        
        # Если текущая волатильность ниже целевой, увеличить размер
        if current_volatility < target_volatility:
            ratio = target_volatility / current_volatility
            return position_size * min(ratio, 2.0)  # Не более чем в 2 раза
        # Если текущая волатильность выше целевой, уменьшить размер
        elif current_volatility > target_volatility:
            ratio = current_volatility / target_volatility
            return position_size / max(ratio, 0.5)  # Не менее чем в 2 раза
        else:
            return position_size
    
    def apply_drawdown_control(
        self,
        current_drawdown: float,
        max_drawdown_limit: float,
        position_size: float,
    ) -> float:
        """
        Применить контроль просадки.
        
        Args:
            current_drawdown: Текущая просадка
            max_drawdown_limit: Лимит просадки
            position_size: Текущий размер позиции
        
        Returns:
            Скорректированный размер позиции
        """
        if max_drawdown_limit <= 0:
            return position_size
        
        # Если просадка близка к лимиту, уменьшить размер
        drawdown_ratio = current_drawdown / max_drawdown_limit
        
        if drawdown_ratio >= 1.0:
            # Не открывать новые позиции
            return 0.0
        elif drawdown_ratio >= 0.8:
            # Уменьшить размер на 80%
            return position_size * 0.2
        elif drawdown_ratio >= 0.5:
            # Уменьшить размер на 50%
            return position_size * 0.5
        else:
            return position_size
    
    def apply_correlation_filtering(
        self,
        correlations: dict[str, float],
        portfolio_symbols: list[str],
        position_size: float,
    ) -> float:
        """
        Применить фильтрацию по корреляции.
        
        Args:
            correlations: Корреляции с другими активами
            portfolio_symbols: Символы в портфеле
            position_size: Текущий размер позиции
        
        Returns:
            Скорректированный размер позиции
        """
        if not portfolio_symbols:
            return position_size
        
        # Рассчитать среднюю корреляцию
        avg_correlation = np.mean(list(correlations.values())) if correlations else 0.0
        
        # Если высокая корреляция, уменьшить размер
        if avg_correlation > self.thresholds["correlation_threshold"]:
            reduction = (avg_correlation - self.thresholds["correlation_threshold"]) * 100
            return max(0, position_size * (1 - reduction / 100))
        else:
            return position_size
    
    def execute_meta_strategy(
        self,
        meta_strategy_id: str,
        market_data: dict[str, Any],
        portfolio_data: dict[str, Any] | None = None,
    ) -> MetaStrategyResult:
        """
        Исполнить мета-стратегию.
        
        Args:
            meta_strategy_id: ID мета-стратегии
            market_data: Данные рынка
            portfolio_data: Данные портфеля
        
        Returns:
            Результат мета-стратегии
        """
        meta_strategy = self._meta_strategies.get(meta_strategy_id)
        if not meta_strategy:
            return MetaStrategyResult(
                meta_strategy_id=meta_strategy_id,
                symbol="",
                timestamp=datetime.now(timezone.utc),
                recommendations=["Meta strategy not found"],
            )
        
        # Получить сигналы
        signals = [s for s in self._signals.values() 
                  if s.symbol == meta_strategy.symbol and s.strategy_id in meta_strategy.strategy_ids]
        
        # Получить производительность стратегий
        strategy_performances = [p for p in self._strategy_performances.values() 
                                if p.symbol == meta_strategy.symbol and p.strategy_id in meta_strategy.strategy_ids]
        
        # Определить режим
        current_regime = self.determine_regime(meta_strategy.symbol, market_data)
        
        # Агрегировать сигналы
        aggregated_signal = self.aggregate_signals(
            meta_strategy.symbol,
            meta_strategy.strategy_ids,
            AggregationMethod.WEIGHTED_VOTING,
        )
        
        # Применить переключение режимов
        regime_recommendations = self.apply_regime_switching(meta_strategy_id, current_regime)
        
        # Применить таргетирование волатильности
        current_volatility = market_data.get("volatility", 0.0)
        target_volatility = meta_strategy.parameters.get("target_volatility", 2.0)
        
        # Применить контроль просадки
        current_drawdown = portfolio_data.get("current_drawdown", 0.0) if portfolio_data else 0.0
        max_drawdown_limit = meta_strategy.parameters.get("max_drawdown_limit", 20.0)
        
        # Применить фильтрацию по корреляции
        correlations = meta_strategy.parameters.get("correlations", {})
        portfolio_symbols = portfolio_data.get("symbols", []) if portfolio_data else []
        
        # Создать рекомендации
        recommendations = []
        
        if aggregated_signal.direction == "buy":
            recommendations.append(f"Aggregated signal: BUY (confidence: {aggregated_signal.confidence:.2f})")
        elif aggregated_signal.direction == "sell":
            recommendations.append(f"Aggregated signal: SELL (confidence: {aggregated_signal.confidence:.2f})")
        else:
            recommendations.append("Aggregated signal: NEUTRAL")
        
        recommendations.append(f"Current regime: {current_regime.value}")
        
        if current_volatility > target_volatility:
            recommendations.append("Current volatility is above target - consider reducing position size")
        elif current_volatility < target_volatility:
            recommendations.append("Current volatility is below target - consider increasing position size")
        
        if current_drawdown > max_drawdown_limit * 0.8:
            recommendations.append("Approaching max drawdown limit - consider reducing risk")
        
        if avg_correlation := np.mean(list(correlations.values())) if correlations else 0:
            if avg_correlation > self.thresholds["correlation_threshold"]:
                recommendations.append(f"High correlation with portfolio ({avg_correlation:.2f}) - consider reducing position size")
        
        # Рассчитать итоговую уверенность
        confidence = aggregated_signal.confidence
        
        result = MetaStrategyResult(
            meta_strategy_id=meta_strategy_id,
            symbol=meta_strategy.symbol,
            timestamp=datetime.now(timezone.utc),
            aggregated_signal=aggregated_signal,
            signals=signals,
            strategy_performances=strategy_performances,
            current_regime=current_regime,
            target_volatility=target_volatility,
            current_volatility=current_volatility,
            current_drawdown=current_drawdown,
            max_drawdown_limit=max_drawdown_limit,
            correlations=correlations,
            recommendations=recommendations,
            confidence=confidence,
        )
        
        return result


# Глобальный экземпляр
_meta_strategy_engine: MetaStrategyEngine | None = None


def get_meta_strategy_engine() -> MetaStrategyEngine:
    """Получить глобальный Meta-Strategy Engine"""
    global _meta_strategy_engine
    if _meta_strategy_engine is None:
        _meta_strategy_engine = MetaStrategyEngine()
    return _meta_strategy_engine


def reset_meta_strategy_engine():
    """Сбросить Meta-Strategy Engine (для тестов)"""
    global _meta_strategy_engine
    _meta_strategy_engine = MetaStrategyEngine()
