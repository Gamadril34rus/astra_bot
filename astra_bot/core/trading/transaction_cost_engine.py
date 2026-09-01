"""
ASTRA BOT - Transaction Cost Engine

Движок расчёта транзакционных издержек (ТЗ Пункты 17, 38, 57-58, 62-64, 83)

Рассчитывает:
- spreads
- commissions
- slippage
- market impact
- opportunity cost
- liquidity cost
- total transaction cost

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class CostType(str, Enum):
    """Типы издержек"""
    SPREAD = "spread"
    COMMISSION = "commission"
    SLIPPAGE = "slippage"
    MARKET_IMPACT = "market_impact"
    OPPORTUNITY_COST = "opportunity_cost"
    LIQUIDITY_COST = "liquidity_cost"
    TOTAL = "total"


class OrderType(str, Enum):
    """Типы ордеров"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    IOC = "ioc"  # Immediate or Cancel
    FOK = "fok"  # Fill or Kill


class OrderSide(str, Enum):
    """Стороны ордера"""
    BUY = "buy"
    SELL = "sell"


@dataclass
class CostComponent:
    """Компонент издержек"""
    cost_type: CostType
    name: str
    value: float  # В валюте
    value_pct: float  # В процентах
    description: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_type": self.cost_type.value,
            "name": self.name,
            "value": self.value,
            "value_pct": self.value_pct,
            "description": self.description,
        }


@dataclass
class TransactionCost:
    """Транзакционные издержки"""
    symbol: str
    order_type: OrderType
    order_side: OrderSide
    quantity: float
    price: float
    
    # Компоненты издержек
    components: list[CostComponent] = field(default_factory=list)
    
    # Итоговые издержки
    total_cost: float = 0.0
    total_cost_pct: float = 0.0
    
    # Время расчёта
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "order_type": self.order_type.value,
            "order_side": self.order_side.value,
            "quantity": self.quantity,
            "price": self.price,
            "components": [c.to_dict() for c in self.components],
            "total_cost": self.total_cost,
            "total_cost_pct": self.total_cost_pct,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class MarketImpactModel:
    """Модель рыночного влияния"""
    model_type: str  # linear, square_root, power, etc.
    coefficients: list[float]  # Коэффициенты модели
    
    def calculate_impact(self, order_size: float, avg_daily_volume: float) -> float:
        """
        Рассчитать рыночное влияние.
        
        Args:
            order_size: Размер ордера
            avg_daily_volume: Средний дневной объём
        
        Returns:
            Рыночное влияние в процентах
        """
        if avg_daily_volume <= 0:
            return 0.0
        
        # Отношение размера ордера к среднему дневному объёму
        participation_rate = order_size / avg_daily_volume
        
        if self.model_type == "linear":
            # Линейная модель: impact = coef[0] * participation_rate
            impact = self.coefficients[0] * participation_rate if self.coefficients else 0.0
        elif self.model_type == "square_root":
            # Квадратный корень: impact = coef[0] * sqrt(participation_rate)
            impact = self.coefficients[0] * np.sqrt(participation_rate) if self.coefficients else 0.0
        elif self.model_type == "power":
            # Степенная модель: impact = coef[0] * participation_rate^coef[1]
            if len(self.coefficients) >= 2:
                impact = self.coefficients[0] * (participation_rate ** self.coefficients[1])
            else:
                impact = 0.0
        else:
            impact = 0.0
        
        return min(impact, 100.0)  # Ограничить 100%


@dataclass
class LiquidityCostModel:
    """Модель издержек ликвидности"""
    bid_ask_spread: float = 0.0  # Средний спред
    depth: float = 0.0  # Глубина рынка
    volume: float = 0.0  # Объём
    
    def calculate_liquidity_cost(self, order_size: float, order_type: OrderType) -> float:
        """
        Рассчитать издержки ликвидности.
        
        Args:
            order_size: Размер ордера
            order_type: Тип ордера
        
        Returns:
            Издержки ликвидности в процентах
        """
        if order_type == OrderType.MARKET:
            # Для рыночных ордеров - полный спред
            return self.bid_ask_spread
        elif order_type == OrderType.LIMIT:
            # Для лимитных ордеров - часть спреда
            return self.bid_ask_spread * 0.5
        else:
            return self.bid_ask_spread * 0.75


class TransactionCostEngine:
    """
    Движок расчёта транзакционных издержек.
    
    Рассчитывает все виды издержек для различных типов ордеров.
    """
    
    def __init__(self):
        # Модели рыночного влияния
        self.market_impact_models: dict[str, MarketImpactModel] = {}
        
        # Модели издержек ликвидности
        self.liquidity_cost_models: dict[str, LiquidityCostModel] = {}
        
        # Комиссии по символам
        self.commissions: dict[str, float] = {}
        
        # Пороги
        self.thresholds = {
            "min_spread_pct": 0.0001,  # 0.01%
            "max_spread_pct": 0.01,  # 1%
            "min_slippage_pct": 0.0001,  # 0.01%
            "max_slippage_pct": 0.005,  # 0.5%
            "min_market_impact_pct": 0.0001,  # 0.01%
            "max_market_impact_pct": 0.05,  # 5%
        }
        
        # Создать стандартные модели
        self._initialize_models()
    
    def _initialize_models(self):
        """Инициализировать стандартные модели"""
        # Модель рыночного влияния для акций
        self.market_impact_models["stock"] = MarketImpactModel(
            model_type="square_root",
            coefficients=[0.5],  # 0.5 * sqrt(participation_rate)
        )
        
        # Модель рыночного влияния для криптовалют
        self.market_impact_models["crypto"] = MarketImpactModel(
            model_type="power",
            coefficients=[0.3, 0.7],  # 0.3 * participation_rate^0.7
        )
        
        # Модель издержек ликвидности для акций
        self.liquidity_cost_models["stock"] = LiquidityCostModel(
            bid_ask_spread=0.001,  # 0.1%
            depth=1000000,  # $1M
            volume=10000000,  # $10M
        )
        
        # Модель издержек ликвидности для криптовалют
        self.liquidity_cost_models["crypto"] = LiquidityCostModel(
            bid_ask_spread=0.002,  # 0.2%
            depth=100000,  # $100K
            volume=1000000,  # $1M
        )
    
    def set_commission(self, symbol: str, commission_pct: float):
        """
        Установить комиссию для символа.
        
        Args:
            symbol: Символ
            commission_pct: Комиссия в процентах
        """
        self.commissions[symbol] = commission_pct
    
    def set_market_impact_model(self, symbol: str, model: MarketImpactModel):
        """
        Установить модель рыночного влияния для символа.
        
        Args:
            symbol: Символ
            model: Модель рыночного влияния
        """
        self.market_impact_models[symbol] = model
    
    def set_liquidity_cost_model(self, symbol: str, model: LiquidityCostModel):
        """
        Установить модель издержек ликвидности для символа.
        
        Args:
            symbol: Символ
            model: Модель издержек ликвидности
        """
        self.liquidity_cost_models[symbol] = model
    
    def calculate_spread_cost(
        self,
        symbol: str,
        bid_price: float,
        ask_price: float,
        order_type: OrderType,
        order_side: OrderSide,
    ) -> CostComponent:
        """
        Рассчитать издержки спреда.
        
        Args:
            symbol: Символ
            bid_price: Цена покупки
            ask_price: Цена продажи
            order_type: Тип ордера
            order_side: Сторона ордера
        
        Returns:
            Компонент издержек
        """
        if bid_price <= 0 or ask_price <= 0:
            return CostComponent(
                cost_type=CostType.SPREAD,
                name="Spread",
                value=0.0,
                value_pct=0.0,
                description="Invalid bid/ask prices",
            )
        
        # Рассчитать спред
        spread = ask_price - bid_price
        spread_pct = (spread / bid_price * 100) if bid_price > 0 else 0.0
        
        # Для рыночных ордеров - полный спред
        if order_type == OrderType.MARKET:
            cost_value = spread
            cost_pct = spread_pct
        # Для лимитных ордеров - часть спреда
        elif order_type == OrderType.LIMIT:
            cost_value = spread * 0.5
            cost_pct = spread_pct * 0.5
        else:
            cost_value = spread * 0.75
            cost_pct = spread_pct * 0.75
        
        return CostComponent(
            cost_type=CostType.SPREAD,
            name="Spread",
            value=cost_value,
            value_pct=cost_pct,
            description=f"Bid: {bid_price}, Ask: {ask_price}",
        )
    
    def calculate_commission_cost(
        self,
        symbol: str,
        quantity: float,
        price: float,
    ) -> CostComponent:
        """
        Рассчитать комиссию.
        
        Args:
            symbol: Символ
            quantity: Количество
            price: Цена
        
        Returns:
            Компонент издержек
        """
        commission_pct = self.commissions.get(symbol, 0.001)  # 0.1% по умолчанию
        
        order_value = quantity * price
        commission_value = order_value * commission_pct / 100
        
        return CostComponent(
            cost_type=CostType.COMMISSION,
            name="Commission",
            value=commission_value,
            value_pct=commission_pct,
            description=f"Commission rate: {commission_pct}%",
        )
    
    def calculate_slippage_cost(
        self,
        symbol: str,
        order_price: float,
        execution_price: float,
        order_side: OrderSide,
    ) -> CostComponent:
        """
        Рассчитать издержки проскальзывания.
        
        Args:
            symbol: Символ
            order_price: Цена ордера
            execution_price: Цена исполнения
            order_side: Сторона ордера
        
        Returns:
            Компонент издержек
        """
        if order_price <= 0:
            return CostComponent(
                cost_type=CostType.SLIPPAGE,
                name="Slippage",
                value=0.0,
                value_pct=0.0,
                description="Invalid order price",
            )
        
        slippage = execution_price - order_price
        slippage_pct = (slippage / order_price * 100) if order_price > 0 else 0.0
        
        # Для покупки - проскальзывание вверх плохо
        # Для продажи - проскальзывание вниз плохо
        if order_side == OrderSide.BUY:
            cost_value = max(0, slippage)
            cost_pct = max(0, slippage_pct)
        else:
            cost_value = max(0, -slippage)
            cost_pct = max(0, -slippage_pct)
        
        return CostComponent(
            cost_type=CostType.SLIPPAGE,
            name="Slippage",
            value=cost_value,
            value_pct=cost_pct,
            description=f"Order price: {order_price}, Execution price: {execution_price}",
        )
    
    def calculate_market_impact_cost(
        self,
        symbol: str,
        order_size: float,
        avg_daily_volume: float,
        price: float,
    ) -> CostComponent:
        """
        Рассчитать издержки рыночного влияния.
        
        Args:
            symbol: Символ
            order_size: Размер ордера
            avg_daily_volume: Средний дневной объём
            price: Цена
        
        Returns:
            Компонент издержек
        """
        # Получить модель
        model = self.market_impact_models.get("stock")  # По умолчанию для акций
        if symbol in self.market_impact_models:
            model = self.market_impact_models[symbol]
        
        if not model or avg_daily_volume <= 0:
            return CostComponent(
                cost_type=CostType.MARKET_IMPACT,
                name="Market Impact",
                value=0.0,
                value_pct=0.0,
                description="No model or invalid volume",
            )
        
        # Рассчитать влияние
        impact_pct = model.calculate_impact(order_size, avg_daily_volume)
        impact_value = price * order_size * impact_pct / 100
        
        return CostComponent(
            cost_type=CostType.MARKET_IMPACT,
            name="Market Impact",
            value=impact_value,
            value_pct=impact_pct,
            description=f"Model: {model.model_type}, Order size: {order_size}, ADV: {avg_daily_volume}",
        )
    
    def calculate_opportunity_cost(
        self,
        symbol: str,
        price: float,
        opportunity_price: float,
        order_side: OrderSide,
    ) -> CostComponent:
        """
        Рассчитать упущенную выгоду.
        
        Args:
            symbol: Символ
            price: Текущая цена
            opportunity_price: Цена упущенной возможности
            order_side: Сторона ордера
        
        Returns:
            Компонент издержек
        """
        if price <= 0:
            return CostComponent(
                cost_type=CostType.OPPORTUNITY_COST,
                name="Opportunity Cost",
                value=0.0,
                value_pct=0.0,
                description="Invalid price",
            )
        
        # Упущенная выгода
        opportunity_diff = opportunity_price - price
        opportunity_pct = (opportunity_diff / price * 100) if price > 0 else 0.0
        
        # Для покупки - упущенная выгода если цена выросла
        # Для продажи - упущенная выгода если цена упала
        if order_side == OrderSide.BUY:
            cost_value = max(0, opportunity_diff)
            cost_pct = max(0, opportunity_pct)
        else:
            cost_value = max(0, -opportunity_diff)
            cost_pct = max(0, -opportunity_pct)
        
        return CostComponent(
            cost_type=CostType.OPPORTUNITY_COST,
            name="Opportunity Cost",
            value=cost_value,
            value_pct=cost_pct,
            description=f"Current price: {price}, Opportunity price: {opportunity_price}",
        )
    
    def calculate_liquidity_cost(
        self,
        symbol: str,
        order_size: float,
        order_type: OrderType,
        price: float,
    ) -> CostComponent:
        """
        Рассчитать издержки ликвидности.
        
        Args:
            symbol: Символ
            order_size: Размер ордера
            order_type: Тип ордера
            price: Цена
        
        Returns:
            Компонент издержек
        """
        # Получить модель
        model = self.liquidity_cost_models.get("stock")  # По умолчанию для акций
        if symbol in self.liquidity_cost_models:
            model = self.liquidity_cost_models[symbol]
        
        if not model:
            return CostComponent(
                cost_type=CostType.LIQUIDITY_COST,
                name="Liquidity Cost",
                value=0.0,
                value_pct=0.0,
                description="No model",
            )
        
        # Рассчитать издержки
        liquidity_pct = model.calculate_liquidity_cost(order_size, order_type)
        liquidity_value = price * order_size * liquidity_pct / 100
        
        return CostComponent(
            cost_type=CostType.LIQUIDITY_COST,
            name="Liquidity Cost",
            value=liquidity_value,
            value_pct=liquidity_pct,
            description=f"Model: {model.model_type}, Order size: {order_size}",
        )
    
    def calculate_transaction_cost(
        self,
        symbol: str,
        order_type: OrderType,
        order_side: OrderSide,
        quantity: float,
        price: float,
        bid_price: float | None = None,
        ask_price: float | None = None,
        execution_price: float | None = None,
        order_price: float | None = None,
        opportunity_price: float | None = None,
        avg_daily_volume: float | None = None,
    ) -> TransactionCost:
        """
        Рассчитать полные транзакционные издержки.
        
        Args:
            symbol: Символ
            order_type: Тип ордера
            order_side: Сторона ордера
            quantity: Количество
            price: Текущая цена
            bid_price: Цена покупки
            ask_price: Цена продажи
            execution_price: Цена исполнения
            order_price: Цена ордера
            opportunity_price: Цена упущенной возможности
            avg_daily_volume: Средний дневной объём
        
        Returns:
            Полные транзакционные издержки
        """
        components = []
        
        # Спред
        if bid_price is not None and ask_price is not None:
            spread_component = self.calculate_spread_cost(
                symbol, bid_price, ask_price, order_type, order_side
            )
            components.append(spread_component)
        
        # Комиссия
        commission_component = self.calculate_commission_cost(symbol, quantity, price)
        components.append(commission_component)
        
        # Проскальзывание
        if execution_price is not None and order_price is not None:
            slippage_component = self.calculate_slippage_cost(
                symbol, order_price, execution_price, order_side
            )
            components.append(slippage_component)
        
        # Рыночное влияние
        if avg_daily_volume is not None and avg_daily_volume > 0:
            market_impact_component = self.calculate_market_impact_cost(
                symbol, quantity * price, avg_daily_volume, price
            )
            components.append(market_impact_component)
        
        # Упущенная выгода
        if opportunity_price is not None:
            opportunity_component = self.calculate_opportunity_cost(
                symbol, price, opportunity_price, order_side
            )
            components.append(opportunity_component)
        
        # Издержки ликвидности
        liquidity_component = self.calculate_liquidity_cost(
            symbol, quantity * price, order_type, price
        )
        components.append(liquidity_component)
        
        # Рассчитать итоговые издержки
        total_cost = sum(c.value for c in components)
        total_cost_pct = sum(c.value_pct for c in components)
        
        return TransactionCost(
            symbol=symbol,
            order_type=order_type,
            order_side=order_side,
            quantity=quantity,
            price=price,
            components=components,
            total_cost=total_cost,
            total_cost_pct=total_cost_pct,
        )


# Глобальный экземпляр
_transaction_cost_engine: TransactionCostEngine | None = None


def get_transaction_cost_engine() -> TransactionCostEngine:
    """Получить глобальный Transaction Cost Engine"""
    global _transaction_cost_engine
    if _transaction_cost_engine is None:
        _transaction_cost_engine = TransactionCostEngine()
    return _transaction_cost_engine


def reset_transaction_cost_engine():
    """Сбросить Transaction Cost Engine (для тестов)"""
    global _transaction_cost_engine
    _transaction_cost_engine = TransactionCostEngine()
