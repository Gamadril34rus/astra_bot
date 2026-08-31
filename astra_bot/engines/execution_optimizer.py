"""
ASTRA BOT — Execution Optimizer

Движок оптимизации исполнения (Master Specification v2, Section 15)

Получает:
- signal
- expected edge
- liquidity
- spread
- order book
- volatility
- urgency
- position size

Выбирает:
- order type
- entry price
- execution timing
- order size

Исследует стратегии (Section 16):
- MARKET
- LIMIT
- PASSIVE_LIMIT
- AGGRESSIVE_LIMIT
- WAIT
- SPLIT_ORDER
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class OrderType(str, Enum):
    """Типы ордеров"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    PASSIVE_LIMIT = "PASSIVE_LIMIT"
    AGGRESSIVE_LIMIT = "AGGRESSIVE_LIMIT"
    WAIT = "WAIT"
    SPLIT_ORDER = "SPLIT_ORDER"


class ExecutionUrgency(str, Enum):
    """Уровни срочности исполнения"""
    IMMEDIATE = "IMMEDIATE"  # Немедленное исполнение
    HIGH = "HIGH"  # Высокая срочность
    NORMAL = "NORMAL"  # Нормальная срочность
    LOW = "LOW"  # Низкая срочность


@dataclass
class OrderBookState:
    """Состояние стакана"""
    symbol: str
    bids: list[tuple[float, float]]  # (price, quantity)
    asks: list[tuple[float, float]]  # (price, quantity)
    mid_price: float
    spread: float
    spread_pct: float
    depth: float  # Общая глубина стакана
    best_bid: float
    best_ask: float
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mid_price": self.mid_price,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "depth": self.depth,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "bids_count": len(self.bids),
            "asks_count": len(self.asks),
        }


@dataclass
class LiquidityState:
    """Состояние ликвидности"""
    symbol: str
    volume_24h: float
    volume_current: float  # Текущий объём торгов
    order_book_liquidity: float  # Ликвидность стакана
    market_depth: float
    volatility: float
    
    @property
    def is_liquid(self) -> bool:
        """Достаточная ликвидность для исполнения"""
        return (self.volume_24h > 100000 and 
                self.order_book_liquidity > 1000 and
                self.market_depth > 5000)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "volume_24h": self.volume_24h,
            "volume_current": self.volume_current,
            "order_book_liquidity": self.order_book_liquidity,
            "market_depth": self.market_depth,
            "volatility": self.volatility,
            "is_liquid": self.is_liquid,
        }


@dataclass
class ExecutionStrategy:
    """Стратегия исполнения"""
    order_type: OrderType
    entry_price: float | None = None
    order_size: float | None = None
    split_sizes: list[float] | None = None  # Для SPLIT_ORDER
    timing: str = "immediate"  # immediate, delayed, scheduled
    expected_slippage: float = 0.0
    expected_fill_price: float | None = None
    probability_of_fill: float = 1.0
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "order_type": self.order_type.value,
            "entry_price": self.entry_price,
            "order_size": self.order_size,
            "timing": self.timing,
            "expected_slippage": self.expected_slippage,
            "expected_fill_price": self.expected_fill_price,
            "probability_of_fill": self.probability_of_fill,
        }
        if self.split_sizes:
            result["split_sizes"] = self.split_sizes
        return result


@dataclass
class ExecutionPlan:
    """План исполнения"""
    symbol: str
    signal: dict[str, Any]
    expected_edge: float  # Ожидаемый edge (%)
    
    # Рекомендуемая стратегия
    recommended_strategy: ExecutionStrategy
    
    # Альтернативные стратегии
    alternative_strategies: list[ExecutionStrategy] = field(default_factory=list)
    
    # Оценка качества
    execution_quality_score: float = 0.0
    
    # Метаданные
    timestamp: datetime = field(default_factory=datetime.now)
    market_conditions: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "signal": self.signal,
            "expected_edge": self.expected_edge,
            "recommended_strategy": self.recommended_strategy.to_dict(),
            "alternative_strategies": [s.to_dict() for s in self.alternative_strategies],
            "execution_quality_score": self.execution_quality_score,
            "timestamp": self.timestamp.isoformat(),
            "market_conditions": self.market_conditions,
        }


@dataclass
class ExecutionResult:
    """Результат исполнения"""
    symbol: str
    order_type: OrderType
    requested_price: float
    order_price: float
    mid_at_signal: float
    spread: float
    fill_price: float | None = None
    mid_at_fill: float | None = None
    slippage: float | None = None
    latency: float | None = None  # в миллисекундах
    
    @property
    def implementation_shortfall(self) -> float | None:
        """Implementation Shortfall (Section 17)"""
        if self.fill_price is None or self.mid_at_fill is None:
            return None
        
        # Implementation Shortfall = (fill_price - decision_price) - (mid_at_fill - mid_at_signal)
        # Для покупки: decision_price = order_price
        # Для продажи: decision_price = order_price (но направление учитывается)
        
        # Упрощённая версия
        price_diff = self.fill_price - self.requested_price
        mid_diff = self.mid_at_fill - self.mid_at_signal
        
        return price_diff - mid_diff
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "symbol": self.symbol,
            "order_type": self.order_type.value,
            "requested_price": self.requested_price,
            "order_price": self.order_price,
            "fill_price": self.fill_price,
            "mid_at_signal": self.mid_at_signal,
            "mid_at_fill": self.mid_at_fill,
            "spread": self.spread,
            "slippage": self.slippage,
            "latency": self.latency,
        }
        if self.implementation_shortfall is not None:
            result["implementation_shortfall"] = self.implementation_shortfall
        return result


class ExecutionOptimizer:
    """
    Движок оптимизации исполнения.
    
    Выбирает оптимальную стратегию исполнения на основе текущих условий рынка
    и характеристик сигнала.
    """
    
    def __init__(self):
        # Пороги для выбора стратегии
        self.thresholds = {
            "spread_pct": {
                "low": 0.001,
                "medium": 0.005,
                "high": 0.01,
            },
            "volatility": {
                "low": 0.01,
                "medium": 0.05,
                "high": 0.10,
            },
            "liquidity": {
                "low": 1000,
                "medium": 10000,
                "high": 100000,
            },
            "edge_size": {
                "small": 0.001,
                "medium": 0.005,
                "large": 0.01,
            },
            "position_size": {
                "small": 0.01,  # % от капитала
                "medium": 0.05,
                "large": 0.10,
            }
        }
        
        # Веса для оценки стратегий
        self.strategy_weights = {
            "slippage": 0.3,
            "fill_probability": 0.3,
            "speed": 0.2,
            "cost": 0.2,
        }
    
    def assess_market_conditions(
        self,
        order_book: OrderBookState,
        liquidity: LiquidityState,
        urgency: ExecutionUrgency
    ) -> dict[str, Any]:
        """
        Оценить текущие условия рынка.
        
        Args:
            order_book: Состояние стакана
            liquidity: Состояние ликвидности
            urgency: Уровень срочности
        
        Returns:
            Словарь с оценкой условий
        """
        # Оценить spread
        if order_book.spread_pct < self.thresholds["spread_pct"]["low"]:
            spread_level = "low"
        elif order_book.spread_pct < self.thresholds["spread_pct"]["medium"]:
            spread_level = "medium"
        else:
            spread_level = "high"
        
        # Оценить волатильность
        if liquidity.volatility < self.thresholds["volatility"]["low"]:
            vol_level = "low"
        elif liquidity.volatility < self.thresholds["volatility"]["medium"]:
            vol_level = "medium"
        else:
            vol_level = "high"
        
        # Оценить ликвидность
        if liquidity.order_book_liquidity > self.thresholds["liquidity"]["high"]:
            liq_level = "high"
        elif liquidity.order_book_liquidity > self.thresholds["liquidity"]["medium"]:
            liq_level = "medium"
        else:
            liq_level = "low"
        
        return {
            "spread_level": spread_level,
            "volatility_level": vol_level,
            "liquidity_level": liq_level,
            "urgency": urgency.value,
            "spread_pct": order_book.spread_pct,
            "volatility": liquidity.volatility,
            "liquidity": liquidity.order_book_liquidity,
        }
    
    def evaluate_market_order(
        self,
        order_book: OrderBookState,
        signal_price: float,
        order_size: float
    ) -> ExecutionStrategy:
        """
        Оценить стратегию MARKET ORDER.
        
        Args:
            order_book: Состояние стакана
            signal_price: Цена сигнала
            order_size: Размер ордера
        
        Returns:
            ExecutionStrategy
        """
        # Для рыночного ордера исполнение произойдёт по лучшей цене
        if order_size > 0:  # Покупка
            expected_fill_price = order_book.best_ask
        else:  # Продажа
            expected_fill_price = order_book.best_bid
        
        # Рассчитать проскальзывание
        slippage = abs(expected_fill_price - signal_price)
        slippage_pct = slippage / signal_price if signal_price > 0 else 0
        
        # Вероятность исполнения
        fill_probability = 1.0
        
        return ExecutionStrategy(
            order_type=OrderType.MARKET,
            entry_price=signal_price,
            order_size=order_size,
            timing="immediate",
            expected_slippage=slippage_pct,
            expected_fill_price=expected_fill_price,
            probability_of_fill=fill_probability
        )
    
    def evaluate_limit_order(
        self,
        order_book: OrderBookState,
        signal_price: float,
        order_size: float,
        direction: str  # "buy" или "sell"
    ) -> ExecutionStrategy:
        """
        Оценить стратегию LIMIT ORDER.
        
        Args:
            order_book: Состояние стакана
            signal_price: Цена сигнала
            order_size: Размер ордера
            direction: Направление (buy/sell)
        
        Returns:
            ExecutionStrategy
        """
        # Для лимитного ордера устанавливаем цену лучше рыночной
        if direction == "buy":
            # Покупаем по цене ниже рыночной
            limit_price = signal_price * 0.999  # -0.1%
            expected_fill_price = limit_price
            # Вероятность исполнения зависит от расстояния до лучшей цены
            distance_to_best = order_book.best_ask - limit_price
            fill_probability = max(0, 1 - (distance_to_best / order_book.spread))
        else:  # sell
            # Продаём по цене выше рыночной
            limit_price = signal_price * 1.001  # +0.1%
            expected_fill_price = limit_price
            # Вероятность исполнения зависит от расстояния до лучшей цены
            distance_to_best = limit_price - order_book.best_bid
            fill_probability = max(0, 1 - (distance_to_best / order_book.spread))
        
        # Проскальзывание минимальное
        slippage_pct = 0.0
        
        return ExecutionStrategy(
            order_type=OrderType.LIMIT,
            entry_price=limit_price,
            order_size=order_size,
            timing="immediate",
            expected_slippage=slippage_pct,
            expected_fill_price=expected_fill_price,
            probability_of_fill=fill_probability
        )
    
    def evaluate_passive_limit_order(
        self,
        order_book: OrderBookState,
        signal_price: float,
        order_size: float,
        direction: str
    ) -> ExecutionStrategy:
        """
        Оценить стратегию PASSIVE_LIMIT ORDER (не агрессивный).
        
        Args:
            order_book: Состояние стакана
            signal_price: Цена сигнала
            order_size: Размер ордера
            direction: Направление (buy/sell)
        
        Returns:
            ExecutionStrategy
        """
        # Пассивный лимитный ордер ставится в стакан и ждёт исполнения
        if direction == "buy":
            # Покупаем по цене ниже лучшего бида
            limit_price = order_book.best_bid * 0.999
            expected_fill_price = limit_price
            # Вероятность исполнения ниже, так как мы не агрессивны
            fill_probability = 0.5
        else:  # sell
            # Продаём по цене выше лучшего аска
            limit_price = order_book.best_ask * 1.001
            expected_fill_price = limit_price
            fill_probability = 0.5
        
        slippage_pct = 0.0
        
        return ExecutionStrategy(
            order_type=OrderType.PASSIVE_LIMIT,
            entry_price=limit_price,
            order_size=order_size,
            timing="delayed",
            expected_slippage=slippage_pct,
            expected_fill_price=expected_fill_price,
            probability_of_fill=fill_probability
        )
    
    def evaluate_aggressive_limit_order(
        self,
        order_book: OrderBookState,
        signal_price: float,
        order_size: float,
        direction: str
    ) -> ExecutionStrategy:
        """
        Оценить стратегию AGGRESSIVE_LIMIT ORDER.
        
        Args:
            order_book: Состояние стакана
            signal_price: Цена сигнала
            order_size: Размер ордера
            direction: Направление (buy/sell)
        
        Returns:
            ExecutionStrategy
        """
        # Агрессивный лимитный ордер ставится выше/ниже лучших цен
        if direction == "buy":
            # Покупаем по цене выше лучшего аска
            limit_price = order_book.best_ask * 1.001
            expected_fill_price = limit_price
            fill_probability = 0.9
        else:  # sell
            # Продаём по цене ниже лучшего бида
            limit_price = order_book.best_bid * 0.999
            expected_fill_price = limit_price
            fill_probability = 0.9
        
        slippage_pct = 0.001  # Небольшое проскальзывание
        
        return ExecutionStrategy(
            order_type=OrderType.AGGRESSIVE_LIMIT,
            entry_price=limit_price,
            order_size=order_size,
            timing="immediate",
            expected_slippage=slippage_pct,
            expected_fill_price=expected_fill_price,
            probability_of_fill=fill_probability
        )
    
    def evaluate_wait_strategy(
        self,
        order_book: OrderBookState,
        signal_price: float,
        order_size: float,
        direction: str
    ) -> ExecutionStrategy:
        """
        Оценить стратегию WAIT (ожидание лучших условий).
        
        Args:
            order_book: Состояние стакана
            signal_price: Цена сигнала
            order_size: Размер ордера
            direction: Направление (buy/sell)
        
        Returns:
            ExecutionStrategy
        """
        # Ожидание лучших условий
        # Не ставим ордер сразу, ждём улучшения рынка
        
        return ExecutionStrategy(
            order_type=OrderType.WAIT,
            entry_price=signal_price,
            order_size=order_size,
            timing="scheduled",
            expected_slippage=0.0,
            expected_fill_price=signal_price,
            probability_of_fill=0.7
        )
    
    def evaluate_split_order(
        self,
        order_book: OrderBookState,
        signal_price: float,
        order_size: float,
        direction: str,
        num_splits: int = 3
    ) -> ExecutionStrategy:
        """
        Оценить стратегию SPLIT_ORDER (разбиение ордера).
        
        Args:
            order_book: Состояние стакана
            signal_price: Цена сигнала
            order_size: Размер ордера
            direction: Направление (buy/sell)
            num_splits: Количество частей
        
        Returns:
            ExecutionStrategy
        """
        # Разбить ордер на части
        split_sizes = [order_size / num_splits for _ in range(num_splits)]
        
        # Для каждой части использовать разные стратегии
        # Первая часть - рыночный ордер
        # Остальные - лимитные ордера
        
        # Средняя ожидаемая цена
        if direction == "buy":
            expected_fill_price = order_book.best_ask
        else:
            expected_fill_price = order_book.best_bid
        
        # Среднее проскальзывание
        slippage_pct = order_book.spread_pct / 2
        
        return ExecutionStrategy(
            order_type=OrderType.SPLIT_ORDER,
            entry_price=signal_price,
            order_size=order_size,
            split_sizes=split_sizes,
            timing="immediate",
            expected_slippage=slippage_pct,
            expected_fill_price=expected_fill_price,
            probability_of_fill=0.95
        )
    
    def score_strategy(
        self,
        strategy: ExecutionStrategy,
        expected_edge: float,
        position_size: float
    ) -> float:
        """
        Оценить стратегию по нескольким критериям.
        
        Args:
            strategy: Стратегия исполнения
            expected_edge: Ожидаемый edge
            position_size: Размер позиции
        
        Returns:
            Оценка стратегии (выше = лучше)
        """
        # Критерии:
        # 1. Проскальзывание (меньше = лучше)
        slippage_score = 1 - strategy.expected_slippage
        
        # 2. Вероятность исполнения (выше = лучше)
        fill_score = strategy.probability_of_fill
        
        # 3. Скорость исполнения (быстрее = лучше для срочных ордеров)
        speed_score = 1.0 if strategy.timing == "immediate" else 0.7
        
        # 4. Стоимость (меньше = лучше)
        cost_score = 1 - strategy.expected_slippage * position_size
        
        # Объединить оценки с весами
        score = (
            self.strategy_weights["slippage"] * slippage_score +
            self.strategy_weights["fill_probability"] * fill_score +
            self.strategy_weights["speed"] * speed_score +
            self.strategy_weights["cost"] * cost_score
        )
        
        return score
    
    def select_optimal_strategy(
        self,
        signal: dict[str, Any],
        order_book: OrderBookState,
        liquidity: LiquidityState,
        urgency: ExecutionUrgency,
        expected_edge: float,
        position_size: float
    ) -> ExecutionPlan:
        """
        Выбрать оптимальную стратегию исполнения.
        
        Args:
            signal: Сигнал для торговли
            order_book: Состояние стакана
            liquidity: Состояние ликвидности
            urgency: Уровень срочности
            expected_edge: Ожидаемый edge
            position_size: Размер позиции
        
        Returns:
            ExecutionPlan
        """
        direction = signal.get("direction", "buy")
        signal_price = signal.get("entry_price", order_book.mid_price)
        order_size = signal.get("position_size", position_size)
        
        # Оценить условия рынка
        market_conditions = self.assess_market_conditions(
            order_book, liquidity, urgency
        )
        
        # Сгенерировать и оценить все стратегии
        strategies = []
        
        # 1. Market Order
        market_strategy = self.evaluate_market_order(
            order_book, signal_price, order_size
        )
        market_score = self.score_strategy(
            market_strategy, expected_edge, position_size
        )
        strategies.append((market_strategy, market_score))
        
        # 2. Limit Order
        limit_strategy = self.evaluate_limit_order(
            order_book, signal_price, order_size, direction
        )
        limit_score = self.score_strategy(
            limit_strategy, expected_edge, position_size
        )
        strategies.append((limit_strategy, limit_score))
        
        # 3. Passive Limit Order
        passive_strategy = self.evaluate_passive_limit_order(
            order_book, signal_price, order_size, direction
        )
        passive_score = self.score_strategy(
            passive_strategy, expected_edge, position_size
        )
        strategies.append((passive_strategy, passive_score))
        
        # 4. Aggressive Limit Order
        aggressive_strategy = self.evaluate_aggressive_limit_order(
            order_book, signal_price, order_size, direction
        )
        aggressive_score = self.score_strategy(
            aggressive_strategy, expected_edge, position_size
        )
        strategies.append((aggressive_strategy, aggressive_score))
        
        # 5. Split Order
        split_strategy = self.evaluate_split_order(
            order_book, signal_price, order_size, direction
        )
        split_score = self.score_strategy(
            split_strategy, expected_edge, position_size
        )
        strategies.append((split_strategy, split_score))
        
        # 6. Wait Strategy
        wait_strategy = self.evaluate_wait_strategy(
            order_book, signal_price, order_size, direction
        )
        wait_score = self.score_strategy(
            wait_strategy, expected_edge, position_size
        )
        strategies.append((wait_strategy, wait_score))
        
        # Сортировать по оценке
        strategies.sort(key=lambda x: x[1], reverse=True)
        
        # Выбрать лучшую стратегию
        best_strategy, best_score = strategies[0]
        
        # Создать план исполнения
        plan = ExecutionPlan(
            symbol=signal.get("symbol", ""),
            signal=signal,
            expected_edge=expected_edge,
            recommended_strategy=best_strategy,
            alternative_strategies=[s[0] for s in strategies[1:]],
            execution_quality_score=best_score,
            market_conditions=market_conditions
        )
        
        return plan
    
    def calculate_execution_cost(
        self,
        strategy: ExecutionStrategy,
        fees_pct: float = 0.001
    ) -> float:
        """
        Рассчитать полную стоимость исполнения.
        
        Args:
            strategy: Стратегия исполнения
            fees_pct: Комиссия (%)
        
        Returns:
            Полная стоимость исполнения (%)
        """
        # Стоимость = проскальзывание + комиссия
        total_cost = strategy.expected_slippage + fees_pct
        
        # Если вероятность исполнения < 1, учесть ожидаемую стоимость
        if strategy.probability_of_fill < 1.0:
            # Ожидаемая стоимость = стоимость * вероятность исполнения
            # + стоимость ожидания (оппортунистическая стоимость)
            opportunity_cost = (1 - strategy.probability_of_fill) * 0.001
            total_cost += opportunity_cost
        
        return total_cost


# Глобальный экземпляр Execution Optimizer
_execution_optimizer: ExecutionOptimizer | None = None


def get_execution_optimizer() -> ExecutionOptimizer:
    """Получить глобальный Execution Optimizer"""
    global _execution_optimizer
    if _execution_optimizer is None:
        _execution_optimizer = ExecutionOptimizer()
    return _execution_optimizer


def reset_execution_optimizer():
    """Сбросить Execution Optimizer (для тестов)"""
    global _execution_optimizer
    _execution_optimizer = ExecutionOptimizer()
