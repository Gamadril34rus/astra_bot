"""
ASTRA BOT — Event-driven архитектура
"""

import asyncio
import json
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Типы событий системы"""
    
    # Рыночные данные
    CANDLE_UPDATE = "CANDLE_UPDATE"
    ORDERBOOK_UPDATE = "ORDERBOOK_UPDATE"
    TRADE_UPDATE = "TRADE_UPDATE"
    TICKER_UPDATE = "TICKER_UPDATE"
    TRUNCATION = "TRUNCATION"
    
    # Режим рынка
    REGIME_DETECTED = "REGIME_DETECTED"
    REGIME_CHANGE = "REGIME_CHANGE"
    
    # Сигналы
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_SCORED = "SIGNAL_SCORED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    SIGNAL_APPROVED = "SIGNAL_APPROVED"
    
    # Ордеры
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELED = "ORDER_CANCELED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    
    # Риск
    RISK_CHECK_PASSED = "RISK_CHECK_PASSED"
    RISK_CHECK_FAILED = "RISK_CHECK_FAILED"
    RISK_LIMIT_HIT = "RISK_LIMIT_HIT"
    DRAWDOWN_THRESHOLD = "DRAWDOWN_THRESHOLD"
    TRADING_PAUSED = "TRADING_PAUSED"
    TRADING_RESUMED = "TRADING_RESUMED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    
    # Новости
    NEWS_EVENT = "NEWS_EVENT"
    NEWS_DECAY = "NEWS_DECAY"
    NEWS_ANALYSIS_COMPLETE = "NEWS_ANALYSIS_COMPLETE"
    
    # On-chain
    ONCHAIN_EVENT = "ONCHAIN_EVENT"
    ONCHAIN_SCORE_UPDATED = "ONCHAIN_SCORE_UPDATED"
    
    # Ликвидность
    LIQUIDITY_CHECK = "LIQUIDITY_CHECK"
    LIQUIDITY_LOW = "LIQUIDITY_LOW"
    LIQUIDITY_CRITICAL = "LIQUIDITY_CRITICAL"
    
    # ML
    ML_PREDICTION = "ML_PREDICTION"
    ML_MODEL_TRAINED = "ML_MODEL_TRAINED"
    ML_MODEL_DEPLOYED = "ML_MODEL_DEPLOYED"
    ML_DRIFT_DETECTED = "ML_DRIFT_DETECTED"
    
    # Стратегии
    STRATEGY_KILLED = "STRATEGY_KILLED"
    STRATEGY_DECAY_DETECTED = "STRATEGY_DECAY_DETECTED"
    STRATEGY_PERFORMANCE_UPDATE = "STRATEGY_PERFORMANCE_UPDATE"
    
    # Биржа
    EXCHANGE_HEALTH_CHANGE = "EXCHANGE_HEALTH_CHANGE"
    EXCHANGE_DISCONNECTED = "EXCHANGE_DISCONNECTED"
    EXCHANGE_RECONNECTED = "EXCHANGE_RECONNECTED"
    
    # Стейт
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"
    BALANCE_UPDATE = "BALANCE_UPDATE"
    POSITION_UPDATE = "POSITION_UPDATE"
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_COMPLETE = "RECONCILIATION_COMPLETE"
    RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
    
    # Системные
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    CIRCUIT_BREAKER_TRIGGERED = "CIRCUIT_BREAKER_TRIGGERED"
    RECOVERY_MODE_ENTERED = "RECOVERY_MODE_ENTERED"
    RECOVERY_MODE_EXITED = "RECOVERY_MODE_EXITED"
    CAPITAL_PRESERVATION_MODE = "CAPITAL_PRESERVATION_MODE"
    
    # Отчёты
    DAILY_REPORT_READY = "DAILY_REPORT_READY"
    ALERT_SENT = "ALERT_SENT"
    
    # ML модель
    MODEL_VERSION_REGISTERED = "MODEL_VERSION_REGISTERED"


@dataclass
class Event:
    """Базовое событие"""
    type: EventType
    data: Any = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: Optional[str] = None
    correlation_id: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "correlation_id": self.correlation_id,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            type=EventType(data["type"]),
            data=data.get("data"),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=data.get("source"),
            correlation_id=data.get("correlation_id"),
        )


class EventBus:
    """
    Event Bus для коммуникации между модулями.
    Реализует паттерн Pub/Sub.
    """
    
    def __init__(self):
        self._handlers: Dict[EventType, List[Callable]] = {}
        self._async_handlers: Dict[EventType, List[Callable]] = {}
        self._lock = asyncio.Lock()
    
    def subscribe(
        self,
        event_type: EventType,
        handler: Callable,
        async_handler: bool = False
    ) -> None:
        """Подписаться на событие"""
        if async_handler:
            if event_type not in self._async_handlers:
                self._async_handlers[event_type] = []
            self._async_handlers[event_type].append(handler)
        else:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
        
        logger.debug(f"Subscribed {handler.__name__ if hasattr(handler, '__name__') else handler} "
                    f"to {event_type.value}")
    
    def unsubscribe(
        self,
        event_type: EventType,
        handler: Callable,
        async_handler: bool = False
    ) -> None:
        """Отписаться от события"""
        if async_handler:
            handlers = self._async_handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
        else:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
    
    def publish(self, event_type: EventType, data: Any = None, 
                source: Optional[str] = None,
                correlation_id: Optional[str] = None) -> Event:
        """
        Опубликовать синхронное событие.
        Возвращает созданное событие.
        """
        event = Event(
            type=event_type,
            data=data,
            source=source,
            correlation_id=correlation_id,
        )
        
        # Вызов синхронных обработчиков
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Sync handler error for {event_type.value}: {e}", 
                           exc_info=True)
        
        return event
    
    async def publish_async(
        self,
        event_type: EventType,
        data: Any = None,
        source: Optional[str] = None,
        correlation_id: Optional[str] = None
    ) -> Event:
        """
        Опубликовать асинхронное событие.
        Возвращает созданное событие.
        """
        event = Event(
            type=event_type,
            data=data,
            source=source,
            correlation_id=correlation_id,
        )
        
        # Вызов асинхронных обработчиков
        handlers = self._async_handlers.get(event_type, [])
        tasks = []
        for handler in handlers:
            try:
                tasks.append(handler(event))
            except Exception as e:
                logger.error(f"Error preparing async handler for {event_type.value}: {e}",
                           exc_info=True)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        return event
    
    async def broadcast(
        self,
        events: List[tuple[EventType, Any]],
        source: Optional[str] = None
    ) -> List[Event]:
        """Отправить несколько событий"""
        results = []
        for event_type, data in events:
            event = await self.publish_async(
                event_type, data, source
            )
            results.append(event)
        return results
    
    def get_handler_count(self, event_type: EventType) -> int:
        """Получить количество обработчиков для события"""
        sync_count = len(self._handlers.get(event_type, []))
        async_count = len(self._async_handlers.get(event_type, []))
        return sync_count + async_count


# Глобальный EventBus
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Получить глобальный EventBus"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def reset_event_bus() -> None:
    """Сбросить EventBus (для тестов)"""
    global _event_bus
    _event_bus = None


# Вспомогательные функции для быстрого использования
def emit(event_type: EventType, data: Any = None, source: Optional[str] = None):
    """Быстрая публикация события"""
    get_event_bus().publish(event_type, data, source)


async def emit_async(event_type: EventType, data: Any = None, source: Optional[str] = None):
    """Быстрая асинхронная публикация события"""
    await get_event_bus().publish_async(event_type, data, source)
