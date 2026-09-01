"""
ASTRA BOT - Market Microstructure Engine

Движок анализа микроструктуры рынка (ТЗ Пункт 3)

Изучает:
- bid/ask spread
- spread changes
- order book imbalance
- bid/ask depth
- depth changes
- aggressive buy/sell pressure
- trade flow
- volume imbalance
- cumulative delta
- large trades
- liquidity gaps
- liquidity sweep
- absorption
- spoofing-like patterns
- sudden liquidity withdrawal
- spread widening
- volatility expansion
- volatility compression
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

from ...core import models

logger = logging.getLogger(__name__)


@dataclass
class OrderBookSnapshot:
    """Снимок стакана"""
    symbol: str
    timestamp: datetime
    bids: list[tuple[float, float]]  # (price, quantity)
    asks: list[tuple[float, float]]  # (price, quantity)
    best_bid: float
    best_ask: float
    spread: float
    spread_pct: float
    mid_price: float
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "mid_price": self.mid_price,
            "bids_count": len(self.bids),
            "asks_count": len(self.asks),
        }


@dataclass
class OrderBookImbalance:
    """Дисбаланс стакана"""
    symbol: str
    timestamp: datetime
    bid_volume: float
    ask_volume: float
    imbalance: float  # (bid_volume - ask_volume) / (bid_volume + ask_volume)
    imbalance_pct: float  # Процентный дисбаланс
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "bid_volume": self.bid_volume,
            "ask_volume": self.ask_volume,
            "imbalance": self.imbalance,
            "imbalance_pct": self.imbalance_pct,
        }


@dataclass
class AggressivePressure:
    """Агрессивное давление"""
    symbol: str
    timestamp: datetime
    buy_pressure: float  # Объём покупок по market
    sell_pressure: float  # Объём продаж по market
    net_pressure: float  # Чистое давление
    pressure_ratio: float  # Отношение buy/sell
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "buy_pressure": self.buy_pressure,
            "sell_pressure": self.sell_pressure,
            "net_pressure": self.net_pressure,
            "pressure_ratio": self.pressure_ratio,
        }


@dataclass
class LiquidityMetrics:
    """Метрики ликвидности"""
    symbol: str
    timestamp: datetime
    bid_depth: float  # Глубина бидов
    ask_depth: float  # Глубина асков
    total_depth: float  # Общая глубина
    depth_imbalance: float  # Дисбаланс глубины
    liquidity_gaps: list[tuple[float, float]]  # Интервалы без ликвидности
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "bid_depth": self.bid_depth,
            "ask_depth": self.ask_depth,
            "total_depth": self.total_depth,
            "depth_imbalance": self.depth_imbalance,
            "liquidity_gaps_count": len(self.liquidity_gaps),
        }


@dataclass
class TradeFlowMetrics:
    """Метрики потока сделок"""
    symbol: str
    timestamp: datetime
    buy_volume: float
    sell_volume: float
    net_volume: float
    volume_imbalance: float
    cumulative_delta: float  # Накопленный дельта
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "net_volume": self.net_volume,
            "volume_imbalance": self.volume_imbalance,
            "cumulative_delta": self.cumulative_delta,
        }


@dataclass
class MicrostructureAnalysis:
    """Полный анализ микроструктуры"""
    symbol: str
    timestamp: datetime
    snapshot: OrderBookSnapshot
    imbalance: OrderBookImbalance
    aggressive_pressure: AggressivePressure
    liquidity: LiquidityMetrics
    trade_flow: TradeFlowMetrics
    spread_analysis: dict[str, Any]
    volatility_analysis: dict[str, Any]
    patterns: dict[str, Any]
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "snapshot": self.snapshot.to_dict(),
            "imbalance": self.imbalance.to_dict(),
            "aggressive_pressure": self.aggressive_pressure.to_dict(),
            "liquidity": self.liquidity.to_dict(),
            "trade_flow": self.trade_flow.to_dict(),
            "spread_analysis": self.spread_analysis,
            "volatility_analysis": self.volatility_analysis,
            "patterns": self.patterns,
        }


class MarketMicrostructureEngine:
    """
    Движок анализа микроструктуры рынка.
    
    Анализирует стакан, поток ордеров и другие аспекты микроструктуры.
    """
    
    def __init__(self):
        # История для анализа изменений
        self._history: dict[str, list[OrderBookSnapshot]] = {}
        self._trade_history: dict[str, list[TradeFlowMetrics]] = {}
        
        # Пороги для обнаружения паттернов
        self.thresholds = {
            "spread_widening": 0.05,  # 5% увеличение spread
            "liquidity_withdrawal": 0.3,  # 30% уменьшение ликвидности
            "imbalance_extreme": 0.7,  # 70% дисбаланс
            "pressure_extreme": 3.0,  # Отношение buy/sell > 3
            "large_trade_threshold": 0.01,  # 1% от среднего объема
        }
    
    def analyze_order_book(
        self,
        symbol: str,
        orderbook: models.OrderBook,
        current_price: float | None = None,
        timestamp: datetime | None = None,
    ) -> OrderBookSnapshot:
        """
        Проанализировать стакан.
        
        Args:
            symbol: Символ
            orderbook: Объект стакана
            current_price: Текущая цена
            timestamp: Временная метка
        
        Returns:
            Снимок стакана
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        if current_price is None:
            current_price = (orderbook.best_bid + orderbook.best_ask) / 2 if orderbook.best_bid and orderbook.best_ask else 0
        
        # Получение бидов и асков
        bids = [(float(b.price), float(b.quantity)) for b in orderbook.bids] if orderbook.bids else []
        asks = [(float(a.price), float(a.quantity)) for a in orderbook.asks] if orderbook.asks else []
        
        # Best bid/ask
        best_bid = bids[0][0] if bids else 0
        best_ask = asks[0][0] if asks else 0
        
        # Spread
        spread = best_ask - best_bid if best_ask and best_bid else 0
        spread_pct = (spread / current_price * 100) if current_price > 0 else 0
        
        # Mid price
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else current_price
        
        snapshot = OrderBookSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            spread_pct=spread_pct,
            mid_price=mid_price,
        )
        
        # Сохранить в историю
        if symbol not in self._history:
            self._history[symbol] = []
        self._history[symbol].append(snapshot)
        
        # Ограничить историю
        if len(self._history[symbol]) > 1000:
            self._history[symbol] = self._history[symbol][-1000:]
        
        return snapshot
    
    def calculate_imbalance(
        self,
        symbol: str,
        orderbook: models.OrderBook | None = None,
        snapshot: OrderBookSnapshot | None = None,
        depth_levels: int = 5,
    ) -> OrderBookImbalance:
        """
        Рассчитать дисбаланс стакана.
        
        Args:
            symbol: Символ
            orderbook: Объект стакана
            snapshot: Снимок стакана
            depth_levels: Количество уровней для анализа
        
        Returns:
            Дисбаланс стакана
        """
        if snapshot is None and orderbook is None:
            raise ValueError("Either orderbook or snapshot must be provided")
        
        if snapshot is None:
            snapshot = self.analyze_order_book(symbol, orderbook)
        
        # Рассчитать объём бидов и асков
        bid_volume = sum(q for _, q in snapshot.bids[:depth_levels])
        ask_volume = sum(q for _, q in snapshot.asks[:depth_levels])
        total_volume = bid_volume + ask_volume
        
        if total_volume > 0:
            imbalance = (bid_volume - ask_volume) / total_volume
            imbalance_pct = imbalance * 100
        else:
            imbalance = 0
            imbalance_pct = 0
        
        return OrderBookImbalance(
            symbol=symbol,
            timestamp=snapshot.timestamp,
            bid_volume=bid_volume,
            ask_volume=ask_volume,
            imbalance=imbalance,
            imbalance_pct=imbalance_pct,
        )
    
    def calculate_aggressive_pressure(
        self,
        symbol: str,
        trades: list[dict[str, Any]],
        timestamp: datetime | None = None,
    ) -> AggressivePressure:
        """
        Рассчитать агрессивное давление.
        
        Args:
            symbol: Символ
            trades: Список сделок
            timestamp: Временная метка
        
        Returns:
            Агрессивное давление
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        buy_volume = 0
        sell_volume = 0
        
        for trade in trades:
            side = trade.get("side", "").lower()
            quantity = float(trade.get("quantity", 0))
            
            if side in ["buy", "bid"]:
                buy_volume += quantity
            elif side in ["sell", "ask"]:
                sell_volume += quantity
        
        net_pressure = buy_volume - sell_volume
        pressure_ratio = buy_volume / sell_volume if sell_volume > 0 else float('inf')
        
        return AggressivePressure(
            symbol=symbol,
            timestamp=timestamp,
            buy_pressure=buy_volume,
            sell_pressure=sell_volume,
            net_pressure=net_pressure,
            pressure_ratio=pressure_ratio,
        )
    
    def calculate_liquidity_metrics(
        self,
        symbol: str,
        orderbook: models.OrderBook | None = None,
        snapshot: OrderBookSnapshot | None = None,
    ) -> LiquidityMetrics:
        """
        Рассчитать метрики ликвидности.
        
        Args:
            symbol: Символ
            orderbook: Объект стакана
            snapshot: Снимок стакана
        
        Returns:
            Метрики ликвидности
        """
        if snapshot is None and orderbook is None:
            raise ValueError("Either orderbook or snapshot must be provided")
        
        if snapshot is None:
            snapshot = self.analyze_order_book(symbol, orderbook)
        
        # Глубина бидов и асков
        bid_depth = sum(q for _, q in snapshot.bids)
        ask_depth = sum(q for _, q in snapshot.asks)
        total_depth = bid_depth + ask_depth
        
        # Дисбаланс глубины
        if total_depth > 0:
            depth_imbalance = (bid_depth - ask_depth) / total_depth
        else:
            depth_imbalance = 0
        
        # Поиск разрывов в ликвидности
        liquidity_gaps = []
        if snapshot.bids and snapshot.asks:
            # Сортировать по цене
            sorted_bids = sorted(snapshot.bids, key=lambda x: x[0], reverse=True)
            sorted_asks = sorted(snapshot.asks, key=lambda x: x[0])
            
            # Найти разрывы между best bid и best ask
            if sorted_bids and sorted_asks:
                gap = sorted_asks[0][0] - sorted_bids[0][0]
                if gap > 0:
                    liquidity_gaps.append((sorted_bids[0][0], sorted_asks[0][0]))
        
        return LiquidityMetrics(
            symbol=symbol,
            timestamp=snapshot.timestamp,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            total_depth=total_depth,
            depth_imbalance=depth_imbalance,
            liquidity_gaps=liquidity_gaps,
        )
    
    def calculate_trade_flow(
        self,
        symbol: str,
        trades: list[dict[str, Any]],
        timestamp: datetime | None = None,
    ) -> TradeFlowMetrics:
        """
        Рассчитать метрики потока сделок.
        
        Args:
            symbol: Символ
            trades: Список сделок
            timestamp: Временная метка
        
        Returns:
            Метрики потока сделок
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        buy_volume = 0
        sell_volume = 0
        cumulative_delta = 0
        
        for trade in trades:
            side = trade.get("side", "").lower()
            quantity = float(trade.get("quantity", 0))
            price = float(trade.get("price", 0))
            
            if side in ["buy", "bid"]:
                buy_volume += quantity
                cumulative_delta += quantity
            elif side in ["sell", "ask"]:
                sell_volume += quantity
                cumulative_delta -= quantity
        
        net_volume = buy_volume - sell_volume
        total_volume = buy_volume + sell_volume
        
        if total_volume > 0:
            volume_imbalance = net_volume / total_volume
        else:
            volume_imbalance = 0
        
        metrics = TradeFlowMetrics(
            symbol=symbol,
            timestamp=timestamp,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            net_volume=net_volume,
            volume_imbalance=volume_imbalance,
            cumulative_delta=cumulative_delta,
        )
        
        # Сохранить в историю
        if symbol not in self._trade_history:
            self._trade_history[symbol] = []
        self._trade_history[symbol].append(metrics)
        
        if len(self._trade_history[symbol]) > 1000:
            self._trade_history[symbol] = self._trade_history[symbol][-1000:]
        
        return metrics
    
    def detect_spread_widening(
        self,
        symbol: str,
        current_spread_pct: float,
        lookback_periods: int = 10,
    ) -> bool:
        """
        Обнаружить расширение spread.
        
        Args:
            symbol: Символ
            current_spread_pct: Текущий spread в %
            lookback_periods: Количество периодов для анализа
        
        Returns:
            True если spread значительно расширился
        """
        if symbol not in self._history or len(self._history[symbol]) < lookback_periods + 1:
            return False
        
        history = self._history[symbol][-lookback_periods-1:-1]
        if not history:
            return False
        
        avg_spread = np.mean([s.spread_pct for s in history])
        
        if avg_spread > 0:
            spread_increase_pct = (current_spread_pct - avg_spread) / avg_spread * 100
            return spread_increase_pct > self.thresholds["spread_widening"]
        
        return False
    
    def detect_liquidity_withdrawal(
        self,
        symbol: str,
        current_depth: float,
        lookback_periods: int = 10,
    ) -> bool:
        """
        Обнаружить изъятие ликвидности.
        
        Args:
            symbol: Символ
            current_depth: Текущая глубина
            lookback_periods: Количество периодов для анализа
        
        Returns:
            True если ликвидность значительно уменьшилась
        """
        if symbol not in self._history or len(self._history[symbol]) < lookback_periods + 1:
            return False
        
        history = self._history[symbol][-lookback_periods-1:-1]
        if not history:
            return False
        
        # Рассчитать глубину из истории
        depths = []
        for snapshot in history:
            depth = sum(q for _, q in snapshot.bids) + sum(q for _, q in snapshot.asks)
            depths.append(depth)
        
        if depths:
            avg_depth = np.mean(depths)
            if avg_depth > 0:
                depth_decrease_pct = (avg_depth - current_depth) / avg_depth * 100
                return depth_decrease_pct > self.thresholds["liquidity_withdrawal"] * 100
        
        return False
    
    def detect_extreme_imbalance(
        self,
        imbalance: float,
    ) -> bool:
        """
        Обнаружить экстремальный дисбаланс.
        
        Args:
            imbalance: Текущий дисбаланс (-1 до 1)
        
        Returns:
            True если дисбаланс экстремальный
        """
        return abs(imbalance) > self.thresholds["imbalance_extreme"]
    
    def detect_extreme_pressure(
        self,
        pressure_ratio: float,
    ) -> bool:
        """
        Обнаружить экстремальное давление.
        
        Args:
            pressure_ratio: Отношение buy/sell давления
        
        Returns:
            True если давление экстремальное
        """
        return pressure_ratio > self.thresholds["pressure_extreme"] or pressure_ratio < 1/self.thresholds["pressure_extreme"]
    
    def analyze_microstructure(
        self,
        symbol: str,
        orderbook: models.OrderBook,
        trades: list[dict[str, Any]],
        current_price: float | None = None,
        timestamp: datetime | None = None,
    ) -> MicrostructureAnalysis:
        """
        Полный анализ микроструктуры.
        
        Args:
            symbol: Символ
            orderbook: Объект стакана
            trades: Список сделок
            current_price: Текущая цена
            timestamp: Временная метка
        
        Returns:
            Полный анализ микроструктуры
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        # Анализ стакана
        snapshot = self.analyze_order_book(symbol, orderbook, current_price, timestamp)
        
        # Дисбаланс
        imbalance = self.calculate_imbalance(symbol, snapshot=snapshot)
        
        # Агрессивное давление
        aggressive_pressure = self.calculate_aggressive_pressure(symbol, trades, timestamp)
        
        # Ликвидность
        liquidity = self.calculate_liquidity_metrics(symbol, snapshot=snapshot)
        
        # Поток сделок
        trade_flow = self.calculate_trade_flow(symbol, trades, timestamp)
        
        # Анализ spread
        spread_analysis = {
            "current_spread_pct": snapshot.spread_pct,
            "spread_widening": self.detect_spread_widening(symbol, snapshot.spread_pct),
        }
        
        # Анализ волатильности (на основе spread)
        if symbol in self._history and len(self._history[symbol]) > 1:
            recent_spreads = [s.spread_pct for s in self._history[symbol][-10:]]
            volatility_analysis = {
                "spread_volatility": float(np.std(recent_spreads)) if recent_spreads else 0,
                "spread_trend": "increasing" if recent_spreads and recent_spreads[-1] > recent_spreads[0] else "decreasing",
            }
        else:
            volatility_analysis = {"spread_volatility": 0, "spread_trend": "unknown"}
        
        # Обнаружение паттернов
        patterns = {
            "extreme_imbalance": self.detect_extreme_imbalance(imbalance.imbalance),
            "extreme_pressure": self.detect_extreme_pressure(aggressive_pressure.pressure_ratio),
            "liquidity_withdrawal": self.detect_liquidity_withdrawal(symbol, liquidity.total_depth),
        }
        
        return MicrostructureAnalysis(
            symbol=symbol,
            timestamp=timestamp,
            snapshot=snapshot,
            imbalance=imbalance,
            aggressive_pressure=aggressive_pressure,
            liquidity=liquidity,
            trade_flow=trade_flow,
            spread_analysis=spread_analysis,
            volatility_analysis=volatility_analysis,
            patterns=patterns,
        )


# Глобальный экземпляр
_market_microstructure_engine: MarketMicrostructureEngine | None = None


def get_market_microstructure_engine() -> MarketMicrostructureEngine:
    """Получить глобальный Market Microstructure Engine"""
    global _market_microstructure_engine
    if _market_microstructure_engine is None:
        _market_microstructure_engine = MarketMicrostructureEngine()
    return _market_microstructure_engine


def reset_market_microstructure_engine():
    """Сбросить Market Microstructure Engine (для тестов)"""
    global _market_microstructure_engine
    _market_microstructure_engine = MarketMicrostructureEngine()
