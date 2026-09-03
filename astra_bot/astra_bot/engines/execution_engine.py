"""
ASTRA BOT — Execution Engine
Движок исполнения ордеров
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from ..core import events, models

logger = logging.getLogger(__name__)


class OrderState(Enum):
    """Состояние ордера в нашей системе"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ERROR = "error"


@dataclass
class ExecutionConfig:
    """Конфигурация исполнения"""
    # Комиссии
    maker_fee_rate: Decimal = Decimal("0.001")  # 0.1%
    taker_fee_rate: Decimal = Decimal("0.001")  # 0.1%

    # Slippage
    slippage_buffer_percent: Decimal = Decimal("0.001")  # 0.1%
    max_slippage_percent: Decimal = Decimal("0.01")  # 1%

    # Время
    order_timeout_seconds: int = 60
    cancel_timeout_seconds: int = 30

    # Повторные попытки
    max_retries: int = 3
    retry_delay_seconds: float = 1.0

    # Частичные исполнения
    allow_partial_fills: bool = True
    min_fill_quantity: Decimal | None = None


@dataclass
class OrderResult:
    """Результат исполнения ордера"""
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None
    status: str
    filled_quantity: Decimal = Decimal("0")
    filled_price: Decimal | None = None
    filled_fees: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    execution_time_ms: float = 0.0
    error_message: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_success(self) -> bool:
        return self.status in ["filled", "partially_filled"]

    @property
    def is_closed(self) -> bool:
        return self.status in ["filled", "canceled", "rejected", "expired"]


@dataclass
class Fill:
    """Исполнение части ордера"""
    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    is_maker: bool = False

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price

    @property
    def net_notional(self) -> Decimal:
        if self.side == models.Side.BUY.value:
            return self.notional + self.fee
        return self.notional - self.fee


class ExecutionEngine:
    """
    Движок исполнения ордеров.

    Управляет lifecycle ордеров:
    - Размещение
    - Отмена
    - Отслеживание статуса
    - Обработка частичных исполнений
    - Reconciliation
    """

    def __init__(self, config: ExecutionConfig = None):
        self.config = config or ExecutionConfig()

        # Отслеживание активных ордеров
        self._active_orders: dict[str, OrderResult] = {}
        self._order_fills: dict[str, list[Fill]] = {}

        # Callback'и
        self._on_order_filled: list[Callable] = []
        self._on_order_rejected: list[Callable] = []
        self._on_order_canceled: list[Callable] = []

        # Exchange адаптер (будет установлен позже)
        self._exchange = None

    def set_exchange(self, exchange):
        """Установить exchange адаптер"""
        self._exchange = exchange

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
    ) -> OrderResult:
        """
        Разместить ордер.

        Возвращает OrderResult с состоянием ордера.
        """
        order_id = client_order_id or str(uuid4())

        logger.info(
            f"Placing {order_type} order: {symbol} {side} "
            f"qty={quantity}, price={price}, stop={stop_price}"
        )

        start_time = datetime.utcnow()

        try:
            # Валидация
            if quantity <= 0:
                return OrderResult(
                    order_id=order_id,
                    client_order_id=client_order_id or order_id,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    status=OrderState.REJECTED.value,
                    error_message="Quantity must be positive",
                )

            if self._exchange is None:
                raise Exception("Exchange not set")

            # Размещение через exchange
            order = await self._exchange.place_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                take_profit_price=take_profit_price,
                client_order_id=client_order_id or order_id,
            )

            execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            result = OrderResult(
                order_id=order.id or order_id,
                client_order_id=client_order_id or order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=order.status,
                created_at=datetime.utcnow(),
                execution_time_ms=execution_time,
            )

            # Регистрация активного ордера
            self._active_orders[result.order_id] = result

            logger.info(
                f"Order placed: {result.order_id}, "
                f"status={result.status}, "
                f"time={execution_time:.0f}ms"
            )

            events.emit_async(events.EventType.ORDER_PLACED, {
                "order_id": result.order_id,
                "symbol": symbol,
                "side": side,
                "quantity": str(quantity),
                "price": str(price) if price else None,
                "status": result.status,
            })

            return result

        except Exception as e:
            execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            logger.error(f"Order placement failed: {e}")

            result = OrderResult(
                order_id=order_id,
                client_order_id=client_order_id or order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderState.ERROR.value,
                error_message=str(e),
                execution_time_ms=execution_time,
            )

            events.emit_async(events.EventType.ORDER_REJECTED, {
                "order_id": result.order_id,
                "error": str(e),
            })

            return result

    async def cancel_order(
        self,
        symbol: str,
        order_id: str,
    ) -> bool:
        """Отменить ордер"""
        logger.info(f"Canceling order: {order_id}")

        try:
            if self._exchange is None:
                raise Exception("Exchange not set")

            result = await self._exchange.cancel_order(symbol, order_id)

            if result:
                # Обновление состояния
                if order_id in self._active_orders:
                    self._active_orders[order_id].status = OrderState.CANCELED.value

                events.emit_async(events.EventType.ORDER_CANCELED, {
                    "order_id": order_id,
                    "symbol": symbol,
                })

                logger.info(f"Order canceled: {order_id}")

            return result

        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False

    async def cancel_all_orders(self, symbol: str) -> int:
        """Отменить все ордера по символу"""
        logger.info(f"Canceling all orders for {symbol}")

        try:
            if self._exchange is None:
                raise Exception("Exchange not set")

            count = await self._exchange.cancel_all_orders(symbol)

            # Очистка локального кэша
            for _order_id, order in self._active_orders.items():
                if order.symbol == symbol and order.is_open:
                    order.status = OrderState.CANCELED.value

            logger.info(f"Canceled {count} orders for {symbol}")
            return count

        except Exception as e:
            logger.error(f"Cancel all failed: {e}")
            return 0

    async def get_order_status(
        self,
        symbol: str,
        order_id: str,
    ) -> OrderResult | None:
        """Получить статус ордера"""
        try:
            if self._exchange is None:
                raise Exception("Exchange not set")

            order = await self._exchange.get_order(symbol, order_id)

            if order:
                result = OrderResult(
                    order_id=order.id or order_id,
                    client_order_id=order.client_order_id or order_id,
                    symbol=symbol,
                    side=order.side,
                    order_type=order.order_type,
                    quantity=order.quantity,
                    price=order.price,
                    status=order.status,
                    filled_quantity=order.filled_quantity,
                    filled_price=order.filled_price,
                    filled_fees=order.filled_fees,
                )

                # Обновление кэша
                self._active_orders[order_id] = result

                return result

            return None

        except Exception as e:
            logger.error(f"Get order status failed: {e}")
            return None

    async def reconcile_orders(self) -> dict[str, Any]:
        """
        Синхронизировать состояние ордеров с биржей.

        Возвращает несоответствия.
        """
        if self._exchange is None:
            return {}

        try:
            server_orders = await self._exchange.get_open_orders()

            mismatches = []
            server_order_ids = {o.id for o in server_orders}
            local_order_ids = set(self._active_orders.keys())

            # Ордера на бирже, которых нет локально
            missing_locally = server_order_ids - local_order_ids
            for oid in missing_locally:
                order = next((o for o in server_orders if o.id == oid), None)
                if order:
                    self._active_orders[oid] = OrderResult(
                        order_id=oid,
                        client_order_id=order.client_order_id or oid,
                        symbol=order.symbol,
                        side=order.side,
                        order_type=order.order_type,
                        quantity=order.quantity,
                        price=order.price,
                        status=order.status,
                    )
                    mismatches.append({
                        "order_id": oid,
                        "issue": "missing_locally",
                        "server_order": order,
                    })

            # Локальные ордера, которых нет на бирже
            missing_server = local_order_ids - server_order_ids
            for oid in missing_server:
                order = self._active_orders.get(oid)
                if order and order.status in ["new", "pending", "acknowledged"]:
                    order.status = OrderState.REJECTED.value
                    mismatches.append({
                        "order_id": oid,
                        "issue": "missing_server",
                        "local_order": order,
                    })

            if mismatches:
                logger.warning(
                    f"Order reconciliation found {len(mismatches)} mismatches"
                )

            return {
                "mismatches": mismatches,
                "server_count": len(server_orders),
                "local_count": len(self._active_orders),
            }

        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            return {"error": str(e)}

    def register_on_filled_callback(self, callback: Callable):
        """Зарегистрировать callback на исполнение"""
        self._on_order_filled.append(callback)

    def register_on_rejected_callback(self, callback: Callable):
        """Зарегистрировать callback на отказ"""
        self._on_order_rejected.append(callback)

    def register_on_canceled_callback(self, callback: Callable):
        """Зарегистрировать callback на отмену"""
        self._on_order_canceled.append(callback)

    def _notify_filled(self, order: OrderResult):
        """Уведомить о исполнении"""
        for callback in self._on_order_filled:
            try:
                callback(order)
            except Exception as e:
                logger.error(f"Filled callback error: {e}")

    def _notify_rejected(self, order: OrderResult):
        """Уведомить об отказе"""
        for callback in self._on_order_rejected:
            try:
                callback(order)
            except Exception as e:
                logger.error(f"Rejected callback error: {e}")

    def get_active_orders(self) -> dict[str, OrderResult]:
        """Получить активные ордера"""
        return self._active_orders.copy()

    def get_order(self, order_id: str) -> OrderResult | None:
        """Получить ордер"""
        return self._active_orders.get(order_id)

    def get_open_order_count(self) -> int:
        """Получить количество открытых ордеров"""
        return len([
            o for o in self._active_orders.values()
            if o.status in ["new", "pending", "acknowledged", "partially_filled"]
        ])


# Глобальный execution engine
_execution_engine: ExecutionEngine | None = None


def get_execution_engine() -> ExecutionEngine:
    """Получить глобальный execution engine"""
    global _execution_engine
    if _execution_engine is None:
        _execution_engine = ExecutionEngine()
    return _execution_engine


def reset_execution_engine():
    """Сбросить execution engine (для тестов)"""
    global _execution_engine
    _execution_engine = ExecutionEngine()
