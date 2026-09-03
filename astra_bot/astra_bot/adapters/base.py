"""
ASTRA BOT — Базовый адаптер биржи
Exchange Abstraction Layer
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ExchangeType(Enum):
    """Типы бирж"""
    OKX = "okx"
    BYBIT = "bybit"
    BINGX = "bingx"
    BINANCE = "binance"
    MEXC = "mexc"


class OrderSide(Enum):
    """Сторона ордера"""
    BUY = "buy"
    SELL = "sell"
    LONG = "long"
    SHORT = "short"


class OrderType(Enum):
    """Тип ордера"""
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS_LIMIT = "stop_loss_limit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    STOP_MARKET = "stop_market"
    TAKE_PROFIT_MARKET = "take_profit_market"


class OrderStatus(Enum):
    """Статус ордера"""
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    TRIGGERED = "triggered"


class PositionSide(Enum):
    """Сторона позиции"""
    LONG = "long"
    SHORT = "short"


class PositionStatus(Enum):
    """Статус позиции"""
    OPEN = "open"
    CLOSED = "closed"
    PARTIALLY_CLOSED = "partially_closed"


class ExchangeHealthStatus(Enum):
    """Статус здоровья биржи"""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"


@dataclass
class Instrument:
    """Инструмент торговли"""
    exchange: str = ""
    symbol: str = ""
    base_asset: str = ""
    quote_asset: str = ""
    min_quantity: Decimal = Decimal("0")
    min_notional: Decimal = Decimal("0")
    step_size: Decimal = Decimal("0.001")
    tick_size: Decimal = Decimal("0.01")
    price_precision: int = 2
    quantity_precision: int = 4
    trading_status: str = "trading"
    fee_rate: Decimal = Decimal("0.001")
    is_active: bool = True
    contract_type: str = "spot"  # spot, linear, inverse
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_exchange_response(cls, exchange: str, data: dict[str, Any]) -> "Instrument":
        """Создать из ответа биржи"""
        raise NotImplementedError

    def is_valid_quantity(self, quantity: Decimal) -> bool:
        """Проверить валидность количества"""
        if quantity < self.min_quantity:
            return False
        if quantity <= 0:
            return False
        return True

    def is_valid_price(self, price: Decimal) -> bool:
        """Проверить валидность цены"""
        if price <= 0:
            return False
        return True

    def format_quantity(self, quantity: Decimal) -> Decimal:
        """Отформатировать количество"""
        quantizer = Decimal(10) ** -self.quantity_precision
        return quantity.quantize(quantizer)

    def format_price(self, price: Decimal) -> Decimal:
        """Отформатировать цену"""
        quantizer = Decimal(10) ** -self.price_precision
        return price.quantize(quantizer)


@dataclass
class Candle:
    """Свеча OHLCV"""
    exchange: str = ""
    symbol: str = ""
    timeframe: str = "1m"
    open_time: int = 0
    open: Decimal = Decimal("0")
    high: Decimal = Decimal("0")
    low: Decimal = Decimal("0")
    close: Decimal = Decimal("0")
    volume: Decimal = Decimal("0")
    quote_volume: Decimal = Decimal("0")
    trades_count: int = 0
    close_time: int | None = None

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    @property
    def body(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def change_pct(self) -> Decimal:
        if self.open <= 0:
            return Decimal("0")
        return (self.close - self.open) / self.open * Decimal("100")

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": self.open_time,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "quote_volume": str(self.quote_volume),
        }


@dataclass
class Trade:
    """Торговая сделка"""
    trade_id: str
    exchange: str
    symbol: str
    price: Decimal
    quantity: Decimal
    side: str
    timestamp: int
    is_taker: bool = False

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity


@dataclass
class OrderBookEntry:
    """Запись стакана"""
    price: Decimal
    quantity: Decimal
    orders: int = 0


@dataclass
class OrderBook:
    """Стакан заявок"""
    symbol: str
    exchange: str
    bids: list[OrderBookEntry] = field(default_factory=list)
    asks: list[OrderBookEntry] = field(default_factory=list)
    timestamp: int = field(default_factory=lambda: int(datetime.utcnow().timestamp() * 1000))
    sequence: int | None = None

    @property
    def best_bid(self) -> Decimal | None:
        if not self.bids:
            return None
        return self.bids[0].price

    @property
    def best_ask(self) -> Decimal | None:
        if not self.asks:
            return None
        return self.asks[0].price

    @property
    def spread(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid

    @property
    def mid_price(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / Decimal("2")

    def get_imbalance(self, depth: int = 10) -> Decimal:
        """Рассчитать дисбаланс"""
        bid_vol = sum(e.quantity for e in self.bids[:depth])
        ask_vol = sum(e.quantity for e in self.asks[:depth])
        total = bid_vol + ask_vol
        if total <= 0:
            return Decimal("0")
        return (bid_vol - ask_vol) / total


@dataclass
class AccountBalance:
    """Баланс аккаунта"""
    account_id: str = ""
    exchange: str = ""
    asset: str = ""
    free: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    @property
    def available(self) -> Decimal:
        return self.free


@dataclass
class Order:
    """Ордер"""
    id: str | None = None
    client_order_id: str | None = None
    exchange: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    quantity: Decimal = Decimal("0")
    price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    status: str = "new"
    filled_quantity: Decimal = Decimal("0")
    filled_price: Decimal | None = None
    filled_fees: Decimal = Decimal("0")
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    filled_at: datetime | None = None
    exchange_order_id: str | None = None
    reject_reason: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status in ["new", "pending", "partially_filled", "acknowledged"]

    @property
    def is_closed(self) -> bool:
        return self.status in ["filled", "canceled", "rejected", "expired"]

    @property
    def is_filled(self) -> bool:
        return self.status == "filled"

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "client_order_id": self.client_order_id,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
            "status": self.status,
            "filled_quantity": str(self.filled_quantity),
            "filled_price": str(self.filled_price) if self.filled_price else None,
            "filled_fees": str(self.filled_fees),
        }


@dataclass
class Position:
    """Позиция"""
    id: str | None = None
    account_id: str = ""
    exchange: str = ""
    symbol: str = ""
    side: str = ""
    quantity: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    current_price: Decimal | None = None
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    status: str = "open"
    strategy_name: str = ""
    open_time: datetime = field(default_factory=datetime.utcnow)
    close_time: datetime | None = None

    @property
    def market_value(self) -> Decimal:
        if self.current_price is None:
            return Decimal("0")
        return self.quantity * self.current_price


@dataclass
class ExchangeHealth:
    """Здоровье биржи"""
    exchange: str
    status: ExchangeHealthStatus = ExchangeHealthStatus.OFFLINE
    api_latency_ms: float = 0.0
    websocket_status: str = "DISCONNECTED"
    rejected_orders_count: int = 0
    execution_quality_score: float = 1.0
    price_anomaly_detected: bool = False
    maintenance_mode: bool = False
    error_rate: float = 0.0
    last_check: datetime = field(default_factory=datetime.utcnow)

    @property
    def health_score(self) -> float:
        """Общий балл здоровья (0-100)"""
        score = 100.0
        score -= self.api_latency_ms / 10
        if self.websocket_status != "CONNECTED":
            score -= 30
        score -= self.rejected_orders_count * 5
        score *= self.execution_quality_score
        if self.price_anomaly_detected:
            score -= 40
        if self.maintenance_mode:
            score = 0
        return max(0, min(100, score))

    @property
    def is_healthy(self) -> bool:
        return self.health_score >= 70 and not self.maintenance_mode


class ExchangeAdapter(ABC):
    """
    Базовый контракт адаптера биржи.
    Каждый адаптер должен реализовать этот интерфейс.
    """

    # Название биржи
    exchange_name: str = "base"
    exchange_type: ExchangeType

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.api_key = config.get("api_key", "")
        self.api_secret = config.get("api_secret", "")
        self.passphrase = config.get("passphrase")
        self.sandbox = config.get("sandbox", False)
        self.base_url = config.get("base_url")
        self.enabled = config.get("enabled", True)
        self.contract_type = config.get("contract_type", "spot")

        self._is_connected = False
        self._last_latency = 0.0

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    # === Инструменты ===

    @abstractmethod
    async def get_instruments(self, symbol: str | None = None) -> list[Instrument]:
        """Получить метаданные инструментов"""

    @abstractmethod
    async def get_instrument(self, symbol: str) -> Instrument | None:
        """Получить один инструмент"""

    # === Рыночные данные ===

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int = 1000
    ) -> list[Candle]:
        """Получить исторические свечи"""

    @abstractmethod
    async def get_recent_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100
    ) -> list[Candle]:
        """Получить последние свечи"""

    @abstractmethod
    async def get_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int = 100
    ) -> list[Trade]:
        """Получить историю торгов"""

    @abstractmethod
    async def get_orderbook(
        self,
        symbol: str,
        depth: int = 20
    ) -> OrderBook:
        """Получить стакан заявок"""

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Получить тикер"""

    # === Аккаунт ===

    @abstractmethod
    async def get_account_balance(self) -> dict[str, AccountBalance]:
        """Получить баланс аккаунта"""

    @abstractmethod
    async def get_balances(self, assets: list[str] | None = None) -> dict[str, Decimal]:
        """Получить балансы в виде словаря"""

    # === Ордера ===

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
        client_order_id: str | None = None,
        **kwargs
    ) -> Order:
        """Разместить ордер"""

    @abstractmethod
    async def cancel_order(
        self,
        symbol: str,
        order_id: str
    ) -> bool:
        """Отменить ордер"""

    @abstractmethod
    async def cancel_all_orders(self, symbol: str) -> int:
        """Отменить все ордера по символу"""

    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Order | None:
        """Получить ордер по ID"""

    @abstractmethod
    async def get_open_orders(
        self,
        symbol: str | None = None
    ) -> list[Order]:
        """Получить открытые ордера"""

    @abstractmethod
    async def get_order_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int = 100
    ) -> list[Order]:
        """Получить историю ордеров"""

    # === Позиции ===

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Получить открытые позиции"""

    @abstractmethod
    async def close_position(
        self,
        symbol: str,
        quantity: Decimal | None = None,
        price: Decimal | None = None
    ) -> bool:
        """Закрыть позицию"""

    # === Здоровье ===

    @abstractmethod
    async def get_exchange_health(self) -> ExchangeHealth:
        """Получить метрики здоровья биржи"""

    @abstractmethod
    async def test_connection(self) -> bool:
        """Проверить соединение"""

    # === Вспомогательные методы ===

    def _generate_client_order_id(self, symbol: str, side: str, timestamp: int = None) -> str:
        """Сгенерировать client_order_id"""
        if timestamp is None:
            timestamp = int(datetime.utcnow().timestamp() * 1000)
        base = f"{symbol.replace('/','-')}/{side}"
        return f"astra_{base}_{timestamp}"

    def validate_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Decimal | None = None
    ) -> list[str]:
        """Проверить валидность ордера"""
        errors = []

        if quantity <= 0:
            errors.append("Quantity must be positive")

        if price is not None and price <= 0:
            errors.append("Price must be positive")

        return errors

    async def ensure_instrument(self, symbol: str) -> Instrument:
        """Получить или обновить инструмент"""
        instrument = await self.get_instrument(symbol)
        if instrument is None:
            raise ValueError(f"Instrument not found: {symbol}")
        return instrument

    async def check_min_order_requirements(
        self,
        symbol: str,
        quantity: Decimal,
        price: Decimal | None = None
    ) -> list[str]:
        """Проверить минимальные требования ордера"""
        errors = []

        try:
            instrument = await self.get_instrument(symbol)

            if quantity < instrument.min_quantity:
                errors.append(
                    f"Quantity {quantity} below min {instrument.min_quantity} "
                    f"for {symbol}"
                )

            notional = quantity * (price or Decimal("0"))
            if notional < instrument.min_notional:
                errors.append(
                    f"Notional {notional} below min {instrument.min_notional} "
                    f"for {symbol}"
                )
        except Exception as e:
            logger.error(f"Error checking min requirements: {e}")
            errors.append(f"Failed to check requirements: {e}")

        return errors


class ExchangeFactory:
    """Фабрика адаптеров бирж"""

    _adapters: dict[str, ExchangeAdapter] = {}

    @classmethod
    def register(cls, exchange_type: ExchangeType):
        """Декоратор для регистрации адаптера"""
        def decorator(adapter_class):
            cls._adapters[exchange_type.value] = adapter_class
            return adapter_class
        return decorator

    @classmethod
    def create(
        cls,
        exchange: str,
        config: dict[str, Any]
    ) -> ExchangeAdapter | None:
        """Создать адаптер биржи"""
        adapter_class = cls._adapters.get(exchange.lower())
        if adapter_class is None:
            logger.warning(f"No adapter for exchange: {exchange}")
            return None

        return adapter_class(config)

    @classmethod
    def get_registered_exchanges(cls) -> list[str]:
        """Получить список зарегистрированных бирж"""
        return list(cls._adapters.keys())


# Регистрация адаптеров
def _register_adapters():
    """Регистрация всех адаптеров"""
    # OKX оставлен в реестре как legacy-адаптер (ретир OKX → BingX):
    # активный контур больше не создаёт OKXClient.
    from .bingx import BingXAdapter
    from .bybit import BybitAdapter
    from .okx import OKXAdapter

    ExchangeFactory.register(ExchangeType.BINGX)(BingXAdapter)
    ExchangeFactory.register(ExchangeType.BYBIT)(BybitAdapter)
    ExchangeFactory.register(ExchangeType.OKX)(OKXAdapter)


# Автоматическая регистрация при импорте
_register_adapters()
