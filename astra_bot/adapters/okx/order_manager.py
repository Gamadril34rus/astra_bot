"""
ASTRA BOT — OKX Order Manager
Управление ордерами OKX
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from ..base import (
    Order,
    OrderStatus,
    OrderType,
)
from .client import OKXClient

logger = logging.getLogger(__name__)


class OKXOrderManager:
    """
    Менеджер ордеров для OKX.
    Отслеживает lifecycle ордеров и обеспечивает надёжное исполнение.
    """

    def __init__(self, client: OKXClient):
        self.client = client

        # Отслеживание активных ордеров
        self._active_orders: dict[str, Order] = {}
        self._order_callbacks: dict[str, list[Callable]] = {}

        # Для обработки частичных исполнений
        self._pending_fills: dict[str, dict] = {}

        # Сильные ссылки на отложенные задачи очистки.
        self._cleanup_tasks: set[asyncio.Task] = set()

    @property
    def active_orders(self) -> dict[str, Order]:
        return self._active_orders.copy()

    def register_order_callback(
        self,
        order_id: str,
        callback: Callable[[Order], None]
    ):
        """Зарегистрировать callback для ордера"""
        if order_id not in self._order_callbacks:
            self._order_callbacks[order_id] = []
        self._order_callbacks[order_id].append(callback)

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
    ) -> Order:
        """
        Разместить ордер с полным управлением.

        Returns:
            Order — созданный ордер
        """
        try:
            # Валидация
            validation_errors = self.client.validate_order(
                symbol, side, order_type, quantity, price
            )
            if validation_errors:
                logger.error(f"Order validation failed: {validation_errors}")
                raise ValueError(f"Validation failed: {validation_errors}")

            # Проверка минимальных требований
            min_errors = await self.client.check_min_order_requirements(
                symbol, quantity, price
            )
            if min_errors:
                logger.error(f"Min requirements not met: {min_errors}")
                raise ValueError(f"Min requirements failed: {min_errors}")

            # Генерация client_order_id если не предоставлен
            if not client_order_id:
                timestamp = int(datetime.utcnow().timestamp() * 1000)
                client_order_id = f"astra_{symbol.replace('/', '_')}_{side}_{timestamp}"

            # Размещение ордера
            order = await self.client.place_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                take_profit_price=take_profit_price,
                client_order_id=client_order_id,
            )

            # Регистрация в активных ордерах
            self._active_orders[order.id] = order
            self._active_orders[client_order_id] = order

            logger.info(
                f"Order placed: {order.id}, "
                f"symbol={symbol}, side={side}, "
                f"type={order_type}, qty={quantity}, "
                f"price={price}, status={order.status}"
            )

            return order

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Отменить ордер"""
        try:
            result = await self.client.cancel_order(symbol, order_id)

            if result:
                # Обновление статуса в локальном кэше
                if order_id in self._active_orders:
                    self._active_orders[order_id].status = OrderStatus.CANCELED.value
                    logger.info(f"Order canceled: {order_id}")

            return result

        except Exception as e:
            logger.error(f"Order cancellation failed: {e}")
            return False

    async def cancel_all_orders(self, symbol: str) -> int:
        """Отменить все ордера по символу"""
        try:
            count = await self.client.cancel_all_orders(symbol)

            # Очистка локального кэша
            orders_to_remove = [
                oid for oid, order in self._active_orders.items()
                if order.symbol == symbol and order.is_open
            ]
            for oid in orders_to_remove:
                self._active_orders[oid].status = OrderStatus.CANCELED.value

            logger.info(f"Cancelled {count} orders for {symbol}")
            return count

        except Exception as e:
            logger.error(f"Cancel all orders failed: {e}")
            return 0

    async def get_order(self, symbol: str, order_id: str) -> Order | None:
        """Получить статус ордера"""
        return await self.client.get_order(symbol, order_id)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Получить все открытые ордера"""
        orders = await self.client.get_open_orders(symbol)

        # Обновление локального кэша
        for order in orders:
            self._active_orders[order.id] = order

        return orders

    async def reconcile_orders(self) -> dict[str, Order]:
        """
        Синхронизировать локальный кэш с состоянием биржи.
        Возвращает несоответствия.
        """
        server_orders = await self.get_open_orders()

        mismatches = {}

        # Проверка ордеров на бирже, которых нет локально
        server_order_ids = {o.id for o in server_orders}
        local_order_ids = set(self._active_orders.keys())

        # Ордера на бирже, которых нет локально
        missing_locally = server_order_ids - local_order_ids
        if missing_locally:
            for oid in missing_locally:
                order = next((o for o in server_orders if o.id == oid), None)
                if order:
                    self._active_orders[oid] = order
                    mismatches[oid] = ("missing_locally", order)

        # Локальные ордера, которых нет на бирже
        missing_server = local_order_ids - server_order_ids
        for oid in missing_server:
            order = self._active_orders.get(oid)
            if order and order.is_open:
                # Ордер мог быть исполнен или отменён
                order.status = OrderStatus.FILLED.value if order.filled_quantity >= order.quantity else OrderStatus.CANCELED.value
                mismatches[oid] = ("missing_server", order)

        if mismatches:
            logger.warning(f"Order reconciliation found {len(mismatches)} mismatches")

        return mismatches

    async def handle_order_update(self, order: Order):
        """
        Обработать обновление ордера.
        Вызвать соответствующие callback'и.
        """
        # Обновление локального кэша
        self._active_orders[order.id] = order

        # Вызов callback'ов
        callbacks = self._order_callbacks.get(order.id, [])
        for callback in callbacks:
            try:
                await callback(order)
            except Exception as e:
                logger.error(f"Order callback error: {e}")

        # Обработка завершения ордера
        if order.is_closed:
            self._on_order_closed(order)

    def _on_order_closed(self, order: Order):
        """Ордер закрыт — очистка"""
        # Удаление из активных через некоторое время
        # (на случай если нужен будет для аудита).
        task = asyncio.create_task(
            self._remove_order_after_delay(order.id, delay=3600)
        )
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _remove_order_after_delay(self, order_id: str, delay: int = 3600):
        """Удалить ордер из кэша после задержки"""
        await asyncio.sleep(delay)

        if order_id in self._active_orders:
            del self._active_orders[order_id]

        # Отписываемся от колбэков по истёкшему ордеру.
        self._order_callbacks.pop(order_id, None)

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> Order:
        """Разместить рыночный ордер"""
        return await self.place_order(
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET.value,
            quantity=quantity,
            client_order_id=client_order_id,
        )

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str | None = None,
    ) -> Order:
        """Разместить лимитный ордер"""
        return await self.place_order(
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT.value,
            quantity=quantity,
            price=price,
            client_order_id=client_order_id,
        )

    async def place_stop_loss_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        stop_price: Decimal,
        client_order_id: str | None = None,
    ) -> Order:
        """Разместить ордер стоп-лосса"""
        return await self.place_order(
            symbol=symbol,
            side=side,
            order_type=OrderType.STOP_MARKET.value,
            quantity=quantity,
            stop_price=stop_price,
            client_order_id=client_order_id,
        )

    async def place_take_profit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        take_profit_price: Decimal,
        client_order_id: str | None = None,
    ) -> Order:
        """Разместить ордер тейк-профита"""
        return await self.place_order(
            symbol=symbol,
            side=side,
            order_type=OrderType.TAKE_PROFIT_MARKET.value,
            quantity=quantity,
            take_profit_price=take_profit_price,
            client_order_id=client_order_id,
        )

    async def place_trailing_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        callback_rate: Decimal,
        client_order_id: str | None = None,
    ) -> Order:
        """
        Разместить ордер с трейлинг-стопом.
        В OKX трейлинг-стоп реализуется через отдельные ордера.
        """
        # В OKX трейлинг-стоп — это stop ордер с параметром callback
        # Реализация зависит от конкретных возможностей API
        logger.warning("Trailing stop order not fully implemented for OKX")

        # Плейсхолдер для будущей реализации
        return await self.place_order(
            symbol=symbol,
            side=side,
            order_type=OrderType.STOP_MARKET.value,
            quantity=quantity,
            client_order_id=client_order_id,
        )


# Фабрика
def create_okx_order_manager(client: OKXClient) -> OKXOrderManager:
    """Создать менеджер ордеров OKX"""
    return OKXOrderManager(client)
