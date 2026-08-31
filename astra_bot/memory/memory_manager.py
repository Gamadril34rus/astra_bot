"""
ASTRA BOT — Memory Manager

Управление памятью (Master Specification v2, Section 52)

Память разделить:
- OBSERVATIONS
- HYPOTHESES
- LESSONS
- STRATEGIES
- FEATURES
- EVENTS
- EXPERIMENTS
- MODELS
- EXECUTION
- LOSSES

Каждый объект имеет:
- confidence
- sample_size
- validation_status
- created_at
- updated_at
- source
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    """Типы памяти"""
    OBSERVATIONS = "observations"
    HYPOTHESES = "hypotheses"
    LESSONS = "lessons"
    STRATEGIES = "strategies"
    FEATURES = "features"
    EVENTS = "events"
    EXPERIMENTS = "experiments"
    MODELS = "models"
    EXECUTION = "execution"
    LOSSES = "losses"


class ValidationStatus(str, Enum):
    """Статусы валидации"""
    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    INVALIDATED = "invalidated"
    FAILED = "failed"


@dataclass
class MemoryObject:
    """Базовый объект памяти"""
    object_id: str
    memory_type: MemoryType
    data: dict[str, Any]
    
    # Метаданные
    confidence: float = 0.0  # 0-1
    sample_size: int = 0
    validation_status: ValidationStatus = ValidationStatus.UNVALIDATED
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    source: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "memory_type": self.memory_type.value,
            "data": self.data,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "validation_status": self.validation_status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source": self.source,
        }


@dataclass
class Observation(MemoryObject):
    """Наблюдение"""
    symbol: str = ""
    timeframe: str = ""
    observation_type: str = ""  # trend, volatility, volume, etc.
    value: float = 0.0
    
    def __post_init__(self):
        self.memory_type = MemoryType.OBSERVATIONS


@dataclass
class Lesson(MemoryObject):
    """Урок"""
    condition: str = ""
    observed_effect: str = ""
    statistical_evidence: float = 0.0
    oos_result: float = 0.0
    limitations: list[str] = field(default_factory=list)
    
    def __post_init__(self):
        self.memory_type = MemoryType.LESSONS


@dataclass
class StrategyMemory(MemoryObject):
    """Память стратегии"""
    strategy_name: str = ""
    performance: dict[str, float] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    status: str = "ACTIVE"  # ACTIVE, DEGRADED, RETIRED
    
    def __post_init__(self):
        self.memory_type = MemoryType.STRATEGIES


@dataclass
class FeatureMemory(MemoryObject):
    """Память фактора"""
    feature_name: str = ""
    predictive_power: float = 0.0
    stability: float = 0.0
    regime_dependency: float = 0.0
    
    def __post_init__(self):
        self.memory_type = MemoryType.FEATURES


@dataclass
class EventMemory(MemoryObject):
    """Память события"""
    event_type: str = ""
    event_description: str = ""
    market_impact: float = 0.0
    
    def __post_init__(self):
        self.memory_type = MemoryType.EVENTS


class MemoryManager:
    """
    Менеджер памяти.
    
    Управляет всеми типами памяти и обеспечивает их хранение и извлечение.
    """
    
    def __init__(self):
        # Хранение объектов памяти
        self.memory: dict[str, MemoryObject] = {}
        
        # Индексы для быстрого поиска
        self.indexes: dict[MemoryType, dict[str, list[str]]] = {
            MemoryType.OBSERVATIONS: {},
            MemoryType.HYPOTHESES: {},
            MemoryType.LESSONS: {},
            MemoryType.STRATEGIES: {},
            MemoryType.FEATURES: {},
            MemoryType.EVENTS: {},
            MemoryType.EXPERIMENTS: {},
            MemoryType.MODELS: {},
            MemoryType.EXECUTION: {},
            MemoryType.LOSSES: {},
        }
        
        # Счётчики
        self.counters: dict[MemoryType, int] = {
            MemoryType.OBSERVATIONS: 0,
            MemoryType.HYPOTHESES: 0,
            MemoryType.LESSONS: 0,
            MemoryType.STRATEGIES: 0,
            MemoryType.FEATURES: 0,
            MemoryType.EVENTS: 0,
            MemoryType.EXPERIMENTS: 0,
            MemoryType.MODELS: 0,
            MemoryType.EXECUTION: 0,
            MemoryType.LOSSES: 0,
        }
    
    def _generate_id(self, memory_type: MemoryType) -> str:
        """Сгенерировать идентификатор объекта"""
        self.counters[memory_type] += 1
        return f"{memory_type.value}_{self.counters[memory_type]:05d}"
    
    def store(self, obj: MemoryObject) -> str:
        """
        Сохранить объект в памяти.
        
        Args:
            obj: Объект памяти
        
        Returns:
            Идентификатор объекта
        """
        if not obj.object_id:
            obj.object_id = self._generate_id(obj.memory_type)
        
        # Сохранить объект
        self.memory[obj.object_id] = obj
        
        # Обновить индекс
        if obj.memory_type not in self.indexes:
            self.indexes[obj.memory_type] = {}
        
        # Индексировать по символу (для наблюдений)
        if obj.memory_type == MemoryType.OBSERVATIONS and hasattr(obj, 'symbol'):
            if obj.symbol not in self.indexes[obj.memory_type]:
                self.indexes[obj.memory_type][obj.symbol] = []
            self.indexes[obj.memory_type][obj.symbol].append(obj.object_id)
        
        # Индексировать по имени (для стратегий, факторов)
        if obj.memory_type == MemoryType.STRATEGIES and hasattr(obj, 'strategy_name'):
            if obj.strategy_name not in self.indexes[obj.memory_type]:
                self.indexes[obj.memory_type][obj.strategy_name] = []
            self.indexes[obj.memory_type][obj.strategy_name].append(obj.object_id)
        
        if obj.memory_type == MemoryType.FEATURES and hasattr(obj, 'feature_name'):
            if obj.feature_name not in self.indexes[obj.memory_type]:
                self.indexes[obj.memory_type][obj.feature_name] = []
            self.indexes[obj.memory_type][obj.feature_name].append(obj.object_id)
        
        logger.info(f"Stored {obj.memory_type.value} {obj.object_id}")
        
        return obj.object_id
    
    def retrieve(self, object_id: str) -> MemoryObject | None:
        """
        Извлечь объект из памяти.
        
        Args:
            object_id: Идентификатор объекта
        
        Returns:
            Объект памяти или None
        """
        return self.memory.get(object_id)
    
    def search(
        self,
        memory_type: MemoryType,
        query: dict[str, Any] | None = None,
        limit: int = 100
    ) -> list[MemoryObject]:
        """
        Поиск объектов в памяти.
        
        Args:
            memory_type: Тип памяти
            query: Запрос для поиска
            limit: Лимит результатов
        
        Returns:
            Список объектов
        """
        results = []
        
        if memory_type in self.indexes:
            # Поиск по индексу
            if query and "symbol" in query:
                symbol = query["symbol"]
                if symbol in self.indexes[memory_type]:
                    for object_id in self.indexes[memory_type][symbol][:limit]:
                        obj = self.memory.get(object_id)
                        if obj:
                            results.append(obj)
            
            elif query and "name" in query:
                name = query["name"]
                if name in self.indexes[memory_type]:
                    for object_id in self.indexes[memory_type][name][:limit]:
                        obj = self.memory.get(object_id)
                        if obj:
                            results.append(obj)
            
            else:
                # Вернуть все объекты данного типа
                for object_id, obj in self.memory.items():
                    if obj.memory_type == memory_type:
                        results.append(obj)
                        if len(results) >= limit:
                            break
        
        return results
    
    def store_observation(
        self,
        symbol: str,
        timeframe: str,
        observation_type: str,
        value: float,
        confidence: float = 0.5,
        sample_size: int = 1,
        source: str = "market_data"
    ) -> str:
        """
        Сохранить наблюдение.
        
        Args:
            symbol: Символ
            timeframe: Таймфрейм
            observation_type: Тип наблюдения
            value: Значение
            confidence: Уверенность
            sample_size: Размер выборки
            source: Источник
        
        Returns:
            Идентификатор наблюдения
        """
        observation = Observation(
            object_id="",
            data={
                "symbol": symbol,
                "timeframe": timeframe,
                "observation_type": observation_type,
                "value": value,
            },
            confidence=confidence,
            sample_size=sample_size,
            source=source,
            symbol=symbol,
            timeframe=timeframe,
            observation_type=observation_type,
            value=value
        )
        
        return self.store(observation)
    
    def store_lesson(
        self,
        condition: str,
        observed_effect: str,
        statistical_evidence: float,
        oos_result: float,
        confidence: float,
        limitations: list[str] = None,
        source: str = "trading"
    ) -> str:
        """
        Сохранить урок.
        
        Args:
            condition: Условие
            observed_effect: Наблюдаемый эффект
            statistical_evidence: Статистическое доказательство
            oos_result: Результат OOS
            confidence: Уверенность
            limitations: Ограничения
            source: Источник
        
        Returns:
            Идентификатор урока
        """
        lesson = Lesson(
            object_id="",
            data={
                "condition": condition,
                "observed_effect": observed_effect,
                "statistical_evidence": statistical_evidence,
                "oos_result": oos_result,
                "limitations": limitations or [],
            },
            confidence=confidence,
            sample_size=1,
            source=source,
            condition=condition,
            observed_effect=observed_effect,
            statistical_evidence=statistical_evidence,
            oos_result=oos_result,
            limitations=limitations or []
        )
        
        return self.store(lesson)
    
    def store_strategy(
        self,
        strategy_name: str,
        performance: dict[str, float],
        parameters: dict[str, Any],
        status: str = "ACTIVE",
        confidence: float = 0.5,
        sample_size: int = 1,
        source: str = "backtest"
    ) -> str:
        """
        Сохранить информацию о стратегии.
        
        Args:
            strategy_name: Имя стратегии
            performance: Производительность
            parameters: Параметры
            status: Статус
            confidence: Уверенность
            sample_size: Размер выборки
            source: Источник
        
        Returns:
            Идентификатор стратегии
        """
        strategy = StrategyMemory(
            object_id="",
            data={
                "strategy_name": strategy_name,
                "performance": performance,
                "parameters": parameters,
                "status": status,
            },
            confidence=confidence,
            sample_size=sample_size,
            source=source,
            strategy_name=strategy_name,
            performance=performance,
            parameters=parameters,
            status=status
        )
        
        return self.store(strategy)
    
    def store_feature(
        self,
        feature_name: str,
        predictive_power: float,
        stability: float,
        regime_dependency: float,
        confidence: float = 0.5,
        sample_size: int = 1,
        source: str = "research"
    ) -> str:
        """
        Сохранить информацию о факторе.
        
        Args:
            feature_name: Имя фактора
            predictive_power: Предсказательная сила
            stability: Стабильность
            regime_dependency: Зависимость от режима
            confidence: Уверенность
            sample_size: Размер выборки
            source: Источник
        
        Returns:
            Идентификатор фактора
        """
        feature = FeatureMemory(
            object_id="",
            data={
                "feature_name": feature_name,
                "predictive_power": predictive_power,
                "stability": stability,
                "regime_dependency": regime_dependency,
            },
            confidence=confidence,
            sample_size=sample_size,
            source=source,
            feature_name=feature_name,
            predictive_power=predictive_power,
            stability=stability,
            regime_dependency=regime_dependency
        )
        
        return self.store(feature)
    
    def store_event(
        self,
        event_type: str,
        event_description: str,
        market_impact: float,
        confidence: float = 0.5,
        sample_size: int = 1,
        source: str = "news"
    ) -> str:
        """
        Сохранить событие.
        
        Args:
            event_type: Тип события
            event_description: Описание события
            market_impact: Влияние на рынок
            confidence: Уверенность
            sample_size: Размер выборки
            source: Источник
        
        Returns:
            Идентификатор события
        """
        event = EventMemory(
            object_id="",
            data={
                "event_type": event_type,
                "event_description": event_description,
                "market_impact": market_impact,
            },
            confidence=confidence,
            sample_size=sample_size,
            source=source,
            event_type=event_type,
            event_description=event_description,
            market_impact=market_impact
        )
        
        return self.store(event)
    
    def get_statistics(self) -> dict[str, Any]:
        """
        Получить статистику памяти.
        
        Returns:
            Статистика
        """
        by_type = {}
        for memory_type in MemoryType:
            count = len([
                obj for obj in self.memory.values()
                if obj.memory_type == memory_type
            ])
            by_type[memory_type.value] = count
        
        return {
            "total_objects": len(self.memory),
            "by_type": by_type,
            "indexes_size": {k.value: len(v) for k, v in self.indexes.items()}
        }
    
    def validate_object(
        self,
        object_id: str,
        validation_status: ValidationStatus
    ) -> bool:
        """
        Валидировать объект.
        
        Args:
            object_id: Идентификатор объекта
            validation_status: Статус валидации
        
        Returns:
            True если объект найден и обновлён
        """
        obj = self.memory.get(object_id)
        
        if not obj:
            return False
        
        obj.validation_status = validation_status
        obj.updated_at = datetime.now()
        
        return True
    
    def cleanup_old_objects(self, max_age_days: int = 90) -> dict[str, int]:
        """
        Очистить старые объекты.
        
        Args:
            max_age_days: Максимальный возраст в днях
        
        Returns:
            Количество удалённых объектов по типам
        """
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        cleaned = {}
        objects_to_remove = []
        
        for object_id, obj in self.memory.items():
            if obj.updated_at < cutoff:
                objects_to_remove.append(object_id)
                if obj.memory_type.value not in cleaned:
                    cleaned[obj.memory_type.value] = 0
                cleaned[obj.memory_type.value] += 1
        
        for object_id in objects_to_remove:
            # Удалить из индексов
            obj = self.memory[object_id]
            if obj.memory_type in self.indexes:
                if hasattr(obj, 'symbol') and obj.symbol in self.indexes[obj.memory_type]:
                    self.indexes[obj.memory_type][obj.symbol].remove(object_id)
                if hasattr(obj, 'strategy_name') and obj.strategy_name in self.indexes[obj.memory_type]:
                    self.indexes[obj.memory_type][obj.strategy_name].remove(object_id)
                if hasattr(obj, 'feature_name') and obj.feature_name in self.indexes[obj.memory_type]:
                    self.indexes[obj.memory_type][obj.feature_name].remove(object_id)
            
            # Удалить из памяти
            del self.memory[object_id]
        
        return cleaned


# Глобальный экземпляр Memory Manager
_memory_manager: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    """Получить глобальный Memory Manager"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


def reset_memory_manager():
    """Сбросить Memory Manager (для тестов)"""
    global _memory_manager
    _memory_manager = MemoryManager()
