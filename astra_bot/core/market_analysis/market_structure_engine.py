"""
ASTRA BOT - Market Structure Engine

Движок анализа структуры рынка (ТЗ Пункты 35)

Исследует:
- HH (Higher High)
- HL (Higher Low)
- LH (Lower High)
- LL (Lower Low)
- break of structure
- market structure shift
- liquidity sweep
- support/resistance
- range expansion
- range compression

Все concepts должны быть формализованы математически.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

from ...core import models

logger = logging.getLogger(__name__)


class StructurePattern(str, Enum):
    """Паттерны структуры"""
    UPTREND = "UPTREND"  # HH + HL
    DOWNTREND = "DOWNTREND"  # LH + LL
    RANGE = "RANGE"  # Смешанные HH/LL
    BREAKOUT = "BREAKOUT"  # Разрыв структуры
    BREAKDOWN = "BREAKDOWN"  # Разрыв структуры вниз
    STRUCTURE_SHIFT = "STRUCTURE_SHIFT"  # Смена структуры
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"  # Сметение ликвидности
    RANGE_EXPANSION = "RANGE_EXPANSION"  # Расширение диапазона
    RANGE_COMPRESSION = "RANGE_COMPRESSION"  # Сжатие диапазона
    UNKNOWN = "UNKNOWN"


@dataclass
class StructurePoint:
    """Точка структуры"""
    timestamp: datetime
    price: float
    point_type: str  # HH, HL, LH, LL
    swing_high: bool = False
    swing_low: bool = False
    strength: float = 0.0  # Сила точки (объём, время удержания и т.д.)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "price": self.price,
            "point_type": self.point_type,
            "swing_high": self.swing_high,
            "swing_low": self.swing_low,
            "strength": self.strength,
        }


@dataclass
class StructureLevel:
    """Уровень структуры (support/resistance)"""
    price: float
    level_type: str  # support/resistance
    strength: float = 0.0  # Сила уровня
    touches: int = 0  # Количество касаний
    last_touch: datetime | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "level_type": self.level_type,
            "strength": self.strength,
            "touches": self.touches,
            "last_touch": self.last_touch.isoformat() if self.last_touch else None,
        }


@dataclass
class StructureMetrics:
    """Метрики структуры"""
    symbol: str
    timestamp: datetime
    timeframe: str
    
    # Последние точки структуры
    last_hh: StructurePoint | None = None
    last_hl: StructurePoint | None = None
    last_lh: StructurePoint | None = None
    last_ll: StructurePoint | None = None
    
    # Уровни поддержки/сопротивления
    support_levels: list[StructureLevel] = field(default_factory=list)
    resistance_levels: list[StructureLevel] = field(default_factory=list)
    
    # Диапазоны
    current_range_high: float = 0.0
    current_range_low: float = 0.0
    range_width: float = 0.0
    range_width_pct: float = 0.0
    
    # Тренд структуры
    structure_trend: str = "neutral"  # up/down/neutral
    structure_strength: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "last_hh": self.last_hh.to_dict() if self.last_hh else None,
            "last_hl": self.last_hl.to_dict() if self.last_hl else None,
            "last_lh": self.last_lh.to_dict() if self.last_lh else None,
            "last_ll": self.last_ll.to_dict() if self.last_ll else None,
            "support_levels": [s.to_dict() for s in self.support_levels],
            "resistance_levels": [r.to_dict() for r in self.resistance_levels],
            "current_range_high": self.current_range_high,
            "current_range_low": self.current_range_low,
            "range_width": self.range_width,
            "range_width_pct": self.range_width_pct,
            "structure_trend": self.structure_trend,
            "structure_strength": self.structure_strength,
        }


@dataclass
class StructureBreak:
    """Разрыв структуры"""
    symbol: str
    timestamp: datetime
    break_type: str  # HH, HL, LH, LL
    break_price: float
    broken_level: float
    break_direction: str  # up/down
    break_strength: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "break_type": self.break_type,
            "break_price": self.break_price,
            "broken_level": self.broken_level,
            "break_direction": self.break_direction,
            "break_strength": self.break_strength,
        }


@dataclass
class StructureAnalysis:
    """Полный анализ структуры"""
    symbol: str
    timestamp: datetime
    timeframe: str
    
    # Метрики
    metrics: StructureMetrics
    
    # Паттерн
    pattern: StructurePattern = StructurePattern.UNKNOWN
    pattern_confidence: float = 0.0
    
    # Разрывы
    recent_breaks: list[StructureBreak] = field(default_factory=list)
    
    # Изменения структуры
    structure_shifts: list[datetime] = field(default_factory=list)
    
    # Статистика
    statistics: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "metrics": self.metrics.to_dict(),
            "pattern": self.pattern.value,
            "pattern_confidence": self.pattern_confidence,
            "recent_breaks": [b.to_dict() for b in self.recent_breaks],
            "structure_shifts": [s.isoformat() for s in self.structure_shifts],
            "statistics": self.statistics,
        }


class MarketStructureEngine:
    """
    Движок анализа структуры рынка.
    
    Идентифицирует паттерны структуры и отслеживает изменения.
    """
    
    def __init__(self):
        # История для анализа
        self._history: dict[str, list[models.Candle]] = {}
        self._structure_points: dict[str, list[StructurePoint]] = {}
        self._structure_breaks: dict[str, list[StructureBreak]] = {}
        self._structure_shifts: dict[str, list[datetime]] = {}
        
        # Уровни поддержки/сопротивления
        self._support_levels: dict[str, list[StructureLevel]] = {}
        self._resistance_levels: dict[str, list[StructureLevel]] = {}
        
        # Пороги
        self.thresholds = {
            "swing_high_lookback": 5,  # Количество свечей для поиска swing high
            "swing_low_lookback": 5,  # Количество свечей для поиска swing low
            "range_expansion_threshold": 0.1,  # 10% расширение диапазона
            "range_compression_threshold": 0.1,  # 10% сжатие диапазона
            "structure_shift_threshold": 3,  # 3 последовательных разрыва
        }
    
    def identify_structure_points(
        self,
        symbol: str,
        candles: list[models.Candle],
    ) -> list[StructurePoint]:
        """
        Идентифицировать точки структуры (HH, HL, LH, LL).
        
        Args:
            symbol: Символ
            candles: Список свечей
        
        Returns:
            Список точек структуры
        """
        if not candles:
            return []
        
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        timestamps = [c.timestamp for c in candles if hasattr(c, 'timestamp') and c.timestamp]
        
        points = []
        
        # Найти HH и LL (свечи с локальными максимумами/минимумами)
        for i in range(self.thresholds["swing_high_lookback"], 
                      len(highs) - self.thresholds["swing_high_lookback"]):
            # Проверка HH (Higher High)
            if all(highs[i] >= highs[i-j] for j in range(1, self.thresholds["swing_high_lookback"] + 1)) and \
               all(highs[i] >= highs[i+j] for j in range(1, self.thresholds["swing_high_lookback"] + 1) 
                      if i+j < len(highs)):
                points.append(StructurePoint(
                    timestamp=timestamps[i] if i < len(timestamps) else datetime.now(timezone.utc),
                    price=highs[i],
                    point_type="HH",
                    swing_high=True,
                    strength=1.0,
                ))
            
            # Проверка LL (Lower Low)
            if all(lows[i] <= lows[i-j] for j in range(1, self.thresholds["swing_low_lookback"] + 1)) and \
               all(lows[i] <= lows[i+j] for j in range(1, self.thresholds["swing_low_lookback"] + 1) 
                      if i+j < len(lows)):
                points.append(StructurePoint(
                    timestamp=timestamps[i] if i < len(timestamps) else datetime.now(timezone.utc),
                    price=lows[i],
                    point_type="LL",
                    swing_low=True,
                    strength=1.0,
                ))
        
        # Найти HL и LH (промежуточные точки)
        for i in range(1, len(highs)):
            # HL (Higher Low) - низ выше предыдущего низа
            if i > 0 and lows[i] > lows[i-1]:
                points.append(StructurePoint(
                    timestamp=timestamps[i] if i < len(timestamps) else datetime.now(timezone.utc),
                    price=lows[i],
                    point_type="HL",
                    swing_low=False,
                    strength=0.5,
                ))
            
            # LH (Lower High) - высок ниже предыдущего хая
            if i > 0 and highs[i] < highs[i-1]:
                points.append(StructurePoint(
                    timestamp=timestamps[i] if i < len(timestamps) else datetime.now(timezone.utc),
                    price=highs[i],
                    point_type="LH",
                    swing_high=False,
                    strength=0.5,
                ))
        
        # Сохранить в историю
        if symbol not in self._structure_points:
            self._structure_points[symbol] = []
        self._structure_points[symbol].extend(points)
        
        # Ограничить историю
        if len(self._structure_points[symbol]) > 1000:
            self._structure_points[symbol] = self._structure_points[symbol][-1000:]
        
        return points
    
    def identify_support_resistance(
        self,
        symbol: str,
        candles: list[models.Candle],
        lookback: int = 50,
    ) -> tuple[list[StructureLevel], list[StructureLevel]]:
        """
        Идентифицировать уровни поддержки и сопротивления.
        
        Args:
            symbol: Символ
            candles: Список свечей
            lookback: Количество свечей для анализа
        
        Returns:
            Уровни поддержки и сопротивления
        """
        if not candles:
            return [], []
        
        highs = [float(c.high) for c in candles[-lookback:]]
        lows = [float(c.low) for c in candles[-lookback:]]
        closes = [float(c.close) for c in candles[-lookback:]]
        
        support_levels = []
        resistance_levels = []
        
        # Найти уровни поддержки (локальные минимумы)
        for i in range(1, len(lows) - 1):
            if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]:
                # Проверка, что цена отскакивала от этого уровня
                bounce_count = 0
                for j in range(i+1, min(i+10, len(closes))):
                    if closes[j] > lows[i]:
                        bounce_count += 1
                
                if bounce_count >= 2:  # минимум 2 отскока
                    support_levels.append(StructureLevel(
                        price=lows[i],
                        level_type="support",
                        strength=min(1.0, bounce_count / 10),
                        touches=1,
                        last_touch=candles[-lookback + i].timestamp if hasattr(candles[-lookback + i], 'timestamp') else None,
                    ))
        
        # Найти уровни сопротивления (локальные максимумы)
        for i in range(1, len(highs) - 1):
            if highs[i] >= highs[i-1] and highs[i] >= highs[i+1]:
                # Проверка, что цена отскакивала от этого уровня
                bounce_count = 0
                for j in range(i+1, min(i+10, len(closes))):
                    if closes[j] < highs[i]:
                        bounce_count += 1
                
                if bounce_count >= 2:  # минимум 2 отскока
                    resistance_levels.append(StructureLevel(
                        price=highs[i],
                        level_type="resistance",
                        strength=min(1.0, bounce_count / 10),
                        touches=1,
                        last_touch=candles[-lookback + i].timestamp if hasattr(candles[-lookback + i], 'timestamp') else None,
                    ))
        
        # Объединить с существующими уровнями
        if symbol in self._support_levels:
            existing_supports = self._support_levels[symbol]
            for level in support_levels:
                # Проверить, есть ли уже такой уровень
                found = False
                for existing in existing_supports:
                    if abs(existing.price - level.price) / level.price < 0.01:  # 1% разница
                        existing.touches += 1
                        existing.last_touch = level.last_touch
                        existing.strength = min(1.0, existing.strength + 0.1)
                        found = True
                        break
                if not found:
                    existing_supports.append(level)
            support_levels = existing_supports
        else:
            self._support_levels[symbol] = support_levels
        
        if symbol in self._resistance_levels:
            existing_resistances = self._resistance_levels[symbol]
            for level in resistance_levels:
                # Проверить, есть ли уже такой уровень
                found = False
                for existing in existing_resistances:
                    if abs(existing.price - level.price) / level.price < 0.01:  # 1% разница
                        existing.touches += 1
                        existing.last_touch = level.last_touch
                        existing.strength = min(1.0, existing.strength + 0.1)
                        found = True
                        break
                if not found:
                    existing_resistances.append(level)
            resistance_levels = existing_resistances
        else:
            self._resistance_levels[symbol] = resistance_levels
        
        return support_levels, resistance_levels
    
    def detect_structure_breaks(
        self,
        symbol: str,
        candles: list[models.Candle],
    ) -> list[StructureBreak]:
        """
        Обнаружить разрывы структуры.
        
        Args:
            symbol: Символ
            candles: Список свечей
        
        Returns:
            Список разрывов структуры
        """
        if not candles:
            return []
        
        breaks = []
        
        if symbol in self._support_levels:
            for level in self._support_levels[symbol]:
                current_price = float(candles[-1].close)
                if current_price < level.price and current_price < level.price * 0.99:  # Пробит уровень
                    breaks.append(StructureBreak(
                        symbol=symbol,
                        timestamp=candles[-1].timestamp if hasattr(candles[-1], 'timestamp') else datetime.now(timezone.utc),
                        break_type="LL",
                        break_price=current_price,
                        broken_level=level.price,
                        break_direction="down",
                        break_strength=level.strength,
                    ))
        
        if symbol in self._resistance_levels:
            for level in self._resistance_levels[symbol]:
                current_price = float(candles[-1].close)
                if current_price > level.price and current_price > level.price * 1.01:  # Пробит уровень
                    breaks.append(StructureBreak(
                        symbol=symbol,
                        timestamp=candles[-1].timestamp if hasattr(candles[-1], 'timestamp') else datetime.now(timezone.utc),
                        break_type="HH",
                        break_price=current_price,
                        broken_level=level.price,
                        break_direction="up",
                        break_strength=level.strength,
                    ))
        
        # Сохранить в историю
        if symbol not in self._structure_breaks:
            self._structure_breaks[symbol] = []
        self._structure_breaks[symbol].extend(breaks)
        
        if len(self._structure_breaks[symbol]) > 100:
            self._structure_breaks[symbol] = self._structure_breaks[symbol][-100:]
        
        return breaks
    
    def detect_structure_shifts(
        self,
        symbol: str,
        candles: list[models.Candle],
    ) -> list[datetime]:
        """
        Обнаружить смены структуры.
        
        Args:
            symbol: Символ
            candles: Список свечей
        
        Returns:
            Список времен смены структуры
        """
        shifts = []
        
        if symbol in self._structure_breaks and len(self._structure_breaks[symbol]) >= self.thresholds["structure_shift_threshold"]:
            recent_breaks = self._structure_breaks[symbol][-self.thresholds["structure_shift_threshold"]:]
            
            # Если все разрывы в одном направлении
            directions = set(b.break_direction for b in recent_breaks)
            if len(directions) == 1:
                # Это смена структуры
                shifts.append(candles[-1].timestamp if hasattr(candles[-1], 'timestamp') else datetime.now(timezone.utc))
        
        # Сохранить в историю
        if symbol not in self._structure_shifts:
            self._structure_shifts[symbol] = []
        self._structure_shifts[symbol].extend(shifts)
        
        if len(self._structure_shifts[symbol]) > 100:
            self._structure_shifts[symbol] = self._structure_shifts[symbol][-100:]
        
        return shifts
    
    def detect_range_expansion_compression(
        self,
        symbol: str,
        candles: list[models.Candle],
    ) -> tuple[bool, bool]:
        """
        Обнаружить расширение или сжатие диапазона.
        
        Args:
            symbol: Символ
            candles: Список свечей
        
        Returns:
            (is_expanding, is_compressing)
        """
        if len(candles) < 20:
            return False, False
        
        # Текущий диапазон
        current_high = max(float(c.high) for c in candles[-10:])
        current_low = min(float(c.low) for c in candles[-10:])
        current_range = current_high - current_low
        
        # Предыдущий диапазон
        prev_high = max(float(c.high) for c in candles[-20:-10])
        prev_low = min(float(c.low) for c in candles[-20:-10])
        prev_range = prev_high - prev_low
        
        if prev_range > 0:
            expansion_pct = (current_range - prev_range) / prev_range
            is_expanding = expansion_pct > self.thresholds["range_expansion_threshold"]
            is_compressing = expansion_pct < -self.thresholds["range_compression_threshold"]
            return is_expanding, is_compressing
        
        return False, False
    
    def determine_structure_pattern(
        self,
        symbol: str,
        candles: list[models.Candle],
    ) -> tuple[StructurePattern, float]:
        """
        Определить паттерн структуры.
        
        Args:
            symbol: Символ
            candles: Список свечей
        
        Returns:
            Паттерн и уверенность
        """
        confidence = 0.5
        
        # Идентифицировать точки структуры
        points = self.identify_structure_points(symbol, candles)
        
        if not points:
            return StructurePattern.UNKNOWN, 0.3
        
        # Считать количество каждого типа
        hh_count = sum(1 for p in points if p.point_type == "HH")
        ll_count = sum(1 for p in points if p.point_type == "LL")
        hl_count = sum(1 for p in points if p.point_type == "HL")
        lh_count = sum(1 for p in points if p.point_type == "LH")
        
        # Определить паттерн
        if hh_count > 0 and hl_count > 0 and hh_count >= hl_count:
            # UPTREND: HH + HL
            return StructurePattern.UPTREND, 0.8
        elif ll_count > 0 and lh_count > 0 and ll_count >= lh_count:
            # DOWNTREND: LH + LL
            return StructurePattern.DOWNTREND, 0.8
        elif hh_count > 0 and lh_count > 0:
            # RANGE: смешанные
            return StructurePattern.RANGE, 0.7
        
        # Проверка разрывов
        breaks = self.detect_structure_breaks(symbol, candles)
        if breaks:
            up_breaks = sum(1 for b in breaks if b.break_direction == "up")
            down_breaks = sum(1 for b in breaks if b.break_direction == "down")
            
            if up_breaks > down_breaks:
                return StructurePattern.BREAKOUT, 0.8
            elif down_breaks > up_breaks:
                return StructurePattern.BREAKDOWN, 0.8
        
        # Проверка смены структуры
        shifts = self.detect_structure_shifts(symbol, candles)
        if shifts:
            return StructurePattern.STRUCTURE_SHIFT, 0.8
        
        # Проверка расширения/сжатия диапазона
        is_expanding, is_compressing = self.detect_range_expansion_compression(symbol, candles)
        if is_expanding:
            return StructurePattern.RANGE_EXPANSION, 0.7
        elif is_compressing:
            return StructurePattern.RANGE_COMPRESSION, 0.7
        
        return StructurePattern.UNKNOWN, 0.3
    
    def analyze_market_structure(
        self,
        symbol: str,
        candles: list[models.Candle],
        timeframe: str = "1h",
        timestamp: datetime | None = None,
    ) -> StructureAnalysis:
        """
        Полный анализ структуры рынка.
        
        Args:
            symbol: Символ
            candles: Список свечей
            timeframe: Таймфрейм
            timestamp: Временная метка
        
        Returns:
            Полный анализ структуры
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        if not candles:
            return StructureAnalysis(
                symbol=symbol,
                timestamp=timestamp,
                timeframe=timeframe,
                metrics=StructureMetrics(
                    symbol=symbol,
                    timestamp=timestamp,
                    timeframe=timeframe,
                ),
                pattern=StructurePattern.UNKNOWN,
                pattern_confidence=0.0,
            )
        
        # Идентифицировать точки структуры
        points = self.identify_structure_points(symbol, candles)
        
        # Найти последние точки каждого типа
        last_hh = next((p for p in reversed(points) if p.point_type == "HH"), None)
        last_hl = next((p for p in reversed(points) if p.point_type == "HL"), None)
        last_lh = next((p for p in reversed(points) if p.point_type == "LH"), None)
        last_ll = next((p for p in reversed(points) if p.point_type == "LL"), None)
        
        # Идентифицировать уровни поддержки/сопротивления
        support_levels, resistance_levels = self.identify_support_resistance(symbol, candles)
        
        # Текущий диапазон
        current_range_high = max(float(c.high) for c in candles[-10:])
        current_range_low = min(float(c.low) for c in candles[-10:])
        current_price = float(candles[-1].close)
        range_width = current_range_high - current_range_low
        range_width_pct = (range_width / current_price * 100) if current_price > 0 else 0.0
        
        # Определить тренд структуры
        if last_hh and last_lh:
            if last_hh.timestamp > last_lh.timestamp:
                structure_trend = "up"
                structure_strength = min(1.0, (current_range_high - current_price) / range_width)
            else:
                structure_trend = "down"
                structure_strength = min(1.0, (current_price - current_range_low) / range_width)
        else:
            structure_trend = "neutral"
            structure_strength = 0.5
        
        # Создать метрики
        metrics = StructureMetrics(
            symbol=symbol,
            timestamp=timestamp,
            timeframe=timeframe,
            last_hh=last_hh,
            last_hl=last_hl,
            last_lh=last_lh,
            last_ll=last_ll,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            current_range_high=current_range_high,
            current_range_low=current_range_low,
            range_width=range_width,
            range_width_pct=range_width_pct,
            structure_trend=structure_trend,
            structure_strength=structure_strength,
        )
        
        # Обнаружить разрывы
        breaks = self.detect_structure_breaks(symbol, candles)
        
        # Обнаружить смены структуры
        shifts = self.detect_structure_shifts(symbol, candles)
        
        # Определить паттерн
        pattern, confidence = self.determine_structure_pattern(symbol, candles)
        
        # Статистика
        statistics = {
            "pattern": pattern.value,
            "pattern_confidence": confidence,
            "has_breaks": len(breaks) > 0,
            "has_shifts": len(shifts) > 0,
            "num_support_levels": len(support_levels),
            "num_resistance_levels": len(resistance_levels),
        }
        
        return StructureAnalysis(
            symbol=symbol,
            timestamp=timestamp,
            timeframe=timeframe,
            metrics=metrics,
            pattern=pattern,
            pattern_confidence=confidence,
            recent_breaks=breaks,
            structure_shifts=shifts,
            statistics=statistics,
        )


# Глобальный экземпляр
_market_structure_engine: MarketStructureEngine | None = None


def get_market_structure_engine() -> MarketStructureEngine:
    """Получить глобальный Market Structure Engine"""
    global _market_structure_engine
    if _market_structure_engine is None:
        _market_structure_engine = MarketStructureEngine()
    return _market_structure_engine


def reset_market_structure_engine():
    """Сбросить Market Structure Engine (для тестов)"""
    global _market_structure_engine
    _market_structure_engine = MarketStructureEngine()
