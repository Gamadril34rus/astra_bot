"""
ASTRA BOT - Liquidity Map Engine

Движок карты ликвидности
Приоритетное направление #2

Исследует:
- Концентрацию ликвидности по уровням цен
- Liquidity sweeps (сметение ликвидности)
- Реакцию цены до/после снятия ликвидности
- Паттерн: liquidity → sweep → reaction → continuation/reversal

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from collections import defaultdict

import numpy as np

logger = logging.getLogger(__name__)


class LiquidityZoneType(str, Enum):
    """Типы зон ликвидности"""
    HIGH_VOLUME = "high_volume"  # Высокий объём
    LOW_VOLUME = "low_volume"    # Низкий объём
    SUPPORT = "support"          # Поддержка
    RESISTANCE = "resistance"    # Сопротивление
    NEUTRAL = "neutral"         # Нейтральная


class SweepType(str, Enum):
    """Типы сметения ликвидности"""
    BULLISH_SWEEP = "bullish_sweep"    # Сметение вверх (покупка)
    BEARISH_SWEEP = "bearish_sweep"    # Сметение вниз (продажа)
    FALSE_BREAKOUT = "false_breakout"  # Ложный пробой
    TRUE_BREAKOUT = "true_breakout"    # Истинный пробой
    NONE = "none"                      # Нет сметения


class ReactionType(str, Enum):
    """Типы реакции цены"""
    CONTINUATION = "continuation"      # Продолжение
    REVERSAL = "reversal"            # Разворот
    CONSOLIDATION = "consolidation"  # Консолидация
    NONE = "none"                    # Нет реакции


@dataclass
class LiquidityLevel:
    """Уровень ликвидности"""
    price: float
    volume: float
    side: str  # bid/ask
    zone_type: LiquidityZoneType = LiquidityZoneType.NEUTRAL

    # Время создания
    creation_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Статистика
    touches: int = 0  # Количество касаний
    last_touch: datetime | None = None
    volume_removed: float = 0.0  # Объём, который был снят

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "volume": self.volume,
            "side": self.side,
            "zone_type": self.zone_type.value,
            "creation_time": self.creation_time.isoformat(),
            "touches": self.touches,
            "last_touch": self.last_touch.isoformat() if self.last_touch else None,
            "volume_removed": self.volume_removed,
        }


@dataclass
class LiquiditySweep:
    """Сметение ликвидности"""
    timestamp: datetime
    symbol: str

    # Уровень
    level: LiquidityLevel

    # Тип
    sweep_type: SweepType = SweepType.NONE

    # Объём
    sweep_volume: float = 0.0
    remaining_volume: float = 0.0

    # Цена
    sweep_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0

    # Реакция
    reaction_type: ReactionType = ReactionType.NONE
    reaction_strength: float = 0.0

    # Время
    reaction_time_ms: float = 0.0

    # Уверенность
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "sweep_type": self.sweep_type.value,
            "sweep_volume": self.sweep_volume,
            "remaining_volume": self.remaining_volume,
            "sweep_price": self.sweep_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "reaction_type": self.reaction_type.value,
            "reaction_strength": self.reaction_strength,
            "reaction_time_ms": self.reaction_time_ms,
            "confidence": self.confidence,
            "level": self.level.to_dict() if self.level else None,
        }


@dataclass
class LiquidityPattern:
    """Паттерн ликвидности"""
    pattern_type: str
    timestamp: datetime
    symbol: str

    # Участвующие уровни
    levels: list[LiquidityLevel] = field(default_factory=list)

    # Метрики
    total_volume: float = 0.0
    price_range: float = 0.0

    # Уверенность
    confidence: float = 0.0

    # Сила паттерна
    strength: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "total_volume": self.total_volume,
            "price_range": self.price_range,
            "confidence": self.confidence,
            "strength": self.strength,
            "levels_count": len(self.levels),
        }


@dataclass
class LiquidityAnalysis:
    """Полный анализ ликвидности"""
    symbol: str
    timestamp: datetime
    timeframe: str = "1m"

    # Уровни ликвидности
    levels: list[LiquidityLevel] = field(default_factory=list)

    # Зоны
    high_volume_zones: list[LiquidityLevel] = field(default_factory=list)
    low_volume_zones: list[LiquidityLevel] = field(default_factory=list)
    support_zones: list[LiquidityLevel] = field(default_factory=list)
    resistance_zones: list[LiquidityLevel] = field(default_factory=list)

    # Сметения
    sweeps: list[LiquiditySweep] = field(default_factory=list)

    # Паттерны
    patterns: list[LiquidityPattern] = field(default_factory=list)

    # Метрики
    total_liquidity: float = 0.0
    liquidity_imbalance: float = 0.0

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
            "timeframe": self.timeframe,
            "total_levels": len(self.levels),
            "high_volume_zones": len(self.high_volume_zones),
            "low_volume_zones": len(self.low_volume_zones),
            "support_zones": len(self.support_zones),
            "resistance_zones": len(self.resistance_zones),
            "sweeps_count": len(self.sweeps),
            "patterns_count": len(self.patterns),
            "total_liquidity": self.total_liquidity,
            "liquidity_imbalance": self.liquidity_imbalance,
            "signals": self.signals,
            "recommendations": self.recommendations,
            "confidence": self.confidence,
        }


class LiquidityMapEngine:
    """
    Движок карты ликвидности.

    Создаёт карту ликвидности и отслеживает сметения.
    """

    def __init__(self):
        # Карта ликвидности по символам
        self._liquidity_maps: dict[str, list[LiquidityLevel]] = {}

        # История сметений
        self._sweep_history: dict[str, list[LiquiditySweep]] = {}

        # История цен
        self._price_history: dict[str, list[tuple[datetime, float, float, float, float]]] = {}

        # Пороги
        self.thresholds = {
            "high_volume_multiplier": 2.0,  # Уровень с объёмом > 2x средний
            "low_volume_multiplier": 0.5,   # Уровень с объёмом < 0.5x средний
            "sweep_threshold": 0.7,        # 70% объёма снято
            "reaction_threshold": 0.01,    # 1% движение цены для реакции
            "zone_min_levels": 3,          # Минимальное количество уровней для зоны
            "zone_max_distance_pct": 0.02, # 2% максимальное расстояние между уровнями
        }

        # Параметры
        self.window_size = 1000

    def update_liquidity_map(
        self,
        symbol: str,
        timestamp: datetime,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        current_price: float,
    ):
        """
        Обновить карту ликвидности.

        Args:
            symbol: Символ
            timestamp: Временная метка
            bids: Список (цена, объём) для покупок
            asks: Список (цена, объём) для продаж
            current_price: Текущая цена
        """
        if symbol not in self._liquidity_maps:
            self._liquidity_maps[symbol] = []

        if symbol not in self._price_history:
            self._price_history[symbol] = []

        # Сохранить текущую цену
        self._price_history[symbol].append((timestamp, current_price, 0.0, 0.0, 0.0))
        if len(self._price_history[symbol]) > self.window_size:
            self._price_history[symbol] = self._price_history[symbol][-self.window_size:]

        # Обновить уровни ликвидности
        existing_levels = {l.price: l for l in self._liquidity_maps[symbol]}

        # Обновить биды
        for price, volume in bids:
            if price in existing_levels:
                level = existing_levels[price]
                if volume > level.volume:
                    # Объём увеличился
                    level.volume = volume
                else:
                    # Объём уменьшился
                    level.volume_removed += level.volume - volume
                    level.volume = volume
                level.last_touch = timestamp
                level.touches += 1
            else:
                # Новый уровень
                level = LiquidityLevel(
                    price=price,
                    volume=volume,
                    side="bid",
                    creation_time=timestamp,
                    last_touch=timestamp,
                    touches=1,
                )
                self._liquidity_maps[symbol].append(level)
                existing_levels[price] = level

        # Обновить аски
        for price, volume in asks:
            if price in existing_levels:
                level = existing_levels[price]
                if volume > level.volume:
                    level.volume = volume
                else:
                    level.volume_removed += level.volume - volume
                    level.volume = volume
                level.last_touch = timestamp
                level.touches += 1
            else:
                level = LiquidityLevel(
                    price=price,
                    volume=volume,
                    side="ask",
                    creation_time=timestamp,
                    last_touch=timestamp,
                    touches=1,
                )
                self._liquidity_maps[symbol].append(level)
                existing_levels[price] = level

        # Удалить уровни с нулевым объёмом
        self._liquidity_maps[symbol] = [l for l in self._liquidity_maps[symbol] if l.volume > 0]

        # Обновить типы зон
        self._update_zone_types(symbol, current_price)

        # Обнаружить сметения
        self._detect_sweeps(symbol, timestamp, current_price)

    def _update_zone_types(self, symbol: str, current_price: float):
        """Обновить типы зон ликвидности"""
        if symbol not in self._liquidity_maps:
            return

        levels = self._liquidity_maps[symbol]

        if not levels:
            return

        # Рассчитать средний объём
        avg_volume = sum(l.volume for l in levels) / len(levels) if levels else 0

        for level in levels:
            # Определить тип зоны по объёму
            if level.volume > avg_volume * self.thresholds["high_volume_multiplier"]:
                level.zone_type = LiquidityZoneType.HIGH_VOLUME
            elif level.volume < avg_volume * self.thresholds["low_volume_multiplier"]:
                level.zone_type = LiquidityZoneType.LOW_VOLUME
            else:
                level.zone_type = LiquidityZoneType.NEUTRAL

            # Определить тип зоны по позиции относительно цены
            if level.side == "bid" and level.price > current_price:
                level.zone_type = LiquidityZoneType.RESISTANCE
            elif level.side == "ask" and level.price < current_price:
                level.zone_type = LiquidityZoneType.SUPPORT

        # Объединить близкие уровни в зоны
        self._merge_into_zones(symbol)

    def _merge_into_zones(self, symbol: str):
        """Объединить близкие уровни в зоны"""
        if symbol not in self._liquidity_maps:
            return

        levels = sorted(self._liquidity_maps[symbol], key=lambda x: x.price)

        # Группировать уровни по близости
        zones = []
        current_zone = [levels[0]] if levels else []

        for i in range(1, len(levels)):
            if abs(levels[i].price - current_zone[-1].price) / current_zone[-1].price <= self.thresholds["zone_max_distance_pct"]:
                current_zone.append(levels[i])
            else:
                if len(current_zone) >= self.thresholds["zone_min_levels"]:
                    zones.append(current_zone)
                current_zone = [levels[i]]

        if len(current_zone) >= self.thresholds["zone_min_levels"]:
            zones.append(current_zone)

        # Обновить типы зон
        for zone in zones:
            zone_type = LiquidityZoneType.NEUTRAL

            # Если majority bid levels below current price → support
            # Если majority ask levels above current price → resistance
            bid_count = sum(1 for l in zone if l.side == "bid")
            ask_count = sum(1 for l in zone if l.side == "ask")

            if bid_count > ask_count:
                zone_type = LiquidityZoneType.SUPPORT
            elif ask_count > bid_count:
                zone_type = LiquidityZoneType.RESISTANCE

            for level in zone:
                level.zone_type = zone_type

    def _detect_sweeps(self, symbol: str, timestamp: datetime, current_price: float):
        """Обнаружить сметения ликвидности"""
        if symbol not in self._liquidity_maps:
            return

        levels = self._liquidity_maps[symbol]

        for level in levels:
            # Проверить, был ли снят значительный объём
            if level.volume_removed > 0:
                sweep_ratio = level.volume_removed / (level.volume + level.volume_removed)

                if sweep_ratio >= self.thresholds["sweep_threshold"]:
                    # Определить тип сметения
                    if level.side == "bid" and level.price < current_price:
                        sweep_type = SweepType.BULLISH_SWEEP
                    elif level.side == "ask" and level.price > current_price:
                        sweep_type = SweepType.BEARISH_SWEEP
                    else:
                        sweep_type = SweepType.NONE

                    if sweep_type != SweepType.NONE:
                        # Создать сметение
                        sweep = LiquiditySweep(
                            timestamp=timestamp,
                            symbol=symbol,
                            level=level,
                            sweep_type=sweep_type,
                            sweep_volume=level.volume_removed,
                            remaining_volume=level.volume,
                            sweep_price=level.price,
                        )

                        # Рассчитать реакцию цены
                        if symbol in self._price_history and len(self._price_history[symbol]) >= 2:
                            prices = [p[1] for p in self._price_history[symbol]]
                            sweep_index = len(self._price_history[symbol]) - 1

                            # Найти максимум/минимум после сметения
                            if sweep_type == SweepType.BULLISH_SWEEP:
                                high_price = max(prices[sweep_index:sweep_index+10]) if sweep_index+10 <= len(prices) else current_price
                                low_price = min(prices[sweep_index:sweep_index+10]) if sweep_index+10 <= len(prices) else current_price
                            else:
                                high_price = max(prices[sweep_index-10:sweep_index]) if sweep_index-10 >= 0 else current_price
                                low_price = min(prices[sweep_index-10:sweep_index]) if sweep_index-10 >= 0 else current_price

                            sweep.high_price = high_price
                            sweep.low_price = low_price

                            # Определить тип реакции
                            if sweep_type == SweepType.BULLISH_SWEEP:
                                if current_price > level.price:
                                    sweep.reaction_type = ReactionType.CONTINUATION
                                else:
                                    sweep.reaction_type = ReactionType.REVERSAL
                            else:
                                if current_price < level.price:
                                    sweep.reaction_type = ReactionType.CONTINUATION
                                else:
                                    sweep.reaction_type = ReactionType.REVERSAL

                            # Рассчитать силу реакции
                            if sweep.reaction_type == ReactionType.CONTINUATION:
                                if sweep_type == SweepType.BULLISH_SWEEP:
                                    sweep.reaction_strength = (current_price - level.price) / level.price
                                else:
                                    sweep.reaction_strength = (level.price - current_price) / level.price
                            elif sweep.reaction_type == ReactionType.REVERSAL:
                                if sweep_type == SweepType.BULLISH_SWEEP:
                                    sweep.reaction_strength = (level.price - current_price) / level.price
                                else:
                                    sweep.reaction_strength = (current_price - level.price) / level.price

                        # Сохранить сметение
                        if symbol not in self._sweep_history:
                            self._sweep_history[symbol] = []
                        self._sweep_history[symbol].append(sweep)

                        # Сбросить volume_removed
                        level.volume_removed = 0.0

    def get_liquidity_levels(
        self,
        symbol: str,
        side: str | None = None,
        zone_type: LiquidityZoneType | None = None,
    ) -> list[LiquidityLevel]:
        """
        Получить уровни ликвидности.

        Args:
            symbol: Символ
            side: Сторона (bid/ask)
            zone_type: Тип зоны

        Returns:
            Список уровней
        """
        if symbol not in self._liquidity_maps:
            return []

        levels = self._liquidity_maps[symbol]

        if side:
            levels = [l for l in levels if l.side == side]

        if zone_type:
            levels = [l for l in levels if l.zone_type == zone_type]

        return sorted(levels, key=lambda x: x.price)

    def get_sweep_history(
        self,
        symbol: str,
        sweep_type: SweepType | None = None,
        limit: int = 100,
    ) -> list[LiquiditySweep]:
        """
        Получить историю сметений.

        Args:
            symbol: Символ
            sweep_type: Тип сметения
            limit: Лимит

        Returns:
            Список сметений
        """
        if symbol not in self._sweep_history:
            return []

        sweeps = self._sweep_history[symbol]

        if sweep_type:
            sweeps = [s for s in sweeps if s.sweep_type == sweep_type]

        return sorted(sweeps, key=lambda x: x.timestamp, reverse=True)[:limit]

    def analyze_liquidity(
        self,
        symbol: str,
        timestamp: datetime,
        current_price: float,
        timeframe: str = "1m",
    ) -> LiquidityAnalysis:
        """
        Полный анализ ликвидности.

        Args:
            symbol: Символ
            timestamp: Временная метка
            current_price: Текущая цена
            timeframe: Временной горизонт

        Returns:
            Полный анализ
        """
        # Получить уровни
        all_levels = self.get_liquidity_levels(symbol)

        # Разделить по типам
        high_volume = [l for l in all_levels if l.zone_type == LiquidityZoneType.HIGH_VOLUME]
        low_volume = [l for l in all_levels if l.zone_type == LiquidityZoneType.LOW_VOLUME]
        support = [l for l in all_levels if l.zone_type == LiquidityZoneType.SUPPORT]
        resistance = [l for l in all_levels if l.zone_type == LiquidityZoneType.RESISTANCE]

        # Получить сметения
        sweeps = self.get_sweep_history(symbol)

        # Рассчитать метрики
        total_liquidity = sum(l.volume for l in all_levels)

        bid_liquidity = sum(l.volume for l in all_levels if l.side == "bid")
        ask_liquidity = sum(l.volume for l in all_levels if l.side == "ask")

        if total_liquidity > 0:
            liquidity_imbalance = (bid_liquidity - ask_liquidity) / total_liquidity
        else:
            liquidity_imbalance = 0.0

        # Обнаружить паттерны
        patterns = self._detect_patterns(symbol, current_price, all_levels, sweeps)

        # Сгенерировать сигналы
        signals = self._generate_signals(all_levels, sweeps, current_price)

        # Сгенерировать рекомендации
        recommendations = self._generate_recommendations(all_levels, sweeps, current_price)

        # Рассчитать уверенность
        confidence = self._calculate_confidence(all_levels, sweeps)

        return LiquidityAnalysis(
            symbol=symbol,
            timestamp=timestamp,
            timeframe=timeframe,
            levels=all_levels,
            high_volume_zones=high_volume,
            low_volume_zones=low_volume,
            support_zones=support,
            resistance_zones=resistance,
            sweeps=sweeps,
            patterns=patterns,
            total_liquidity=total_liquidity,
            liquidity_imbalance=liquidity_imbalance,
            signals=signals,
            recommendations=recommendations,
            confidence=confidence,
        )

    def _detect_patterns(
        self,
        symbol: str,
        current_price: float,
        levels: list[LiquidityLevel],
        sweeps: list[LiquiditySweep],
    ) -> list[LiquidityPattern]:
        """Обнаружить паттерны ликвидности"""
        patterns = []

        # Паттерн: Liquidity Void (пустота ликвидности)
        if not levels:
            patterns.append(LiquidityPattern(
                pattern_type="LIQUIDITY_VOID",
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                confidence=0.9,
                strength=1.0,
            ))

        # Паттерн: Liquidity Sweep → Continuation
        continuation_sweeps = [s for s in sweeps if s.reaction_type == ReactionType.CONTINUATION]
        if continuation_sweeps:
            patterns.append(LiquidityPattern(
                pattern_type="SWEEP_CONTINUATION",
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                levels=[s.level for s in continuation_sweeps],
                confidence=0.8,
                strength=len(continuation_sweeps) / len(sweeps) if sweeps else 0.0,
            ))

        # Паттерн: Liquidity Sweep → Reversal
        reversal_sweeps = [s for s in sweeps if s.reaction_type == ReactionType.REVERSAL]
        if reversal_sweeps:
            patterns.append(LiquidityPattern(
                pattern_type="SWEEP_REVERSAL",
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                levels=[s.level for s in reversal_sweeps],
                confidence=0.8,
                strength=len(reversal_sweeps) / len(sweeps) if sweeps else 0.0,
            ))

        # Паттерн: High Liquidity Above (сопротивление)
        high_above = [l for l in levels if l.zone_type == LiquidityZoneType.HIGH_VOLUME and l.price > current_price]
        if high_above:
            patterns.append(LiquidityPattern(
                pattern_type="HIGH_LIQUIDITY_ABOVE",
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                levels=high_above,
                confidence=0.7,
                strength=sum(l.volume for l in high_above) / sum(l.volume for l in levels) if levels else 0.0,
            ))

        # Паттерн: High Liquidity Below (поддержка)
        high_below = [l for l in levels if l.zone_type == LiquidityZoneType.HIGH_VOLUME and l.price < current_price]
        if high_below:
            patterns.append(LiquidityPattern(
                pattern_type="HIGH_LIQUIDITY_BELOW",
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                levels=high_below,
                confidence=0.7,
                strength=sum(l.volume for l in high_below) / sum(l.volume for l in levels) if levels else 0.0,
            ))

        return patterns

    def _generate_signals(
        self,
        levels: list[LiquidityLevel],
        sweeps: list[LiquiditySweep],
        current_price: float,
    ) -> list[str]:
        """Сгенерировать сигналы"""
        signals = []

        # Сигналы по сметениям
        for sweep in sweeps:
            if sweep.sweep_type == SweepType.BULLISH_SWEEP:
                signals.append("BULLISH_SWEEP")
            elif sweep.sweep_type == SweepType.BEARISH_SWEEP:
                signals.append("BEARISH_SWEEP")

            if sweep.reaction_type == ReactionType.CONTINUATION:
                signals.append("SWEEP_CONTINUATION")
            elif sweep.reaction_type == ReactionType.REVERSAL:
                signals.append("SWEEP_REVERSAL")

        # Сигналы по зонам
        support_levels = [l for l in levels if l.zone_type == LiquidityZoneType.SUPPORT]
        resistance_levels = [l for l in levels if l.zone_type == LiquidityZoneType.RESISTANCE]

        if support_levels:
            signals.append("SUPPORT_ZONE_DETECTED")
        if resistance_levels:
            signals.append("RESISTANCE_ZONE_DETECTED")

        # Сигналы по ликвидности
        bid_liquidity = sum(l.volume for l in levels if l.side == "bid")
        ask_liquidity = sum(l.volume for l in levels if l.side == "ask")

        if bid_liquidity > ask_liquidity * 1.5:
            signals.append("BULLISH_LIQUIDITY_IMBALANCE")
        elif ask_liquidity > bid_liquidity * 1.5:
            signals.append("BEARISH_LIQUIDITY_IMBALANCE")

        if not signals:
            signals.append("NEUTRAL_LIQUIDITY")

        return signals

    def _generate_recommendations(
        self,
        levels: list[LiquidityLevel],
        sweeps: list[LiquiditySweep],
        current_price: float,
    ) -> list[str]:
        """Сгенерировать рекомендации"""
        recommendations = []

        # Рекомендации по сметениям
        for sweep in sweeps:
            if sweep.sweep_type == SweepType.BULLISH_SWEEP:
                if sweep.reaction_type == ReactionType.CONTINUATION:
                    recommendations.append("Bullish sweep with continuation - potential uptrend")
                elif sweep.reaction_type == ReactionType.REVERSAL:
                    recommendations.append("Bullish sweep with reversal - potential trap")
            elif sweep.sweep_type == SweepType.BEARISH_SWEEP:
                if sweep.reaction_type == ReactionType.CONTINUATION:
                    recommendations.append("Bearish sweep with continuation - potential downtrend")
                elif sweep.reaction_type == ReactionType.REVERSAL:
                    recommendations.append("Bearish sweep with reversal - potential trap")

        # Рекомендации по зонам
        support_levels = [l for l in levels if l.zone_type == LiquidityZoneType.SUPPORT]
        resistance_levels = [l for l in levels if l.zone_type == LiquidityZoneType.RESISTANCE]

        if support_levels:
            closest_support = min(support_levels, key=lambda x: abs(x.price - current_price))
            recommendations.append(f"Support zone at {closest_support.price:.2f} with {closest_support.volume:.2f} volume")

        if resistance_levels:
            closest_resistance = min(resistance_levels, key=lambda x: abs(x.price - current_price))
            recommendations.append(f"Resistance zone at {closest_resistance.price:.2f} with {closest_resistance.volume:.2f} volume")

        if not recommendations:
            recommendations.append("No significant liquidity patterns detected")

        return recommendations

    def _calculate_confidence(
        self,
        levels: list[LiquidityLevel],
        sweeps: list[LiquiditySweep],
    ) -> float:
        """Рассчитать уверенность"""
        confidence = 0.5

        # Учесть количество уровней
        if len(levels) > 10:
            confidence += 0.1

        # Учесть количество сметений
        if sweeps:
            confidence += 0.1

        # Учесть силу реакций
        for sweep in sweeps:
            if sweep.reaction_strength > 0.01:
                confidence += 0.05

        return min(1.0, confidence)


# Глобальный экземпляр
_liquidity_map_engine: LiquidityMapEngine | None = None


def get_liquidity_map_engine() -> LiquidityMapEngine:
    """Получить глобальный Liquidity Map Engine"""
    global _liquidity_map_engine
    if _liquidity_map_engine is None:
        _liquidity_map_engine = LiquidityMapEngine()
    return _liquidity_map_engine


def reset_liquidity_map_engine():
    """Сбросить Liquidity Map Engine (для тестов)"""
    global _liquidity_map_engine
    _liquidity_map_engine = LiquidityMapEngine()
