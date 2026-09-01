"""
ASTRA BOT - Event Response Engine

Система изучения причинно-временных связей (ТЗ Пункт 4)

Для каждого события исследует реакцию цены:
T-30m, T-15m, T-5m, T0, T+1m, T+3m, T+5m, T+15m, T+30m, T+1h, T+4h, T+24h

Исследует:
- return
- volatility
- volume
- ATR
- trend
- drawdown
- reversal probability
- continuation probability

Категории событий:
- macroeconomic
- CPI
- PPI
- FOMC
- rates
- employment
- GDP
- inflation
- ETF flows
- crypto-specific events
- exchange events
- liquidations
- large transfers
- funding changes
- open interest changes
- regulatory news
- major announcements
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

import numpy as np

from .. import models, utils

logger = logging.getLogger(__name__)


class EventCategory(str, Enum):
    """Категории событий"""
    MACROECONOMIC = "macroeconomic"
    CPI = "CPI"
    PPI = "PPI"
    FOMC = "FOMC"
    RATES = "rates"
    EMPLOYMENT = "employment"
    GDP = "GDP"
    INFLATION = "inflation"
    ETF_FLOWS = "ETF_flows"
    CRYPTO_SPECIFIC = "crypto_specific"
    EXCHANGE_EVENTS = "exchange_events"
    LIQUIDATIONS = "liquidations"
    LARGE_TRANSFERS = "large_transfers"
    FUNDING_CHANGES = "funding_changes"
    OPEN_INTEREST_CHANGES = "open_interest_changes"
    REGULATORY_NEWS = "regulatory_news"
    MAJOR_ANNOUNCEMENTS = "major_announcements"


class EventImpact(str, Enum):
    """Влияние события"""
    STRONG_POSITIVE = "strong_positive"
    MODERATE_POSITIVE = "moderate_positive"
    WEAK_POSITIVE = "weak_positive"
    NEUTRAL = "neutral"
    WEAK_NEGATIVE = "weak_negative"
    MODERATE_NEGATIVE = "moderate_negative"
    STRONG_NEGATIVE = "strong_negative"


class EventDirection(str, Enum):
    """Направление события"""
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


@dataclass
class PriceResponse:
    """Реакция цены на событие"""
    offset_minutes: int  # Время относительно события (отрицательное = до, положительное = после)
    return_pct: float  # Возврат в %
    volatility: float  # Волатильность
    volume: float  # Объём
    atr: float  # ATR
    trend: float  # Тренд (-1 до 1)
    drawdown: float  # Просадка
    reversal_probability: float  # Вероятность разворота
    continuation_probability: float  # Вероятность продолжения
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "offset_minutes": self.offset_minutes,
            "return_pct": self.return_pct,
            "volatility": self.volatility,
            "volume": self.volume,
            "atr": self.atr,
            "trend": self.trend,
            "drawdown": self.drawdown,
            "reversal_probability": self.reversal_probability,
            "continuation_probability": self.continuation_probability,
        }


@dataclass
class EventAnalysis:
    """Анализ события"""
    event_id: str
    event_name: str
    event_category: EventCategory
    event_timestamp: datetime
    symbol: str
    
    # Ожидания
    expected_direction: EventDirection = EventDirection.NEUTRAL
    expected_impact: EventImpact = EventImpact.NEUTRAL
    expected_horizon: str = "1h"  # Временной горизонт
    
    # Реакция цены
    price_responses: list[PriceResponse] = field(default_factory=list)
    
    # Статистика
    max_return_pct: float = 0.0
    min_return_pct: float = 0.0
    avg_return_pct: float = 0.0
    max_volatility: float = 0.0
    avg_volatility: float = 0.0
    max_volume: float = 0.0
    avg_volume: float = 0.0
    
    # Уверенность
    confidence: float = 0.0
    sample_size: int = 0
    
    # ОOS результат
    oos_result: dict[str, Any] = field(default_factory=dict)
    
    # Ограничения
    limitations: list[str] = field(default_factory=list)
    
    # Временная метка анализа
    analysis_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "event_category": self.event_category.value,
            "event_timestamp": self.event_timestamp.isoformat(),
            "symbol": self.symbol,
            "expected_direction": self.expected_direction.value,
            "expected_impact": self.expected_impact.value,
            "expected_horizon": self.expected_horizon,
            "price_responses": [r.to_dict() for r in self.price_responses],
            "max_return_pct": self.max_return_pct,
            "min_return_pct": self.min_return_pct,
            "avg_return_pct": self.avg_return_pct,
            "max_volatility": self.max_volatility,
            "avg_volatility": self.avg_volatility,
            "max_volume": self.max_volume,
            "avg_volume": self.avg_volume,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "oos_result": self.oos_result,
            "limitations": self.limitations,
            "analysis_timestamp": self.analysis_timestamp.isoformat(),
        }


@dataclass
class EventLesson:
    """Урок из события (ТЗ Пункт 4)
    
    EVENT → MARKET STATE → PRICE RESPONSE → CONFIDENCE → SAMPLE SIZE → OOS RESULT → LIMITATIONS
    """
    event_id: str
    event_category: EventCategory
    market_state: str
    price_response: str
    confidence: float
    sample_size: int
    oos_result: dict[str, Any]
    limitations: list[str]
    
    # Временная метка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_category": self.event_category.value,
            "market_state": self.market_state,
            "price_response": self.price_response,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "oos_result": self.oos_result,
            "limitations": self.limitations,
            "timestamp": self.timestamp.isoformat(),
        }


class EventResponseEngine:
    """
    Движок изучения причинно-временных связей.
    
    Для каждого события исследует реакцию цены в разные временные интервалы.
    """
    
    def __init__(self):
        # История событий
        self._events: dict[str, EventAnalysis] = {}
        
        # Уроки
        self._lessons: dict[str, EventLesson] = {}
        
        # Временные интервалы для анализа
        self.time_offsets = [-30, -15, -5, 0, 1, 3, 5, 15, 30, 60, 240, 1440]  # минуты
        
        # Пороги
        self.thresholds = {
            "strong_impact_return": 2.0,  # 2% возврат
            "moderate_impact_return": 1.0,  # 1% возврат
            "weak_impact_return": 0.5,  # 0.5% возврат
            "high_volatility": 0.05,  # 5% волатильность
            "high_volume": 2.0,  # 2x средний объём
            "min_sample_size": 5,  # Минимальный размер выборки
        }
    
    def analyze_event(
        self,
        event_id: str,
        event_name: str,
        event_category: EventCategory,
        event_timestamp: datetime,
        symbol: str,
        candles: list[models.Candle],
        expected_direction: EventDirection = EventDirection.NEUTRAL,
        expected_impact: EventImpact = EventImpact.NEUTRAL,
        expected_horizon: str = "1h",
    ) -> EventAnalysis:
        """
        Проанализировать событие.
        
        Args:
            event_id: ID события
            event_name: Название события
            event_category: Категория события
            event_timestamp: Время события
            symbol: Символ
            candles: Список свечей
            expected_direction: Ожидаемое направление
            expected_impact: Ожидаемое влияние
            expected_horizon: Ожидаемый горизонт
        
        Returns:
            Анализ события
        """
        if not candles:
            return EventAnalysis(
                event_id=event_id,
                event_name=event_name,
                event_category=event_category,
                event_timestamp=event_timestamp,
                symbol=symbol,
                expected_direction=expected_direction,
                expected_impact=expected_impact,
                expected_horizon=expected_horizon,
                limitations=["No candle data available"],
            )
        
        # Найти индекс свечи, соответствующей времени события
        event_index = self._find_event_index(candles, event_timestamp)
        
        if event_index is None:
            return EventAnalysis(
                event_id=event_id,
                event_name=event_name,
                event_category=event_category,
                event_timestamp=event_timestamp,
                symbol=symbol,
                expected_direction=expected_direction,
                expected_impact=expected_impact,
                expected_horizon=expected_horizon,
                limitations=["Event timestamp not found in candle data"],
            )
        
        # Рассчитать реакцию цены для каждого временного интервала
        price_responses = []
        for offset_minutes in self.time_offsets:
            response = self._calculate_price_response(
                candles, event_index, offset_minutes
            )
            price_responses.append(response)
        
        # Рассчитать статистику
        returns = [r.return_pct for r in price_responses]
        volatilities = [r.volatility for r in price_responses]
        volumes = [r.volume for r in price_responses]
        
        max_return = max(returns) if returns else 0.0
        min_return = min(returns) if returns else 0.0
        avg_return = np.mean(returns) if returns else 0.0
        max_volatility = max(volatilities) if volatilities else 0.0
        avg_volatility = np.mean(volatilities) if volatilities else 0.0
        max_volume = max(volumes) if volumes else 0.0
        avg_volume = np.mean(volumes) if volumes else 0.0
        
        # Определить уверенность
        confidence = self._calculate_confidence(price_responses, expected_direction)
        
        # Создать анализ
        analysis = EventAnalysis(
            event_id=event_id,
            event_name=event_name,
            event_category=event_category,
            event_timestamp=event_timestamp,
            symbol=symbol,
            expected_direction=expected_direction,
            expected_impact=expected_impact,
            expected_horizon=expected_horizon,
            price_responses=price_responses,
            max_return_pct=max_return,
            min_return_pct=min_return,
            avg_return_pct=avg_return,
            max_volatility=max_volatility,
            avg_volatility=avg_volatility,
            max_volume=max_volume,
            avg_volume=avg_volume,
            confidence=confidence,
            sample_size=len(price_responses),
            limitations=self._identify_limitations(price_responses, expected_direction),
        )
        
        # Сохранить анализ
        self._events[event_id] = analysis
        
        # Создать урок
        lesson = self._create_lesson(analysis)
        self._lessons[event_id] = lesson
        
        return analysis
    
    def _find_event_index(
        self,
        candles: list[models.Candle],
        event_timestamp: datetime,
    ) -> int | None:
        """
        Найти индекс свечи, соответствующей времени события.
        
        Args:
            candles: Список свечей
            event_timestamp: Время события
        
        Returns:
            Индекс свечи или None
        """
        for i, candle in enumerate(candles):
            if hasattr(candle, 'timestamp') and candle.timestamp:
                # Проверка с точностью до минуты
                if (candle.timestamp - event_timestamp).total_seconds() < 60:
                    return i
        return None
    
    def _calculate_price_response(
        self,
        candles: list[models.Candle],
        event_index: int,
        offset_minutes: int,
    ) -> PriceResponse:
        """
        Рассчитать реакцию цены для заданного смещения.
        
        Args:
            candles: Список свечей
            event_index: Индекс события
            offset_minutes: Смещение в минутах
        
        Returns:
            Реакция цены
        """
        # Определить индекс для анализа
        target_index = event_index + offset_minutes
        
        # Проверить границы
        if target_index < 0 or target_index >= len(candles):
            return PriceResponse(
                offset_minutes=offset_minutes,
                return_pct=0.0,
                volatility=0.0,
                volume=0.0,
                atr=0.0,
                trend=0.0,
                drawdown=0.0,
                reversal_probability=0.0,
                continuation_probability=0.0,
            )
        
        # Получение цен
        event_close = float(candles[event_index].close)
        target_close = float(candles[target_index].close)
        
        # Рассчитать возврат
        return_pct = ((target_close - event_close) / event_close * 100) if event_close > 0 else 0.0
        
        # Рассчитать волатильность (стандартное отклонение возвратов)
        if offset_minutes > 0:
            # Для положительных смещений - волатильность между событием и целевым временем
            closes = [float(c.close) for c in candles[event_index:target_index+1]]
            if len(closes) >= 2:
                returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
                volatility = float(np.std(returns)) * 100 if returns else 0.0
            else:
                volatility = 0.0
        else:
            # Для отрицательных смещений - волатильность до события
            closes = [float(c.close) for c in candles[target_index:event_index+1]]
            if len(closes) >= 2:
                returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
                volatility = float(np.std(returns)) * 100 if returns else 0.0
            else:
                volatility = 0.0
        
        # Рассчитать объём
        if offset_minutes > 0:
            volumes = [float(c.volume) for c in candles[event_index:target_index+1] if hasattr(c, 'volume') and c.volume is not None]
        else:
            volumes = [float(c.volume) for c in candles[target_index:event_index+1] if hasattr(c, 'volume') and c.volume is not None]
        volume = sum(volumes) if volumes else 0.0
        
        # Рассчитать ATR
        if offset_minutes > 0:
            highs = [float(c.high) for c in candles[event_index:target_index+1]]
            lows = [float(c.low) for c in candles[event_index:target_index+1]]
            closes_atr = [float(c.close) for c in candles[event_index:target_index+1]]
            atr = float(utils.calculate_atr(highs, lows, closes_atr, period=min(14, len(highs)))) if len(highs) >= 2 else 0.0
        else:
            highs = [float(c.high) for c in candles[target_index:event_index+1]]
            lows = [float(c.low) for c in candles[target_index:event_index+1]]
            closes_atr = [float(c.close) for c in candles[target_index:event_index+1]]
            atr = float(utils.calculate_atr(highs, lows, closes_atr, period=min(14, len(highs)))) if len(highs) >= 2 else 0.0
        
        # Рассчитать тренд
        if len(closes_atr) >= 2:
            trend = (closes_atr[-1] - closes_atr[0]) / abs(closes_atr[0]) if closes_atr[0] != 0 else 0.0
        else:
            trend = 0.0
        
        # Рассчитать просадку
        if offset_minutes > 0:
            max_price = max(float(c.high) for c in candles[event_index:target_index+1])
            min_price = min(float(c.low) for c in candles[event_index:target_index+1])
            drawdown = ((max_price - min_price) / max_price * 100) if max_price > 0 else 0.0
        else:
            drawdown = 0.0
        
        # Рассчитать вероятности разворота и продолжения
        # (Упрощённая логика - нужно доработать)
        if offset_minutes > 0 and len(closes_atr) >= 5:
            # Если цена изменила направление
            if (closes_atr[-1] > closes_atr[-2] and closes_atr[-2] < closes_atr[-3]) or \
               (closes_atr[-1] < closes_atr[-2] and closes_atr[-2] > closes_atr[-3]):
                reversal_probability = 0.7
                continuation_probability = 0.3
            else:
                reversal_probability = 0.3
                continuation_probability = 0.7
        else:
            reversal_probability = 0.5
            continuation_probability = 0.5
        
        return PriceResponse(
            offset_minutes=offset_minutes,
            return_pct=return_pct,
            volatility=volatility,
            volume=volume,
            atr=atr,
            trend=trend,
            drawdown=drawdown,
            reversal_probability=reversal_probability,
            continuation_probability=continuation_probability,
        )
    
    def _calculate_confidence(
        self,
        price_responses: list[PriceResponse],
        expected_direction: EventDirection,
    ) -> float:
        """
        Рассчитать уверенность анализа.
        
        Args:
            price_responses: Реакции цены
            expected_direction: Ожидаемое направление
        
        Returns:
            Уверенность (0-1)
        """
        if not price_responses:
            return 0.0
        
        confidence = 0.5
        
        # Учет согласованности реакций
        positive_responses = sum(1 for r in price_responses if r.return_pct > 0)
        negative_responses = sum(1 for r in price_responses if r.return_pct < 0)
        
        total = len(price_responses)
        if total > 0:
            if expected_direction == EventDirection.POSITIVE:
                confidence += (positive_responses / total) * 0.3
            elif expected_direction == EventDirection.NEGATIVE:
                confidence += (negative_responses / total) * 0.3
        
        # Учет величины реакции
        avg_return = np.mean([r.return_pct for r in price_responses])
        if expected_direction == EventDirection.POSITIVE and avg_return > 0:
            confidence += min(0.2, avg_return / 10)  # 10% возврат = +0.2
        elif expected_direction == EventDirection.NEGATIVE and avg_return < 0:
            confidence += min(0.2, abs(avg_return) / 10)
        
        # Учет волатильности
        avg_volatility = np.mean([r.volatility for r in price_responses])
        if avg_volatility > 0:
            confidence += min(0.1, avg_volatility / 50)  # 50% волатильность = +0.1
        
        # Учет объема
        avg_volume = np.mean([r.volume for r in price_responses])
        if avg_volume > 0:
            # Сравнить с средним объёмом
            confidence += min(0.1, avg_volume / 1000000)  # 1M объём = +0.1
        
        return min(1.0, max(0.0, confidence))
    
    def _identify_limitations(
        self,
        price_responses: list[PriceResponse],
        expected_direction: EventDirection,
    ) -> list[str]:
        """
        Идентифицировать ограничения анализа.
        
        Args:
            price_responses: Реакции цены
            expected_direction: Ожидаемое направление
        
        Returns:
            Список ограничений
        """
        limitations = []
        
        # Проверка размера выборки
        if len(price_responses) < self.thresholds["min_sample_size"]:
            limitations.append("Small sample size")
        
        # Проверка согласованности
        positive_responses = sum(1 for r in price_responses if r.return_pct > 0)
        negative_responses = sum(1 for r in price_responses if r.return_pct < 0)
        
        if positive_responses > 0 and negative_responses > 0:
            limitations.append("Mixed price responses")
        
        # Проверка ожиданий
        avg_return = np.mean([r.return_pct for r in price_responses])
        if expected_direction == EventDirection.POSITIVE and avg_return <= 0:
            limitations.append("Expected positive impact but average return is non-positive")
        elif expected_direction == EventDirection.NEGATIVE and avg_return >= 0:
            limitations.append("Expected negative impact but average return is non-negative")
        
        # Проверка волатильности
        avg_volatility = np.mean([r.volatility for r in price_responses])
        if avg_volatility > self.thresholds["high_volatility"]:
            limitations.append("High volatility during event")
        
        return limitations
    
    def _create_lesson(self, analysis: EventAnalysis) -> EventLesson:
        """
        Создать урок из анализа события.
        
        Args:
            analysis: Анализ события
        
        Returns:
            Урок
        """
        # Определить реакцию цены
        avg_return = analysis.avg_return_pct
        
        if avg_return > self.thresholds["strong_impact_return"]:
            price_response = "Strong positive reaction"
        elif avg_return > self.thresholds["moderate_impact_return"]:
            price_response = "Moderate positive reaction"
        elif avg_return > self.thresholds["weak_impact_return"]:
            price_response = "Weak positive reaction"
        elif avg_return < -self.thresholds["strong_impact_return"]:
            price_response = "Strong negative reaction"
        elif avg_return < -self.thresholds["moderate_impact_return"]:
            price_response = "Moderate negative reaction"
        elif avg_return < -self.thresholds["weak_impact_return"]:
            price_response = "Weak negative reaction"
        else:
            price_response = "Neutral reaction"
        
        # Определить состояние рынка
        max_volatility = analysis.max_volatility
        if max_volatility > self.thresholds["high_volatility"]:
            market_state = "High volatility"
        else:
            market_state = "Normal"
        
        return EventLesson(
            event_id=analysis.event_id,
            event_category=analysis.event_category,
            market_state=market_state,
            price_response=price_response,
            confidence=analysis.confidence,
            sample_size=analysis.sample_size,
            oos_result={},  # Пока пусто - нужно реализовать OOS тестирование
            limitations=analysis.limitations,
        )
    
    def get_event_analysis(self, event_id: str) -> EventAnalysis | None:
        """
        Получить анализ события.
        
        Args:
            event_id: ID события
        
        Returns:
            Анализ события или None
        """
        return self._events.get(event_id)
    
    def get_event_lesson(self, event_id: str) -> EventLesson | None:
        """
        Получить урок из события.
        
        Args:
            event_id: ID события
        
        Returns:
            Урок или None
        """
        return self._lessons.get(event_id)
    
    def search_events(
        self,
        category: EventCategory | None = None,
        symbol: str = "",
        min_confidence: float = 0.0,
        min_sample_size: int = 0,
    ) -> list[EventAnalysis]:
        """
        Поиск событий.
        
        Args:
            category: Категория
            symbol: Символ
            min_confidence: Минимальная уверенность
            min_sample_size: Минимальный размер выборки
        
        Returns:
            Список анализов событий
        """
        results = []
        for analysis in self._events.values():
            if category and analysis.event_category != category:
                continue
            if symbol and analysis.symbol != symbol:
                continue
            if analysis.confidence < min_confidence:
                continue
            if analysis.sample_size < min_sample_size:
                continue
            results.append(analysis)
        
        return results
    
    def get_event_statistics(self, category: EventCategory | None = None) -> dict[str, Any]:
        """
        Получить статистику по событиям.
        
        Args:
            category: Категория (опционально)
        
        Returns:
            Статистика
        """
        if category:
            events = [a for a in self._events.values() if a.event_category == category]
        else:
            events = list(self._events.values())
        
        if not events:
            return {}
        
        # Статистика по направлению
        direction_stats = {}
        for direction in EventDirection:
            direction_events = [a for a in events if a.expected_direction == direction]
            if direction_events:
                direction_stats[direction.value] = {
                    "count": len(direction_events),
                    "avg_return_pct": np.mean([a.avg_return_pct for a in direction_events]),
                    "avg_confidence": np.mean([a.confidence for a in direction_events]),
                }
        
        # Статистика по влиянию
        impact_stats = {}
        for impact in EventImpact:
            impact_events = [a for a in events if a.expected_impact == impact]
            if impact_events:
                impact_stats[impact.value] = {
                    "count": len(impact_events),
                    "avg_return_pct": np.mean([a.avg_return_pct for a in impact_events]),
                }
        
        return {
            "total_events": len(events),
            "by_direction": direction_stats,
            "by_impact": impact_stats,
            "avg_confidence": np.mean([a.confidence for a in events]),
            "avg_sample_size": np.mean([a.sample_size for a in events]),
        }


# Глобальный экземпляр
_event_response_engine: EventResponseEngine | None = None


def get_event_response_engine() -> EventResponseEngine:
    """Получить глобальный Event Response Engine"""
    global _event_response_engine
    if _event_response_engine is None:
        _event_response_engine = EventResponseEngine()
    return _event_response_engine


def reset_event_response_engine():
    """Сбросить Event Response Engine (для тестов)"""
    global _event_response_engine
    _event_response_engine = EventResponseEngine()
