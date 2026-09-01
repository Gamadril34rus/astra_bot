"""
ASTRA BOT - Liquidation Cascade Engine

Движок анализа каскадных ликвидаций
Приоритетное направление #3

Исследует последовательность:
OI → price displacement → liquidation spike → volume → recovery/continuation

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


class LiquidationDirection(str, Enum):
    """Направление ликвидаций"""
    LONG_LIQUIDATION = "long_liquidation"    # Ликвидация лонгов
    SHORT_LIQUIDATION = "short_liquidation"  # Ликвидация шортов
    BOTH = "both"                           # Оба типа
    NEUTRAL = "neutral"                    # Нейтрально


class CascadePhase(str, Enum):
    """Фазы каскада"""
    INITIATION = "initiation"          # Инициация
    ACCELERATION = "acceleration"       # Ускорение
    PEAK = "peak"                       # Пик
    DECELERATION = "deceleration"      # Замедление
    RECOVERY = "recovery"              # Восстановление
    CONTINUATION = "continuation"      # Продолжение


class CascadeType(str, Enum):
    """Типы каскадов"""
    BULLISH_CASCADE = "bullish_cascade"    # Бычий каскад
    BEARISH_CASCADE = "bearish_cascade"    # Медвежий каскад
    NEUTRAL_CASCADE = "neutral_cascade"    # Нейтральный
    FALSE_BREAKOUT = "false_breakout"      # Ложный пробой
    TRUE_BREAKOUT = "true_breakout"        # Истинный пробой


@dataclass
class LiquidationEvent:
    """Событие ликвидации"""
    timestamp: datetime
    symbol: str
    
    # Параметры
    price: float
    volume: float
    direction: LiquidationDirection = LiquidationDirection.NEUTRAL
    
    # Контекст
    open_interest: float = 0.0  # Открытый интерес
    oi_change: float = 0.0  # Изменение OI
    price_displacement: float = 0.0  # Смещение цены
    
    # Метрики
    liquidation_size: float = 0.0  # Размер ликвидации
    volume_spike: float = 0.0  # Всплеск объёма
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "price": self.price,
            "volume": self.volume,
            "direction": self.direction.value,
            "open_interest": self.open_interest,
            "oi_change": self.oi_change,
            "price_displacement": self.price_displacement,
            "liquidation_size": self.liquidation_size,
            "volume_spike": self.volume_spike,
        }


@dataclass
class CascadeMetrics:
    """Метрики каскада"""
    timestamp: datetime
    symbol: str
    
    # Параметры каскада
    total_liquidation_volume: float = 0.0
    total_liquidation_count: int = 0
    long_liquidations: int = 0
    short_liquidations: int = 0
    
    # Изменение OI
    oi_change: float = 0.0
    oi_initial: float = 0.0
    oi_final: float = 0.0
    
    # Смещение цены
    price_displacement: float = 0.0
    price_initial: float = 0.0
    price_final: float = 0.0
    max_price: float = 0.0
    min_price: float = 0.0
    
    # Объём
    volume_spike: float = 0.0
    avg_volume: float = 0.0
    
    # Время
    duration_seconds: float = 0.0
    
    # Фаза
    current_phase: CascadePhase = CascadePhase.INITIATION
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "total_liquidation_volume": self.total_liquidation_volume,
            "total_liquidation_count": self.total_liquidation_count,
            "long_liquidations": self.long_liquidations,
            "short_liquidations": self.short_liquidations,
            "oi_change": self.oi_change,
            "oi_initial": self.oi_initial,
            "oi_final": self.oi_final,
            "price_displacement": self.price_displacement,
            "price_initial": self.price_initial,
            "price_final": self.price_final,
            "max_price": self.max_price,
            "min_price": self.min_price,
            "volume_spike": self.volume_spike,
            "avg_volume": self.avg_volume,
            "duration_seconds": self.duration_seconds,
            "current_phase": self.current_phase.value,
        }


@dataclass
class LiquidationCascade:
    """Каскад ликвидаций"""
    cascade_id: str
    symbol: str
    
    # Время
    start_time: datetime
    end_time: datetime | None = None
    
    # Тип
    cascade_type: CascadeType = CascadeType.NEUTRAL_CASCADE
    
    # События
    events: list[LiquidationEvent] = field(default_factory=list)
    
    # Метрики
    metrics: CascadeMetrics | None = None
    
    # Фазы
    phases: list[tuple[datetime, CascadePhase]] = field(default_factory=list)
    
    # Уверенность
    confidence: float = 0.0
    
    # Сила каскада
    strength: float = 0.0
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "cascade_id": self.cascade_id,
            "symbol": self.symbol,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "cascade_type": self.cascade_type.value,
            "events_count": len(self.events),
            "confidence": self.confidence,
            "strength": self.strength,
            "recommendations": self.recommendations,
        }
        
        if self.metrics:
            result["metrics"] = self.metrics.to_dict()
        
        result["phases"] = [{"time": p[0].isoformat(), "phase": p[1].value} for p in self.phases]
        
        return result


@dataclass
class CascadeAnalysis:
    """Полный анализ каскадов"""
    symbol: str
    timestamp: datetime
    
    # Активные каскады
    active_cascades: list[LiquidationCascade] = field(default_factory=list)
    
    # Завершённые каскады
    completed_cascades: list[LiquidationCascade] = field(default_factory=list)
    
    # Метрики
    total_liquidations: int = 0
    total_liquidation_volume: float = 0.0
    avg_cascade_duration: float = 0.0
    avg_cascade_strength: float = 0.0
    
    # Сигналы
    signals: list[str] = field(default_factory=list)
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    # Уверенность
    confidence: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "active_cascades": len(self.active_cascades),
            "completed_cascades": len(self.completed_cascades),
            "total_liquidations": self.total_liquidations,
            "total_liquidation_volume": self.total_liquidation_volume,
            "avg_cascade_duration": self.avg_cascade_duration,
            "avg_cascade_strength": self.avg_cascade_strength,
            "signals": self.signals,
            "recommendations": self.recommendations,
            "confidence": self.confidence,
        }


class LiquidationCascadeEngine:
    """
    Движок анализа каскадных ликвидаций.
    
    Отслеживает последовательность: OI → price displacement → liquidation spike → volume → recovery/continuation
    """
    
    def __init__(self):
        # Активные каскады
        self._active_cascades: dict[str, LiquidationCascade] = {}
        
        # Завершённые каскады
        self._completed_cascades: dict[str, LiquidationCascade] = {}
        
        # История событий
        self._events: dict[str, list[LiquidationEvent]] = {}
        
        # История OI
        self._oi_history: dict[str, list[tuple[datetime, float]]] = {}
        
        # История цен
        self._price_history: dict[str, list[tuple[datetime, float, float, float, float]]] = {}
        
        # История объёмов
        self._volume_history: dict[str, list[tuple[datetime, float]]] = {}
        
        # Пороги
        self.thresholds = {
            "cascade_min_events": 3,  # Минимальное количество событий для каскада
            "cascade_min_duration": 5.0,  # Минимальная длительность в секундах
            "liquidation_spike_multiplier": 2.0,  # Multiplier для всплеска ликвидаций
            "volume_spike_multiplier": 3.0,  # Multiplier для всплеска объёма
            "price_displacement_threshold": 0.005,  # 0.5% смещение цены
            "oi_change_threshold": 0.01,  # 1% изменение OI
            "cascade_cooldown": 300.0,  # 5 минут перерыва между каскадами
        }
        
        # Параметры
        self.window_size = 1000
    
    def add_liquidation_event(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        volume: float,
        direction: LiquidationDirection,
        open_interest: float = 0.0,
    ):
        """
        Добавить событие ликвидации.
        
        Args:
            symbol: Символ
            timestamp: Временная метка
            price: Цена
            volume: Объём
            direction: Направление
            open_interest: Открытый интерес
        """
        # Создать событие
        event = LiquidationEvent(
            timestamp=timestamp,
            symbol=symbol,
            price=price,
            volume=volume,
            direction=direction,
            open_interest=open_interest,
        )
        
        # Сохранить
        if symbol not in self._events:
            self._events[symbol] = []
        self._events[symbol].append(event)
        
        if len(self._events[symbol]) > self.window_size:
            self._events[symbol] = self._events[symbol][-self.window_size:]
        
        # Обновить историю OI
        if symbol not in self._oi_history:
            self._oi_history[symbol] = []
        self._oi_history[symbol].append((timestamp, open_interest))
        if len(self._oi_history[symbol]) > self.window_size:
            self._oi_history[symbol] = self._oi_history[symbol][-self.window_size:]
        
        # Проверить, является ли это частью каскада
        self._check_cascade(symbol, event)
    
    def add_price_data(
        self,
        symbol: str,
        timestamp: datetime,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ):
        """
        Добавить данные цены и объёма.
        
        Args:
            symbol: Символ
            timestamp: Временная метка
            open_price: Цена открытия
            high: Максимум
            low: Минимум
            close: Цена закрытия
            volume: Объём
        """
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        self._price_history[symbol].append((timestamp, open_price, high, low, close))
        if len(self._price_history[symbol]) > self.window_size:
            self._price_history[symbol] = self._price_history[symbol][-self.window_size:]
        
        if symbol not in self._volume_history:
            self._volume_history[symbol] = []
        self._volume_history[symbol].append((timestamp, volume))
        if len(self._volume_history[symbol]) > self.window_size:
            self._volume_history[symbol] = self._volume_history[symbol][-self.window_size:]
    
    def _check_cascade(self, symbol: str, event: LiquidationEvent):
        """Проверить, является ли событие частью каскада"""
        # Проверить активные каскады
        for cascade_id, cascade in self._active_cascades.items():
            if cascade.symbol == symbol:
                # Добавить событие в каскад
                cascade.events.append(event)
                
                # Обновить метрики
                self._update_cascade_metrics(cascade)
                
                # Проверить завершение каскада
                if self._is_cascade_complete(cascade):
                    self._complete_cascade(cascade_id)
                
                return
        
        # Проверить, нужно ли создать новый каскад
        if self._should_create_cascade(symbol, event):
            self._create_cascade(symbol, event)
    
    def _should_create_cascade(self, symbol: str, event: LiquidationEvent) -> bool:
        """Определить, нужно ли создавать новый каскад"""
        if symbol not in self._events:
            return False
        
        # Проверить, не было ли недавнего каскада
        if symbol in self._active_cascades or symbol in self._completed_cascades:
            active_times = [c.start_time for c in self._active_cascades.values() if c.symbol == symbol]
            completed_times = [c.start_time for c in self._completed_cascades.values() if c.symbol == symbol]
            all_times = active_times + completed_times
            if all_times:
                last_cascade_time = max(all_times)
                if (datetime.now(timezone.utc) - last_cascade_time).total_seconds() < self.thresholds["cascade_cooldown"]:
                    return False
        
        # Проверить, есть ли достаточно событий для каскада
        recent_events = [e for e in self._events[symbol] 
                        if (event.timestamp - e.timestamp).total_seconds() <= 60]
        
        if len(recent_events) >= self.thresholds["cascade_min_events"]:
            return True
        
        return False
    
    def _create_cascade(self, symbol: str, initial_event: LiquidationEvent):
        """Создать новый каскад"""
        cascade_id = f"{symbol}_{datetime.now(timezone.utc).isoformat()}"
        
        # Собрать последние события
        recent_events = [e for e in self._events[symbol] 
                        if (initial_event.timestamp - e.timestamp).total_seconds() <= 60]
        
        cascade = LiquidationCascade(
            cascade_id=cascade_id,
            symbol=symbol,
            start_time=initial_event.timestamp,
            events=recent_events,
            cascade_type=CascadeType.NEUTRAL_CASCADE,
        )
        
        # Добавить начальное событие
        cascade.events.append(initial_event)
        
        # Обновить метрики
        self._update_cascade_metrics(cascade)
        
        # Определить тип каскада
        self._determine_cascade_type(cascade)
        
        # Сохранить
        self._active_cascades[cascade_id] = cascade
        
        # Добавить начальную фазу
        cascade.phases.append((initial_event.timestamp, CascadePhase.INITIATION))
    
    def _update_cascade_metrics(self, cascade: LiquidationCascade):
        """Обновить метрики каскада"""
        if not cascade.events:
            return
        
        # Рассчитать метрики
        total_volume = sum(e.volume for e in cascade.events)
        total_count = len(cascade.events)
        long_count = sum(1 for e in cascade.events if e.direction == LiquidationDirection.LONG_LIQUIDATION)
        short_count = sum(1 for e in cascade.events if e.direction == LiquidationDirection.SHORT_LIQUIDATION)
        
        # OI изменения
        if cascade.events[0].open_interest > 0:
            oi_initial = cascade.events[0].open_interest
            oi_final = cascade.events[-1].open_interest
            oi_change = oi_final - oi_initial
        else:
            oi_initial = 0.0
            oi_final = 0.0
            oi_change = 0.0
        
        # Смещение цены
        price_initial = cascade.events[0].price
        price_final = cascade.events[-1].price
        price_displacement = price_final - price_initial
        
        # Максимум/минимум
        max_price = max(e.price for e in cascade.events)
        min_price = min(e.price for e in cascade.events)
        
        # Объём
        if symbol := cascade.symbol:
            if symbol in self._volume_history and self._volume_history[symbol]:
                volumes = [v[1] for v in self._volume_history[symbol] 
                          if cascade.start_time <= v[0] <= (cascade.end_time or datetime.now(timezone.utc))]
                avg_volume = sum(volumes) / len(volumes) if volumes else 0.0
                current_volume = volumes[-1] if volumes else 0.0
                volume_spike = current_volume / avg_volume if avg_volume > 0 else 0.0
            else:
                avg_volume = 0.0
                volume_spike = 0.0
        else:
            avg_volume = 0.0
            volume_spike = 0.0
        
        # Длительность
        duration = (cascade.events[-1].timestamp - cascade.events[0].timestamp).total_seconds()
        
        cascade.metrics = CascadeMetrics(
            timestamp=datetime.now(timezone.utc),
            symbol=cascade.symbol,
            total_liquidation_volume=total_volume,
            total_liquidation_count=total_count,
            long_liquidations=long_count,
            short_liquidations=short_count,
            oi_change=oi_change,
            oi_initial=oi_initial,
            oi_final=oi_final,
            price_displacement=price_displacement,
            price_initial=price_initial,
            price_final=price_final,
            max_price=max_price,
            min_price=min_price,
            volume_spike=volume_spike,
            avg_volume=avg_volume,
            duration_seconds=duration,
        )
        
        # Определить фазу
        self._update_cascade_phase(cascade)
    
    def _update_cascade_phase(self, cascade: LiquidationCascade):
        """Обновить фазу каскада"""
        if not cascade.metrics:
            return
        
        # Определить текущую фазу
        duration = cascade.metrics.duration_seconds
        events_count = cascade.metrics.total_liquidation_count
        
        if duration < 10:  # Первые 10 секунд
            current_phase = CascadePhase.INITIATION
        elif events_count < self.thresholds["cascade_min_events"]:
            current_phase = CascadePhase.INITIATION
        elif duration < 30 and events_count >= self.thresholds["cascade_min_events"]:
            current_phase = CascadePhase.ACCELERATION
        elif duration >= 30 and duration < 60:
            current_phase = CascadePhase.PEAK
        elif duration >= 60:
            # Проверить, уменьшается ли активность
            if events_count > 10:
                recent_events = cascade.events[-5:]
                recent_duration = (recent_events[-1].timestamp - recent_events[0].timestamp).total_seconds()
                if recent_duration > duration / events_count * 5:
                    current_phase = CascadePhase.DECELERATION
                else:
                    current_phase = CascadePhase.PEAK
            else:
                current_phase = CascadePhase.PEAK
        else:
            current_phase = CascadePhase.INITIATION
        
        # Добавить фазу, если изменилась
        if not cascade.phases or cascade.phases[-1][1] != current_phase:
            cascade.phases.append((datetime.now(timezone.utc), current_phase))
        
        cascade.metrics.current_phase = current_phase
    
    def _determine_cascade_type(self, cascade: LiquidationCascade):
        """Определить тип каскада"""
        if not cascade.metrics:
            return
        
        # Определить по направлению ликвидаций
        long_ratio = cascade.metrics.long_liquidations / max(cascade.metrics.total_liquidation_count, 1)
        short_ratio = cascade.metrics.short_liquidations / max(cascade.metrics.total_liquidation_count, 1)
        
        if long_ratio > short_ratio * 2:
            cascade.cascade_type = CascadeType.BEARISH_CASCADE  # Ликвидация лонгов → цена падает
        elif short_ratio > long_ratio * 2:
            cascade.cascade_type = CascadeType.BULLISH_CASCADE  # Ликвидация шортов → цена растёт
        else:
            cascade.cascade_type = CascadeType.NEUTRAL_CASCADE
        
        # Уточнить по смещению цены
        if cascade.metrics.price_displacement > 0 and cascade.cascade_type == CascadeType.BEARISH_CASCADE:
            cascade.cascade_type = CascadeType.FALSE_BREAKOUT
        elif cascade.metrics.price_displacement < 0 and cascade.cascade_type == CascadeType.BULLISH_CASCADE:
            cascade.cascade_type = CascadeType.FALSE_BREAKOUT
    
    def _is_cascade_complete(self, cascade: LiquidationCascade) -> bool:
        """Проверить, завершён ли каскад"""
        if not cascade.metrics:
            return False
        
        # Каскад завершён, если:
        # 1. Прошло достаточно времени
        duration = cascade.metrics.duration_seconds
        if duration > 120:  # 2 минуты
            return True
        
        # 2. Активность упала
        if len(cascade.events) >= 10:
            recent_events = cascade.events[-3:]
            time_since_last = (datetime.now(timezone.utc) - recent_events[-1].timestamp).total_seconds()
            if time_since_last > 30:  # 30 секунд без новых событий
                return True
        
        return False
    
    def _complete_cascade(self, cascade_id: str):
        """Завершить каскад"""
        if cascade_id not in self._active_cascades:
            return
        
        cascade = self._active_cascades.pop(cascade_id)
        cascade.end_time = datetime.now(timezone.utc)
        
        # Обновить метрики
        self._update_cascade_metrics(cascade)
        
        # Определить финальную фазу
        if cascade.metrics:
            if cascade.metrics.price_displacement > 0:
                cascade.phases.append((cascade.end_time, CascadePhase.CONTINUATION))
            elif cascade.metrics.price_displacement < 0:
                cascade.phases.append((cascade.end_time, CascadePhase.CONTINUATION))
            else:
                cascade.phases.append((cascade.end_time, CascadePhase.RECOVERY))
        
        # Сохранить в завершённые
        self._completed_cascades[cascade_id] = cascade
    
    def analyze_cascades(
        self,
        symbol: str,
        timestamp: datetime,
        timeframe: str = "1m",
    ) -> CascadeAnalysis:
        """
        Полный анализ каскадов ликвидаций.
        
        Args:
            symbol: Символ
            timestamp: Временная метка
            timeframe: Временной горизонт
        
        Returns:
            Полный анализ
        """
        # Получить активные каскады
        active = [c for c in self._active_cascades.values() if c.symbol == symbol]
        
        # Получить завершённые каскады
        completed = [c for c in self._completed_cascades.values() if c.symbol == symbol]
        
        # Рассчитать метрики
        total_liquidations = sum(c.metrics.total_liquidation_count for c in active + completed) if active or completed else 0
        total_volume = sum(c.metrics.total_liquidation_volume for c in active + completed) if active or completed else 0.0
        
        if active + completed:
            avg_duration = sum(c.metrics.duration_seconds for c in active + completed) / len(active + completed)
            avg_strength = sum(abs(c.metrics.price_displacement) for c in active + completed) / len(active + completed)
        else:
            avg_duration = 0.0
            avg_strength = 0.0
        
        # Сгенерировать сигналы
        signals = self._generate_signals(active, completed)
        
        # Сгенерировать рекомендации
        recommendations = self._generate_recommendations(active, completed)
        
        # Рассчитать уверенность
        confidence = self._calculate_confidence(active, completed)
        
        return CascadeAnalysis(
            symbol=symbol,
            timestamp=timestamp,
            active_cascades=active,
            completed_cascades=completed,
            total_liquidations=total_liquidations,
            total_liquidation_volume=total_volume,
            avg_cascade_duration=avg_duration,
            avg_cascade_strength=avg_strength,
            signals=signals,
            recommendations=recommendations,
            confidence=confidence,
        )
    
    def _generate_signals(
        self,
        active: list[LiquidationCascade],
        completed: list[LiquidationCascade],
    ) -> list[str]:
        """Сгенерировать сигналы"""
        signals = []
        
        # Сигналы по активным каскадам
        for cascade in active:
            if cascade.cascade_type == CascadeType.BULLISH_CASCADE:
                signals.append("BULLISH_CASCADE_ACTIVE")
            elif cascade.cascade_type == CascadeType.BEARISH_CASCADE:
                signals.append("BEARISH_CASCADE_ACTIVE")
            elif cascade.cascade_type == CascadeType.FALSE_BREAKOUT:
                signals.append("FALSE_BREAKOUT_DETECTED")
            elif cascade.cascade_type == CascadeType.TRUE_BREAKOUT:
                signals.append("TRUE_BREAKOUT_DETECTED")
        
        # Сигналы по завершённым каскадам
        for cascade in completed:
            if cascade.metrics and cascade.metrics.duration_seconds > 60:
                signals.append("LONG_CASCADE_COMPLETED")
        
        if not signals:
            signals.append("NO_ACTIVE_CASCADES")
        
        return signals
    
    def _generate_recommendations(
        self,
        active: list[LiquidationCascade],
        completed: list[LiquidationCascade],
    ) -> list[str]:
        """Сгенерировать рекомендации"""
        recommendations = []
        
        # Рекомендации по активным каскадам
        for cascade in active:
            if cascade.cascade_type == CascadeType.BULLISH_CASCADE:
                recommendations.append(f"Bullish cascade in {cascade.symbol}: {cascade.metrics.total_liquidation_count} liquidations, price +{cascade.metrics.price_displacement:.2f}")
            elif cascade.cascade_type == CascadeType.BEARISH_CASCADE:
                recommendations.append(f"Bearish cascade in {cascade.symbol}: {cascade.metrics.total_liquidation_count} liquidations, price {cascade.metrics.price_displacement:.2f}")
            elif cascade.cascade_type == CascadeType.FALSE_BREAKOUT:
                recommendations.append(f"False breakout detected in {cascade.symbol}")
        
        # Рекомендации по завершённым каскадам
        for cascade in completed:
            if cascade.metrics and cascade.metrics.price_displacement > 0:
                recommendations.append(f"Bullish cascade completed in {cascade.symbol} with {cascade.metrics.price_displacement:.2f} displacement")
            elif cascade.metrics and cascade.metrics.price_displacement < 0:
                recommendations.append(f"Bearish cascade completed in {cascade.symbol} with {cascade.metrics.price_displacement:.2f} displacement")
        
        if not recommendations:
            recommendations.append("No significant liquidation cascades detected")
        
        return recommendations
    
    def _calculate_confidence(
        self,
        active: list[LiquidationCascade],
        completed: list[LiquidationCascade],
    ) -> float:
        """Рассчитать уверенность"""
        confidence = 0.5
        
        # Учесть количество активных каскадов
        if active:
            confidence += 0.1 * min(1, len(active))
        
        # Учесть количество завершённых каскадов
        if completed:
            confidence += 0.05 * min(1, len(completed))
        
        # Учесть силу каскадов
        for cascade in active + completed:
            if cascade.metrics and cascade.metrics.total_liquidation_count > 5:
                confidence += 0.05
        
        return min(1.0, confidence)


# Глобальный экземпляр
_liquidation_cascade_engine: LiquidationCascadeEngine | None = None


def get_liquidation_cascade_engine() -> LiquidationCascadeEngine:
    """Получить глобальный Liquidation Cascade Engine"""
    global _liquidation_cascade_engine
    if _liquidation_cascade_engine is None:
        _liquidation_cascade_engine = LiquidationCascadeEngine()
    return _liquidation_cascade_engine


def reset_liquidation_cascade_engine():
    """Сбросить Liquidation Cascade Engine (для тестов)"""
    global _liquidation_cascade_engine
    _liquidation_cascade_engine = LiquidationCascadeEngine()
