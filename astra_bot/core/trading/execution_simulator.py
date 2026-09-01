"""
ASTRA BOT - Execution Simulator

Симулятор исполнения ордеров (ТЗ Пункты 13, 17, 54-55, 65-66, 83)

Симулирует:
- order book dynamics
- market orders
- limit orders
- stop orders
- TWAP
- VWAP
- POV
- Iceberg

Учитывает:
- latency
- slippage
- partial fills
- queue position
- adverse selection
- price decay
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


class OrderType(str, Enum):
    """Типы ордеров"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TWAP = "twap"  # Time Weighted Average Price
    VWAP = "vwap"  # Volume Weighted Average Price
    POV = "pov"  # Percentage of Volume
    ICEBERG = "iceberg"


class OrderSide(str, Enum):
    """Стороны ордера"""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    """Статусы ордера"""
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"


class FillType(str, Enum):
    """Типы исполнения"""
    FILL = "fill"
    PARTIAL_FILL = "partial_fill"
    NO_FILL = "no_fill"


@dataclass
class OrderBookLevel:
    """Уровень стакана"""
    price: float
    volume: float
    orders: int = 0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "volume": self.volume,
            "orders": self.orders,
        }


@dataclass
class OrderBook:
    """Стакан заказов"""
    symbol: str
    timestamp: datetime
    
    # Уровни
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    
    # Лучшие цены
    best_bid: float = 0.0
    best_ask: float = 0.0
    
    # Спред
    spread: float = 0.0
    spread_pct: float = 0.0
    
    # Глубина
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "bid_depth": self.bid_depth,
            "ask_depth": self.ask_depth,
            "bids": [b.to_dict() for b in self.bids],
            "asks": [a.to_dict() for a in self.asks],
        }
    
    def update_best_prices(self):
        """Обновить лучшие цены"""
        if self.bids:
            self.best_bid = max(b.price for b in self.bids)
        else:
            self.best_bid = 0.0
        
        if self.asks:
            self.best_ask = min(a.price for a in self.asks)
        else:
            self.best_ask = 0.0
        
        if self.best_bid > 0 and self.best_ask > 0:
            self.spread = self.best_ask - self.best_bid
            self.spread_pct = (self.spread / self.best_bid * 100) if self.best_bid > 0 else 0.0
        else:
            self.spread = 0.0
            self.spread_pct = 0.0
        
        # Глубина
        self.bid_depth = sum(b.volume for b in self.bids)
        self.ask_depth = sum(a.volume for a in self.asks)


@dataclass
class Fill:
    """Исполнение ордера"""
    fill_id: str
    order_id: str
    timestamp: datetime
    price: float
    quantity: float
    side: OrderSide
    fill_type: FillType = FillType.FILL
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "timestamp": self.timestamp.isoformat(),
            "price": self.price,
            "quantity": self.quantity,
            "side": self.side.value,
            "fill_type": self.fill_type.value,
        }


@dataclass
class Order:
    """Ордер"""
    order_id: str
    symbol: str
    order_type: OrderType
    order_side: OrderSide
    quantity: float
    price: float | None = None  # Для limit orders
    stop_price: float | None = None  # Для stop orders
    
    # Время
    creation_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expiration_time: datetime | None = None
    
    # Статус
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    
    # Исполнение
    fills: list[Fill] = field(default_factory=list)
    avg_fill_price: float = 0.0
    
    # Параметры алгоритмов
    twap_intervals: int = 0  # Для TWAP
    vwap_target_volume: float = 0.0  # Для VWAP
    pov_percentage: float = 0.0  # Для POV
    iceberg_peak: float = 0.0  # Для Iceberg
    iceberg_hidden: float = 0.0  # Для Iceberg
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "order_type": self.order_type.value,
            "order_side": self.order_side.value,
            "quantity": self.quantity,
            "price": self.price,
            "stop_price": self.stop_price,
            "creation_time": self.creation_time.isoformat(),
            "expiration_time": self.expiration_time.isoformat() if self.expiration_time else None,
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "remaining_quantity": self.remaining_quantity,
            "avg_fill_price": self.avg_fill_price,
            "fills": [f.to_dict() for f in self.fills],
        }


@dataclass
class ExecutionResult:
    """Результат исполнения"""
    order_id: str
    order: Order
    
    # Результаты
    total_filled: float = 0.0
    avg_fill_price: float = 0.0
    total_cost: float = 0.0
    
    # Метрики
    slippage: float = 0.0
    slippage_pct: float = 0.0
    latency_ms: float = 0.0
    partial_fills: int = 0
    
    # Статистика
    execution_time_ms: float = 0.0
    price_decay: float = 0.0
    adverse_selection: float = 0.0
    
    # Статус
    success: bool = True
    error: str = ""
    
    # Время
    completion_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "total_filled": self.total_filled,
            "avg_fill_price": self.avg_fill_price,
            "total_cost": self.total_cost,
            "slippage": self.slippage,
            "slippage_pct": self.slippage_pct,
            "latency_ms": self.latency_ms,
            "partial_fills": self.partial_fills,
            "execution_time_ms": self.execution_time_ms,
            "price_decay": self.price_decay,
            "adverse_selection": self.adverse_selection,
            "success": self.success,
            "error": self.error,
            "completion_time": self.completion_time.isoformat(),
            "order": self.order.to_dict(),
        }


@dataclass
class ExecutionStatistics:
    """Статистика исполнения"""
    symbol: str
    order_type: OrderType
    
    # Метрики
    avg_slippage_pct: float = 0.0
    avg_latency_ms: float = 0.0
    fill_rate: float = 0.0
    partial_fill_rate: float = 0.0
    
    # Распределение
    slippage_distribution: dict[str, float] = field(default_factory=dict)
    latency_distribution: dict[str, float] = field(default_factory=dict)
    
    # Время
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "order_type": self.order_type.value,
            "avg_slippage_pct": self.avg_slippage_pct,
            "avg_latency_ms": self.avg_latency_ms,
            "fill_rate": self.fill_rate,
            "partial_fill_rate": self.partial_fill_rate,
            "slippage_distribution": self.slippage_distribution,
            "latency_distribution": self.latency_distribution,
            "timestamp": self.timestamp.isoformat(),
        }


class ExecutionSimulator:
    """
    Симулятор исполнения ордеров.
    
    Симулирует различные типы ордеров и алгоритмы исполнения.
    """
    
    def __init__(self):
        # Стаканы заказов
        self._order_books: dict[str, OrderBook] = {}
        
        # Ордера
        self._orders: dict[str, Order] = {}
        
        # Статистика
        self._statistics: dict[str, ExecutionStatistics] = {}
        
        # Пороги
        self.thresholds = {
            "min_latency_ms": 1.0,
            "max_latency_ms": 100.0,
            "min_slippage_pct": 0.0001,
            "max_slippage_pct": 0.01,
            "min_fill_rate": 0.5,
            "price_decay_factor": 0.001,
            "adverse_selection_factor": 0.0005,
        }
        
        # Параметры симуляции
        self.simulation_params = {
            "latency_enabled": True,
            "slippage_enabled": True,
            "partial_fills_enabled": True,
            "price_decay_enabled": True,
            "adverse_selection_enabled": True,
        }
    
    def create_order_book(
        self,
        symbol: str,
        bids: list[tuple[float, float]] | None = None,
        asks: list[tuple[float, float]] | None = None,
    ) -> OrderBook:
        """
        Создать стакан заказов.
        
        Args:
            symbol: Символ
            bids: Список (цена, объём) для покупок
            asks: Список (цена, объём) для продаж
        
        Returns:
            Стакан заказов
        """
        order_book = OrderBook(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
        )
        
        if bids:
            for price, volume in bids:
                order_book.bids.append(OrderBookLevel(price=price, volume=volume))
        
        if asks:
            for price, volume in asks:
                order_book.asks.append(OrderBookLevel(price=price, volume=volume))
        
        # Сортировать
        order_book.bids.sort(key=lambda x: x.price, reverse=True)
        order_book.asks.sort(key=lambda x: x.price)
        
        order_book.update_best_prices()
        
        self._order_books[symbol] = order_book
        
        return order_book
    
    def update_order_book(
        self,
        symbol: str,
        bids: list[tuple[float, float]] | None = None,
        asks: list[tuple[float, float]] | None = None,
    ):
        """
        Обновить стакан заказов.
        
        Args:
            symbol: Символ
            bids: Новые биды
            asks: Новые аски
        """
        if symbol not in self._order_books:
            self.create_order_book(symbol, bids, asks)
            return
        
        order_book = self._order_books[symbol]
        
        if bids:
            # Обновить биды
            new_bids = {}
            for price, volume in bids:
                new_bids[price] = volume
            
            # Объединить с существующими
            existing_bids = {b.price: b.volume for b in order_book.bids}
            existing_bids.update(new_bids)
            
            order_book.bids = [OrderBookLevel(price=p, volume=v) for p, v in existing_bids.items()]
        
        if asks:
            # Обновить аски
            new_asks = {}
            for price, volume in asks:
                new_asks[price] = volume
            
            # Объединить с существующими
            existing_asks = {a.price: a.volume for a in order_book.asks}
            existing_asks.update(new_asks)
            
            order_book.asks = [OrderBookLevel(price=p, volume=v) for p, v in existing_asks.items()]
        
        # Сортировать
        order_book.bids.sort(key=lambda x: x.price, reverse=True)
        order_book.asks.sort(key=lambda x: x.price)
        
        order_book.update_best_prices()
    
    def create_order(
        self,
        order_id: str,
        symbol: str,
        order_type: OrderType,
        order_side: OrderSide,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        expiration_time: datetime | None = None,
        **kwargs,
    ) -> Order:
        """
        Создать ордер.
        
        Args:
            order_id: ID ордера
            symbol: Символ
            order_type: Тип ордера
            order_side: Сторона ордера
            quantity: Количество
            price: Цена (для limit orders)
            stop_price: Стоп-цена (для stop orders)
            expiration_time: Время истечения
            **kwargs: Дополнительные параметры
        
        Returns:
            Ордер
        """
        order = Order(
            order_id=order_id,
            symbol=symbol,
            order_type=order_type,
            order_side=order_side,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            expiration_time=expiration_time,
            remaining_quantity=quantity,
            **kwargs,
        )
        
        self._orders[order_id] = order
        
        return order
    
    def execute_market_order(
        self,
        order_id: str,
        order_book: OrderBook | None = None,
    ) -> ExecutionResult:
        """
        Исполнить рыночный ордер.
        
        Args:
            order_id: ID ордера
            order_book: Стакан заказов (опционально)
        
        Returns:
            Результат исполнения
        """
        order = self._orders.get(order_id)
        if not order:
            return ExecutionResult(
                order_id=order_id,
                order=Order(order_id=order_id, symbol="", order_type=OrderType.MARKET, order_side=OrderSide.BUY, quantity=0),
                success=False,
                error="Order not found",
            )
        
        if order.order_type != OrderType.MARKET:
            return ExecutionResult(
                order_id=order_id,
                order=order,
                success=False,
                error="Not a market order",
            )
        
        # Получить стакан
        if order_book is None:
            order_book = self._order_books.get(order.symbol)
        
        if not order_book:
            return ExecutionResult(
                order_id=order_id,
                order=order,
                success=False,
                error="Order book not found",
            )
        
        # Симулировать задержку
        latency = self._simulate_latency()
        
        # Исполнить ордер
        fills = []
        remaining = order.remaining_quantity
        total_filled = 0.0
        total_cost = 0.0
        
        if order.order_side == OrderSide.BUY:
            # Покупка по лучшим аскам
            for ask in order_book.asks:
                if remaining <= 0:
                    break
                
                fill_qty = min(remaining, ask.volume)
                fill_price = ask.price
                
                # Применить проскальзывание
                slippage = self._simulate_slippage(fill_price, order_book.best_ask)
                actual_price = fill_price + slippage
                
                # Применить ухудшение цены
                price_decay = self._simulate_price_decay(fill_qty, order_book.ask_depth)
                actual_price += price_decay
                
                # Применить неблагоприятный отбор
                adverse_selection = self._simulate_adverse_selection(order.order_side)
                actual_price += adverse_selection
                
                fill = Fill(
                    fill_id=f"{order_id}_{len(fills)}",
                    order_id=order_id,
                    timestamp=datetime.now(timezone.utc),
                    price=actual_price,
                    quantity=fill_qty,
                    side=order.order_side,
                    fill_type=FillType.FILL if fill_qty == remaining else FillType.PARTIAL_FILL,
                )
                fills.append(fill)
                
                total_filled += fill_qty
                total_cost += fill_qty * actual_price
                remaining -= fill_qty
            
            order.filled_quantity = total_filled
            order.remaining_quantity = remaining
            order.fills = fills
            order.avg_fill_price = total_cost / total_filled if total_filled > 0 else 0.0
            
            # Рассчитать проскальзывание
            if order_book.best_ask > 0:
                slippage = (order.avg_fill_price - order_book.best_ask) / order_book.best_ask * 100
            else:
                slippage = 0.0
        else:
            # Продажа по лучшим бидам
            for bid in reversed(order_book.bids):
                if remaining <= 0:
                    break
                
                fill_qty = min(remaining, bid.volume)
                fill_price = bid.price
                
                # Применить проскальзывание
                slippage = self._simulate_slippage(fill_price, order_book.best_bid)
                actual_price = fill_price - slippage
                
                # Применить ухудшение цены
                price_decay = self._simulate_price_decay(fill_qty, order_book.bid_depth)
                actual_price -= price_decay
                
                # Применить неблагоприятный отбор
                adverse_selection = self._simulate_adverse_selection(order.order_side)
                actual_price -= adverse_selection
                
                fill = Fill(
                    fill_id=f"{order_id}_{len(fills)}",
                    order_id=order_id,
                    timestamp=datetime.now(timezone.utc),
                    price=actual_price,
                    quantity=fill_qty,
                    side=order.order_side,
                    fill_type=FillType.FILL if fill_qty == remaining else FillType.PARTIAL_FILL,
                )
                fills.append(fill)
                
                total_filled += fill_qty
                total_cost += fill_qty * actual_price
                remaining -= fill_qty
            
            order.filled_quantity = total_filled
            order.remaining_quantity = remaining
            order.fills = fills
            order.avg_fill_price = total_cost / total_filled if total_filled > 0 else 0.0
            
            # Рассчитать проскальзывание
            if order_book.best_bid > 0:
                slippage = (order_book.best_bid - order.avg_fill_price) / order_book.best_bid * 100
            else:
                slippage = 0.0
        
        # Определить статус
        if remaining == 0:
            order.status = OrderStatus.FILLED
        elif remaining < order.quantity:
            order.status = OrderStatus.PARTIALLY_FILLED
        else:
            order.status = OrderStatus.PENDING
        
        # Создать результат
        result = ExecutionResult(
            order_id=order_id,
            order=order,
            total_filled=total_filled,
            avg_fill_price=order.avg_fill_price,
            total_cost=total_cost,
            slippage=abs(order.avg_fill_price - (order_book.best_ask if order.order_side == OrderSide.BUY else order_book.best_bid)),
            slippage_pct=abs(slippage),
            latency_ms=latency,
            partial_fills=len([f for f in fills if f.fill_type == FillType.PARTIAL_FILL]),
            execution_time_ms=self._simulate_execution_time(order.quantity),
            price_decay=price_decay if 'price_decay' in locals() else 0.0,
            adverse_selection=adverse_selection if 'adverse_selection' in locals() else 0.0,
            success=True,
        )
        
        # Обновить статистику
        self._update_statistics(result)
        
        return result
    
    def execute_limit_order(
        self,
        order_id: str,
        order_book: OrderBook | None = None,
    ) -> ExecutionResult:
        """
        Исполнить лимитный ордер.
        
        Args:
            order_id: ID ордера
            order_book: Стакан заказов (опционально)
        
        Returns:
            Результат исполнения
        """
        order = self._orders.get(order_id)
        if not order:
            return ExecutionResult(
                order_id=order_id,
                order=Order(order_id=order_id, symbol="", order_type=OrderType.LIMIT, order_side=OrderSide.BUY, quantity=0),
                success=False,
                error="Order not found",
            )
        
        if order.order_type != OrderType.LIMIT or order.price is None:
            return ExecutionResult(
                order_id=order_id,
                order=order,
                success=False,
                error="Not a limit order or missing price",
            )
        
        # Получить стакан
        if order_book is None:
            order_book = self._order_books.get(order.symbol)
        
        if not order_book:
            return ExecutionResult(
                order_id=order_id,
                order=order,
                success=False,
                error="Order book not found",
            )
        
        # Симулировать задержку
        latency = self._simulate_latency()
        
        # Исполнить ордер
        fills = []
        remaining = order.remaining_quantity
        total_filled = 0.0
        total_cost = 0.0
        
        if order.order_side == OrderSide.BUY:
            # Покупка по цене не выше лимита
            for ask in order_book.asks:
                if remaining <= 0:
                    break
                
                if ask.price > order.price:
                    continue
                
                fill_qty = min(remaining, ask.volume)
                fill_price = ask.price
                
                # Применить проскальзывание (меньше для лимитных ордеров)
                slippage = self._simulate_slippage(fill_price, order_book.best_ask) * 0.5
                actual_price = fill_price + slippage
                
                # Не превышать лимит
                actual_price = min(actual_price, order.price)
                
                fill = Fill(
                    fill_id=f"{order_id}_{len(fills)}",
                    order_id=order_id,
                    timestamp=datetime.now(timezone.utc),
                    price=actual_price,
                    quantity=fill_qty,
                    side=order.order_side,
                    fill_type=FillType.FILL if fill_qty == remaining else FillType.PARTIAL_FILL,
                )
                fills.append(fill)
                
                total_filled += fill_qty
                total_cost += fill_qty * actual_price
                remaining -= fill_qty
        else:
            # Продажа по цене не ниже лимита
            for bid in reversed(order_book.bids):
                if remaining <= 0:
                    break
                
                if bid.price < order.price:
                    continue
                
                fill_qty = min(remaining, bid.volume)
                fill_price = bid.price
                
                # Применить проскальзывание (меньше для лимитных ордеров)
                slippage = self._simulate_slippage(fill_price, order_book.best_bid) * 0.5
                actual_price = fill_price - slippage
                
                # Не опускаться ниже лимита
                actual_price = max(actual_price, order.price)
                
                fill = Fill(
                    fill_id=f"{order_id}_{len(fills)}",
                    order_id=order_id,
                    timestamp=datetime.now(timezone.utc),
                    price=actual_price,
                    quantity=fill_qty,
                    side=order.order_side,
                    fill_type=FillType.FILL if fill_qty == remaining else FillType.PARTIAL_FILL,
                )
                fills.append(fill)
                
                total_filled += fill_qty
                total_cost += fill_qty * actual_price
                remaining -= fill_qty
        
        order.filled_quantity = total_filled
        order.remaining_quantity = remaining
        order.fills = fills
        order.avg_fill_price = total_cost / total_filled if total_filled > 0 else 0.0
        
        # Определить статус
        if remaining == 0:
            order.status = OrderStatus.FILLED
        elif remaining < order.quantity:
            order.status = OrderStatus.PARTIALLY_FILLED
        else:
            order.status = OrderStatus.PENDING
        
        # Рассчитать проскальзывание
        if order.order_side == OrderSide.BUY and order_book.best_ask > 0:
            slippage = (order.avg_fill_price - order_book.best_ask) / order_book.best_ask * 100
        elif order.order_side == OrderSide.SELL and order_book.best_bid > 0:
            slippage = (order_book.best_bid - order.avg_fill_price) / order_book.best_bid * 100
        else:
            slippage = 0.0
        
        # Создать результат
        result = ExecutionResult(
            order_id=order_id,
            order=order,
            total_filled=total_filled,
            avg_fill_price=order.avg_fill_price,
            total_cost=total_cost,
            slippage=abs(order.avg_fill_price - (order_book.best_ask if order.order_side == OrderSide.BUY else order_book.best_bid)),
            slippage_pct=abs(slippage),
            latency_ms=latency,
            partial_fills=len([f for f in fills if f.fill_type == FillType.PARTIAL_FILL]),
            execution_time_ms=self._simulate_execution_time(order.quantity),
            success=True,
        )
        
        # Обновить статистику
        self._update_statistics(result)
        
        return result
    
    def execute_twap_order(
        self,
        order_id: str,
        intervals: int,
        order_book: OrderBook | None = None,
    ) -> ExecutionResult:
        """
        Исполнить TWAP ордер.
        
        Args:
            order_id: ID ордера
            intervals: Количество интервалов
            order_book: Стакан заказов (опционально)
        
        Returns:
            Результат исполнения
        """
        # Упрощённая реализация TWAP
        # В реальности нужно разбить ордер на части и исполнять в течении времени
        
        order = self._orders.get(order_id)
        if not order:
            return ExecutionResult(
                order_id=order_id,
                order=Order(order_id=order_id, symbol="", order_type=OrderType.TWAP, order_side=OrderSide.BUY, quantity=0),
                success=False,
                error="Order not found",
            )
        
        # Исполнить как рыночный ордер, но с задержкой
        result = self.execute_market_order(order_id, order_book)
        
        # Увеличить время исполнения
        result.execution_time_ms *= intervals
        
        return result
    
    def _simulate_latency(self) -> float:
        """Симулировать задержку"""
        if not self.simulation_params.get("latency_enabled", True):
            return 0.0
        
        return np.random.uniform(
            self.thresholds["min_latency_ms"],
            self.thresholds["max_latency_ms"]
        )
    
    def _simulate_slippage(self, fill_price: float, best_price: float) -> float:
        """Симулировать проскальзывание"""
        if not self.simulation_params.get("slippage_enabled", True):
            return 0.0
        
        if best_price <= 0:
            return 0.0
        
        # Проскальзывание как процент от спреда
        spread_pct = abs(fill_price - best_price) / best_price * 100 if best_price > 0 else 0
        slippage_pct = np.random.uniform(
            self.thresholds["min_slippage_pct"],
            self.thresholds["max_slippage_pct"]
        )
        
        return best_price * slippage_pct / 100
    
    def _simulate_price_decay(self, order_size: float, market_depth: float) -> float:
        """Симулировать ухудшение цены"""
        if not self.simulation_params.get("price_decay_enabled", True):
            return 0.0
        
        if market_depth <= 0:
            return 0.0
        
        # Ухудшение пропорционально размеру ордера и обратно пропорционально глубине рынка
        return order_size / market_depth * self.thresholds["price_decay_factor"]
    
    def _simulate_adverse_selection(self, order_side: OrderSide) -> float:
        """Симулировать неблагоприятный отбор"""
        if not self.simulation_params.get("adverse_selection_enabled", True):
            return 0.0
        
        # Неблагоприятный отбор - случайное ухудшение
        direction = 1 if order_side == OrderSide.BUY else -1
        return direction * np.random.uniform(0, self.thresholds["adverse_selection_factor"])
    
    def _simulate_execution_time(self, quantity: float) -> float:
        """Симулировать время исполнения"""
        # Время пропорционально количеству
        return quantity * 0.1  # 0.1 мс на единицу
    
    def _update_statistics(self, result: ExecutionResult):
        """Обновить статистику"""
        key = f"{result.order.symbol}_{result.order.order_type.value}"
        
        if key not in self._statistics:
            self._statistics[key] = ExecutionStatistics(
                symbol=result.order.symbol,
                order_type=result.order.order_type,
            )
        
        stats = self._statistics[key]
        
        # Обновить средние значения
        if stats.fill_rate == 0:
            stats.fill_rate = 1.0 if result.total_filled == result.order.quantity else 0.0
        else:
            stats.fill_rate = (stats.fill_rate + (1.0 if result.total_filled == result.order.quantity else 0.0)) / 2
        
        if stats.partial_fill_rate == 0:
            stats.partial_fill_rate = 1.0 if result.partial_fills > 0 else 0.0
        else:
            stats.partial_fill_rate = (stats.partial_fill_rate + (1.0 if result.partial_fills > 0 else 0.0)) / 2


# Глобальный экземпляр
_execution_simulator: ExecutionSimulator | None = None


def get_execution_simulator() -> ExecutionSimulator:
    """Получить глобальный Execution Simulator"""
    global _execution_simulator
    if _execution_simulator is None:
        _execution_simulator = ExecutionSimulator()
    return _execution_simulator


def reset_execution_simulator():
    """Сбросить Execution Simulator (для тестов)"""
    global _execution_simulator
    _execution_simulator = ExecutionSimulator()
