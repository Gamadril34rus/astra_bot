"""
ASTRA BOT - Microstructure & Order Flow Engine

Расширенный движок микроструктуры рынка и потока ордеров
Приоритетное направление #1

Анализирует:
- Order Book Imbalance (OBI)
- Скорость изменения bid/ask (bid/ask velocity)
- Агрессивные market buys/sells
- Absorption (поглощение ликвидности)
- Spoofing-подобные паттерны
- Ликвидации (liquidation prints)
- Реакция цены на крупные принты

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


class FlowDirection(str, Enum):
    """Направление потока"""
    AGGRESSIVE_BUY = "aggressive_buy"      # Агрессивная покупка (market buy)
    AGGRESSIVE_SELL = "aggressive_sell"    # Агрессивная продажа (market sell)
    PASSIVE_BUY = "passive_buy"           # Пассивная покупка (limit buy)
    PASSIVE_SELL = "passive_sell"         # Пассивная продажа (limit sell)
    NEUTRAL = "neutral"                   # Нейтральный поток


class ImbalanceType(str, Enum):
    """Тип дисбаланса"""
    BID_HEAVY = "bid_heavy"    # Больше объёма на покупку
    ASK_HEAVY = "ask_heavy"    # Больше объёма на продажу
    BALANCED = "balanced"      # Сбалансированный


class AbsorptionType(str, Enum):
    """Тип поглощения"""
    BULLISH_ABSORPTION = "bullish_absorption"  # Поглощение продаж (покупатели сильные)
    BEARISH_ABSORPTION = "bearish_absorption"  # Поглощение покупок (продавцы сильные)
    NEUTRAL_ABSORPTION = "neutral_absorption"  # Нейтральное поглощение


class SpoofingPattern(str, Enum):
    """Паттерны спуфинга"""
    BID_WALL = "bid_wall"          # Стенка на покупку
    ASK_WALL = "ask_wall"          # Стенка на продажу
    FLASHING = "flashing"          # Быстрое появление/исчезновение
    LAYERING = "layering"          # Слоистые ордера
    ICEBERG_SPOOF = "iceberg_spoof"  # Скрытые ордера + спуфинг
    NONE = "none"                  # Нет паттерна


@dataclass
class OrderPrint:
    """Отпечаток ордера (print)"""
    timestamp: datetime
    price: float
    volume: float
    side: str  # bid/ask/buy/sell
    aggressive: bool = False  # Агрессивный (market order)
    liquidation: bool = False  # Ликвидация

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "price": self.price,
            "volume": self.volume,
            "side": self.side,
            "aggressive": self.aggressive,
            "liquidation": self.liquidation,
        }


@dataclass
class OrderBookSnapshot:
    """Снимок стакана заказов"""
    timestamp: datetime
    symbol: str

    # Уровни
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, volume)
    asks: list[tuple[float, float]] = field(default_factory=list)  # (price, volume)

    # Лучшие цены
    best_bid: float = 0.0
    best_ask: float = 0.0

    # Общая глубина
    total_bid_volume: float = 0.0
    total_ask_volume: float = 0.0

    # Дисбаланс
    imbalance: float = 0.0  # (total_bid_volume - total_ask_volume) / (total_bid_volume + total_ask_volume)
    imbalance_type: ImbalanceType = ImbalanceType.BALANCED

    @property
    def spread(self) -> float:
        """Спред между лучшим ask и лучшим bid"""
        if self.best_ask > 0 and self.best_bid > 0:
            return self.best_ask - self.best_bid
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "total_bid_volume": self.total_bid_volume,
            "total_ask_volume": self.total_ask_volume,
            "imbalance": self.imbalance,
            "imbalance_type": self.imbalance_type.value,
            "bids_count": len(self.bids),
            "asks_count": len(self.asks),
        }


@dataclass
class FlowMetrics:
    """Метрики потока ордеров"""
    timestamp: datetime
    symbol: str
    timeframe: str = "1m"

    # Поток
    aggressive_buy_volume: float = 0.0
    aggressive_sell_volume: float = 0.0
    passive_buy_volume: float = 0.0
    passive_sell_volume: float = 0.0

    # Дисбаланс потока
    flow_imbalance: float = 0.0  # (aggressive_buy - aggressive_sell) / (aggressive_buy + aggressive_sell)
    flow_direction: FlowDirection = FlowDirection.NEUTRAL

    # Скорость изменения
    bid_velocity: float = 0.0  # Скорость изменения best bid (цена/секунда)
    ask_velocity: float = 0.0  # Скорость изменения best ask (цена/секунда)
    spread_velocity: float = 0.0  # Скорость изменения спреда

    # Поглощение
    absorption: float = 0.0  # Коэффициент поглощения
    absorption_type: AbsorptionType = AbsorptionType.NEUTRAL_ABSORPTION

    # Ликвидации
    liquidation_buy_volume: float = 0.0
    liquidation_sell_volume: float = 0.0
    total_liquidations: int = 0

    # Крупные принты
    large_buy_prints: int = 0
    large_sell_prints: int = 0
    avg_print_size: float = 0.0

    # Реакция цены
    price_impact: float = 0.0  # Влияние потока на цену
    price_reaction_speed: float = 0.0  # Скорость реакции цены

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "aggressive_buy_volume": self.aggressive_buy_volume,
            "aggressive_sell_volume": self.aggressive_sell_volume,
            "passive_buy_volume": self.passive_buy_volume,
            "passive_sell_volume": self.passive_sell_volume,
            "flow_imbalance": self.flow_imbalance,
            "flow_direction": self.flow_direction.value,
            "bid_velocity": self.bid_velocity,
            "ask_velocity": self.ask_velocity,
            "spread_velocity": self.spread_velocity,
            "absorption": self.absorption,
            "absorption_type": self.absorption_type.value,
            "liquidation_buy_volume": self.liquidation_buy_volume,
            "liquidation_sell_volume": self.liquidation_sell_volume,
            "total_liquidations": self.total_liquidations,
            "large_buy_prints": self.large_buy_prints,
            "large_sell_prints": self.large_sell_prints,
            "avg_print_size": self.avg_print_size,
            "price_impact": self.price_impact,
            "price_reaction_speed": self.price_reaction_speed,
        }


@dataclass
class SpoofingDetection:
    """Обнаружение спуфинга"""
    timestamp: datetime
    symbol: str

    # Паттерны
    pattern: SpoofingPattern = SpoofingPattern.NONE

    # Детали
    wall_price: float = 0.0
    wall_volume: float = 0.0
    wall_side: str = "bid"  # bid/ask

    # Время жизни стены
    wall_duration_seconds: float = 0.0

    # Результат
    price_reaction: float = 0.0  # Реакция цены
    volume_removed: float = 0.0  # Объём, который исчез

    # Уверенность
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "pattern": self.pattern.value,
            "wall_price": self.wall_price,
            "wall_volume": self.wall_volume,
            "wall_side": self.wall_side,
            "wall_duration_seconds": self.wall_duration_seconds,
            "price_reaction": self.price_reaction,
            "volume_removed": self.volume_removed,
            "confidence": self.confidence,
        }


@dataclass
class MicrostructureAnalysis:
    """Полный анализ микроструктуры"""
    symbol: str
    timestamp: datetime
    timeframe: str = "1m"

    # Снимки стакана
    snapshots: list[OrderBookSnapshot] = field(default_factory=list)

    # Метрики потока
    flow_metrics: FlowMetrics | None = None

    # Обнаруженные паттерны
    spoofing_detections: list[SpoofingDetection] = field(default_factory=list)

    # Сигналы
    signals: list[str] = field(default_factory=list)

    # Уверенность
    confidence: float = 0.0

    # Рекомендации
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "snapshots_count": len(self.snapshots),
            "flow_metrics": self.flow_metrics.to_dict() if self.flow_metrics else None,
            "spoofing_detections": [s.to_dict() for s in self.spoofing_detections],
            "signals": self.signals,
            "confidence": self.confidence,
            "recommendations": self.recommendations,
        }


class MicrostructureFlowEngine:
    """
    Движок анализа микроструктуры и потока ордеров.

    Отслеживает order book dynamics и detect агрессивные потоки.
    """

    def __init__(self):
        # История снимков
        self._snapshots: dict[str, deque[OrderBookSnapshot]] = {}

        # История принтов
        self._prints: dict[str, deque[OrderPrint]] = {}

        # Последние метрики
        self._last_metrics: dict[str, FlowMetrics] = {}

        # Пороги
        self.thresholds = {
            "large_print_threshold": 0.01,  # 1% от среднего объёма
            "aggressive_flow_threshold": 0.6,  # 60% агрессивный поток
            "imbalance_threshold": 0.3,  # 30% дисбаланс
            "absorption_threshold": 0.5,  # 50% поглощение
            "spoofing_wall_size": 0.05,  # 5% от общей глубины
            "spoofing_duration_max": 10.0,  # 10 секунд
            "velocity_window": 5,  # Окно для скорости
        }

        # Параметры
        self.window_size = 100  # Размер окна для анализа

    def add_order_book_snapshot(
        self,
        symbol: str,
        timestamp: datetime,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
    ) -> OrderBookSnapshot:
        """
        Добавить снимок стакана.

        Args:
            symbol: Символ
            timestamp: Временная метка
            bids: Список (цена, объём) для покупок
            asks: Список (цена, объём) для продаж

        Returns:
            Снимок стакана
        """
        # Создать снимок
        snapshot = OrderBookSnapshot(
            timestamp=timestamp,
            symbol=symbol,
        )

        snapshot.bids = sorted(bids, key=lambda x: x[0], reverse=True)
        snapshot.asks = sorted(asks, key=lambda x: x[0])

        # Рассчитать лучшие цены
        if snapshot.bids:
            snapshot.best_bid = snapshot.bids[0][0]
            snapshot.total_bid_volume = sum(v for _, v in snapshot.bids)

        if snapshot.asks:
            snapshot.best_ask = snapshot.asks[0][0]
            snapshot.total_ask_volume = sum(v for _, v in snapshot.asks)

        # Рассчитать дисбаланс
        total_volume = snapshot.total_bid_volume + snapshot.total_ask_volume
        if total_volume > 0:
            snapshot.imbalance = (snapshot.total_bid_volume - snapshot.total_ask_volume) / total_volume

        # Определить тип дисбаланса
        if snapshot.imbalance > self.thresholds["imbalance_threshold"]:
            snapshot.imbalance_type = ImbalanceType.BID_HEAVY
        elif snapshot.imbalance < -self.thresholds["imbalance_threshold"]:
            snapshot.imbalance_type = ImbalanceType.ASK_HEAVY
        else:
            snapshot.imbalance_type = ImbalanceType.BALANCED

        # Сохранить
        if symbol not in self._snapshots:
            self._snapshots[symbol] = deque(maxlen=self.window_size)
        self._snapshots[symbol].append(snapshot)

        return snapshot

    def add_order_print(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        volume: float,
        side: str,
        aggressive: bool = False,
        liquidation: bool = False,
    ) -> OrderPrint:
        """
        Добавить отпечаток ордера.

        Args:
            symbol: Символ
            timestamp: Временная метка
            price: Цена
            volume: Объём
            side: Сторона (bid/ask/buy/sell)
            aggressive: Агрессивный ордер
            liquidation: Ликвидация

        Returns:
            Отпечаток ордера
        """
        print_obj = OrderPrint(
            timestamp=timestamp,
            price=price,
            volume=volume,
            side=side,
            aggressive=aggressive,
            liquidation=liquidation,
        )

        # Сохранить
        if symbol not in self._prints:
            self._prints[symbol] = deque(maxlen=self.window_size * 10)
        self._prints[symbol].append(print_obj)

        return print_obj

    def calculate_flow_metrics(
        self,
        symbol: str,
        timestamp: datetime,
        timeframe: str = "1m",
    ) -> FlowMetrics:
        """
        Рассчитать метрики потока ордеров.

        Args:
            symbol: Символ
            timestamp: Временная метка
            timeframe: Временной горизонт

        Returns:
            Метрики потока
        """
        if symbol not in self._prints or not self._prints[symbol]:
            return FlowMetrics(
                timestamp=timestamp,
                symbol=symbol,
                timeframe=timeframe,
            )

        # Получить принты за интервал
        prints = list(self._prints[symbol])

        # Рассчитать объёмы
        aggressive_buy = sum(p.volume for p in prints if p.aggressive and p.side in ["buy", "bid"])
        aggressive_sell = sum(p.volume for p in prints if p.aggressive and p.side in ["sell", "ask"])
        passive_buy = sum(p.volume for p in prints if not p.aggressive and p.side in ["buy", "bid"])
        passive_sell = sum(p.volume for p in prints if not p.aggressive and p.side in ["sell", "ask"])

        # Рассчитать дисбаланс потока
        total_aggressive = aggressive_buy + aggressive_sell
        if total_aggressive > 0:
            flow_imbalance = (aggressive_buy - aggressive_sell) / total_aggressive
        else:
            flow_imbalance = 0.0

        # Определить направление потока
        if flow_imbalance > self.thresholds["aggressive_flow_threshold"]:
            flow_direction = FlowDirection.AGGRESSIVE_BUY
        elif flow_imbalance < -self.thresholds["aggressive_flow_threshold"]:
            flow_direction = FlowDirection.AGGRESSIVE_SELL
        elif aggressive_buy > aggressive_sell:
            flow_direction = FlowDirection.AGGRESSIVE_BUY
        elif aggressive_sell > aggressive_buy:
            flow_direction = FlowDirection.AGGRESSIVE_SELL
        else:
            flow_direction = FlowDirection.NEUTRAL

        # Рассчитать скорость изменения bid/ask
        if symbol in self._snapshots and len(self._snapshots[symbol]) >= 2:
            snapshots = list(self._snapshots[symbol])
            time_deltas = [(snapshots[i].timestamp - snapshots[i-1].timestamp).total_seconds()
                          for i in range(1, len(snapshots))]

            if time_deltas:
                bid_deltas = [snapshots[i].best_bid - snapshots[i-1].best_bid
                             for i in range(1, len(snapshots))]
                ask_deltas = [snapshots[i].best_ask - snapshots[i-1].best_ask
                             for i in range(1, len(snapshots))]
                spread_deltas = [snapshots[i].spread - snapshots[i-1].spread
                               for i in range(1, len(snapshots))]

                avg_time_delta = sum(time_deltas) / len(time_deltas)
                if avg_time_delta > 0:
                    bid_velocity = sum(bid_deltas) / sum(time_deltas)
                    ask_velocity = sum(ask_deltas) / sum(time_deltas)
                    spread_velocity = sum(spread_deltas) / sum(time_deltas)
                else:
                    bid_velocity = 0.0
                    ask_velocity = 0.0
                    spread_velocity = 0.0
            else:
                bid_velocity = 0.0
                ask_velocity = 0.0
                spread_velocity = 0.0
        else:
            bid_velocity = 0.0
            ask_velocity = 0.0
            spread_velocity = 0.0

        # Рассчитать поглощение
        absorption = self._calculate_absorption(symbol)

        # Рассчитать ликвидации
        liquidation_buy = sum(p.volume for p in prints if p.liquidation and p.side in ["buy", "bid"])
        liquidation_sell = sum(p.volume for p in prints if p.liquidation and p.side in ["sell", "ask"])
        total_liquidations = sum(1 for p in prints if p.liquidation)

        # Рассчитать крупные принты
        if prints:
            avg_volume = sum(p.volume for p in prints) / len(prints)
            large_threshold = avg_volume * self.thresholds["large_print_threshold"]
            large_buy = sum(1 for p in prints if p.volume >= large_threshold and p.side in ["buy", "bid"])
            large_sell = sum(1 for p in prints if p.volume >= large_threshold and p.side in ["sell", "ask"])
            avg_print_size = avg_volume
        else:
            large_buy = 0
            large_sell = 0
            avg_print_size = 0.0

        # Рассчитать влияние на цену
        if symbol in self._snapshots and len(self._snapshots[symbol]) >= 2:
            price_deltas = [snapshots[i].best_bid - snapshots[i-1].best_bid
                           for i in range(1, len(snapshots))]
            price_impact = sum(price_deltas) / len(price_deltas) if price_deltas else 0.0
        else:
            price_impact = 0.0

        # Рассчитать скорость реакции цены
        if prints and len(prints) >= 2:
            price_reaction_speed = abs(price_impact) / sum(p.volume for p in prints)
        else:
            price_reaction_speed = 0.0

        # Определить тип поглощения
        if absorption > self.thresholds["absorption_threshold"]:
            absorption_type = AbsorptionType.BULLISH_ABSORPTION
        elif absorption < -self.thresholds["absorption_threshold"]:
            absorption_type = AbsorptionType.BEARISH_ABSORPTION
        else:
            absorption_type = AbsorptionType.NEUTRAL_ABSORPTION

        return FlowMetrics(
            timestamp=timestamp,
            symbol=symbol,
            timeframe=timeframe,
            aggressive_buy_volume=aggressive_buy,
            aggressive_sell_volume=aggressive_sell,
            passive_buy_volume=passive_buy,
            passive_sell_volume=passive_sell,
            flow_imbalance=flow_imbalance,
            flow_direction=flow_direction,
            bid_velocity=bid_velocity,
            ask_velocity=ask_velocity,
            spread_velocity=spread_velocity,
            absorption=absorption,
            absorption_type=absorption_type,
            liquidation_buy_volume=liquidation_buy,
            liquidation_sell_volume=liquidation_sell,
            total_liquidations=total_liquidations,
            large_buy_prints=large_buy,
            large_sell_prints=large_sell,
            avg_print_size=avg_print_size,
            price_impact=price_impact,
            price_reaction_speed=price_reaction_speed,
        )

    def _calculate_absorption(self, symbol: str) -> float:
        """
        Рассчитать поглощение ликвидности.

        Args:
            symbol: Символ

        Returns:
            Коэффициент поглощения
        """
        if symbol not in self._snapshots or len(self._snapshots[symbol]) < 2:
            return 0.0

        snapshots = list(self._snapshots[symbol])

        # Сравнить изменения объёма и цены
        volume_changes = []
        price_changes = []

        for i in range(1, len(snapshots)):
            bid_volume_change = snapshots[i].total_bid_volume - snapshots[i-1].total_bid_volume
            ask_volume_change = snapshots[i].total_ask_volume - snapshots[i-1].total_ask_volume
            price_change = snapshots[i].best_bid - snapshots[i-1].best_bid

            volume_changes.append(bid_volume_change + ask_volume_change)
            price_changes.append(price_change)

        if not volume_changes or sum(abs(v) for v in volume_changes) == 0:
            return 0.0

        # Поглощение = изменение цены / изменение объёма
        # Если цена растёт при уменьшении объёма на покупку → поглощение
        absorption = 0.0
        for i in range(len(volume_changes)):
            if volume_changes[i] < 0 and price_changes[i] > 0:
                absorption += 1.0  # Поглощение продаж
            elif volume_changes[i] > 0 and price_changes[i] < 0:
                absorption -= 1.0  # Поглощение покупок

        return absorption / len(volume_changes) if volume_changes else 0.0

    def detect_spoofing(
        self,
        symbol: str,
        timestamp: datetime,
    ) -> list[SpoofingDetection]:
        """
        Обнаружить паттерны спуфинга.

        Args:
            symbol: Символ
            timestamp: Временная метка

        Returns:
            Список обнаруженных паттернов
        """
        detections = []

        if symbol not in self._snapshots or len(self._snapshots[symbol]) < 3:
            return detections

        snapshots = list(self._snapshots[symbol])

        # Проверить стены
        for i in range(1, len(snapshots)):
            # Стенка на покупку
            if snapshots[i].bids and snapshots[i-1].bids:
                current_bid_vol = snapshots[i].bids[0][1]
                prev_bid_vol = snapshots[i-1].bids[0][1]

                # Если объём вырос больше порога
                if current_bid_vol >= self.thresholds["spoofing_wall_size"] * snapshots[i].total_bid_volume:
                    # Проверить, исчезла ли стена
                    if i + 1 < len(snapshots):
                        next_bid_vol = snapshots[i+1].bids[0][1] if snapshots[i+1].bids else 0
                        if next_bid_vol < current_bid_vol * 0.5:  # Уменьшилась в 2 раза
                            duration = (snapshots[i+1].timestamp - snapshots[i].timestamp).total_seconds()
                            if duration <= self.thresholds["spoofing_duration_max"]:
                                price_reaction = snapshots[i+1].best_bid - snapshots[i].best_bid
                                detections.append(SpoofingDetection(
                                    timestamp=timestamp,
                                    symbol=symbol,
                                    pattern=SpoofingPattern.BID_WALL,
                                    wall_price=snapshots[i].bids[0][0],
                                    wall_volume=current_bid_vol,
                                    wall_side="bid",
                                    wall_duration_seconds=duration,
                                    price_reaction=price_reaction,
                                    volume_removed=current_bid_vol - next_bid_vol,
                                    confidence=min(1.0, duration / self.thresholds["spoofing_duration_max"]),
                                ))

            # Стенка на продажу
            if snapshots[i].asks and snapshots[i-1].asks:
                current_ask_vol = snapshots[i].asks[0][1]
                prev_ask_vol = snapshots[i-1].asks[0][1]

                if current_ask_vol >= self.thresholds["spoofing_wall_size"] * snapshots[i].total_ask_volume:
                    if i + 1 < len(snapshots):
                        next_ask_vol = snapshots[i+1].asks[0][1] if snapshots[i+1].asks else 0
                        if next_ask_vol < current_ask_vol * 0.5:
                            duration = (snapshots[i+1].timestamp - snapshots[i].timestamp).total_seconds()
                            if duration <= self.thresholds["spoofing_duration_max"]:
                                price_reaction = snapshots[i+1].best_ask - snapshots[i].best_ask
                                detections.append(SpoofingDetection(
                                    timestamp=timestamp,
                                    symbol=symbol,
                                    pattern=SpoofingPattern.ASK_WALL,
                                    wall_price=snapshots[i].asks[0][0],
                                    wall_volume=current_ask_vol,
                                    wall_side="ask",
                                    wall_duration_seconds=duration,
                                    price_reaction=price_reaction,
                                    volume_removed=current_ask_vol - next_ask_vol,
                                    confidence=min(1.0, duration / self.thresholds["spoofing_duration_max"]),
                                ))

        return detections

    def analyze_microstructure(
        self,
        symbol: str,
        timestamp: datetime,
        timeframe: str = "1m",
    ) -> MicrostructureAnalysis:
        """
        Полный анализ микроструктуры.

        Args:
            symbol: Символ
            timestamp: Временная метка
            timeframe: Временной горизонт

        Returns:
            Полный анализ
        """
        # Получить последние снимки
        snapshots = list(self._snapshots.get(symbol, []))

        # Рассчитать метрики потока
        flow_metrics = self.calculate_flow_metrics(symbol, timestamp, timeframe)

        # Обнаружить спуфинг
        spoofing_detections = self.detect_spoofing(symbol, timestamp)

        # Сгенерировать сигналы
        signals = self._generate_signals(symbol, flow_metrics, spoofing_detections)

        # Сгенерировать рекомендации
        recommendations = self._generate_recommendations(symbol, flow_metrics, spoofing_detections)

        # Рассчитать уверенность
        confidence = self._calculate_confidence(flow_metrics, spoofing_detections)

        return MicrostructureAnalysis(
            symbol=symbol,
            timestamp=timestamp,
            timeframe=timeframe,
            snapshots=snapshots,
            flow_metrics=flow_metrics,
            spoofing_detections=spoofing_detections,
            signals=signals,
            confidence=confidence,
            recommendations=recommendations,
        )

    def _generate_signals(
        self,
        symbol: str,
        flow_metrics: FlowMetrics,
        spoofing_detections: list[SpoofingDetection],
    ) -> list[str]:
        """Сгенерировать сигналы"""
        signals = []

        # Сигналы по потоку
        if flow_metrics.flow_direction == FlowDirection.AGGRESSIVE_BUY:
            signals.append("AGGRESSIVE_BUY_FLOW")
        elif flow_metrics.flow_direction == FlowDirection.AGGRESSIVE_SELL:
            signals.append("AGGRESSIVE_SELL_FLOW")

        # Сигналы по дисбалансу
        if flow_metrics.flow_imbalance > self.thresholds["imbalance_threshold"]:
            signals.append("BULLISH_IMBALANCE")
        elif flow_metrics.flow_imbalance < -self.thresholds["imbalance_threshold"]:
            signals.append("BEARISH_IMBALANCE")

        # Сигналы по поглощению
        if flow_metrics.absorption_type == AbsorptionType.BULLISH_ABSORPTION:
            signals.append("BULLISH_ABSORPTION")
        elif flow_metrics.absorption_type == AbsorptionType.BEARISH_ABSORPTION:
            signals.append("BEARISH_ABSORPTION")

        # Сигналы по ликвидациям
        if flow_metrics.liquidation_sell_volume > flow_metrics.liquidation_buy_volume:
            signals.append("BEARISH_LIQUIDATIONS")
        elif flow_metrics.liquidation_buy_volume > flow_metrics.liquidation_sell_volume:
            signals.append("BULLISH_LIQUIDATIONS")

        # Сигналы по спуфингу
        for detection in spoofing_detections:
            if detection.pattern == SpoofingPattern.BID_WALL:
                signals.append("BID_WALL_DETECTED")
            elif detection.pattern == SpoofingPattern.ASK_WALL:
                signals.append("ASK_WALL_DETECTED")

        # Сигналы по скорости
        if flow_metrics.bid_velocity > 0 and flow_metrics.ask_velocity > 0:
            signals.append("PRICE_RISING_FAST")
        elif flow_metrics.bid_velocity < 0 and flow_metrics.ask_velocity < 0:
            signals.append("PRICE_FALLING_FAST")

        if not signals:
            signals.append("NEUTRAL")

        return signals

    def _generate_recommendations(
        self,
        symbol: str,
        flow_metrics: FlowMetrics,
        spoofing_detections: list[SpoofingDetection],
    ) -> list[str]:
        """Сгенерировать рекомендации"""
        recommendations = []

        # Рекомендации по потоку
        if flow_metrics.flow_direction == FlowDirection.AGGRESSIVE_BUY:
            recommendations.append("Strong buying pressure detected")
        elif flow_metrics.flow_direction == FlowDirection.AGGRESSIVE_SELL:
            recommendations.append("Strong selling pressure detected")

        # Рекомендации по поглощению
        if flow_metrics.absorption_type == AbsorptionType.BULLISH_ABSORPTION:
            recommendations.append("Bullish absorption: sellers are being absorbed")
        elif flow_metrics.absorption_type == AbsorptionType.BEARISH_ABSORPTION:
            recommendations.append("Bearish absorption: buyers are being absorbed")

        # Рекомендации по ликвидациям
        if flow_metrics.total_liquidations > 0:
            recommendations.append(f"{flow_metrics.total_liquidations} liquidations detected")

        # Рекомендации по спуфингу
        for detection in spoofing_detections:
            if detection.pattern != SpoofingPattern.NONE:
                recommendations.append(f"Spoofing pattern: {detection.pattern.value}")

        if not recommendations:
            recommendations.append("No significant microstructure signals")

        return recommendations

    def _calculate_confidence(
        self,
        flow_metrics: FlowMetrics,
        spoofing_detections: list[SpoofingDetection],
    ) -> float:
        """Рассчитать уверенность"""
        confidence = 0.5

        # Учесть направление потока
        if flow_metrics.flow_direction != FlowDirection.NEUTRAL:
            confidence += 0.1

        # Учесть дисбаланс
        if abs(flow_metrics.flow_imbalance) > self.thresholds["imbalance_threshold"]:
            confidence += 0.1

        # Учесть поглощение
        if flow_metrics.absorption_type != AbsorptionType.NEUTRAL_ABSORPTION:
            confidence += 0.1

        # Учесть ликвидации
        if flow_metrics.total_liquidations > 0:
            confidence += 0.1

        # Учесть спуфинг
        if spoofing_detections:
            confidence += 0.1

        return min(1.0, confidence)


# Глобальный экземпляр
_microstructure_flow_engine: MicrostructureFlowEngine | None = None


def get_microstructure_flow_engine() -> MicrostructureFlowEngine:
    """Получить глобальный Microstructure Flow Engine"""
    global _microstructure_flow_engine
    if _microstructure_flow_engine is None:
        _microstructure_flow_engine = MicrostructureFlowEngine()
    return _microstructure_flow_engine


def reset_microstructure_flow_engine():
    """Сбросить Microstructure Flow Engine (для тестов)"""
    global _microstructure_flow_engine
    _microstructure_flow_engine = MicrostructureFlowEngine()
