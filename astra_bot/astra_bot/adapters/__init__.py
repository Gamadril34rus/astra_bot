"""
ASTRA BOT — Exchange Adapters
"""

from .base import (
    AccountBalance,
    Candle,
    ExchangeAdapter,
    ExchangeFactory,
    ExchangeHealth,
    ExchangeHealthStatus,
    ExchangeType,
    Instrument,
    Order,
    OrderBook,
    OrderBookEntry,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    PositionStatus,
    Trade,
)
from .bingx import BingXAdapter, BingXClient, BingXWebSocket
from .bybit import BybitAdapter
from .okx import OKXAdapter, OKXClient, OKXOrderManager, OKXWebSocket

# OKX — legacy-адаптер после ретира OKX → BingX (см. config/settings.yaml):
# остаётся импортируемым для тестов и отката, но в активных путях не создаётся.

__all__ = [
    "AccountBalance",
    "BingXAdapter",
    "BingXClient",
    "BingXWebSocket",
    "BybitAdapter",
    "Candle",
    "ExchangeAdapter",
    "ExchangeFactory",
    "ExchangeHealth",
    "ExchangeHealthStatus",
    "ExchangeType",
    "Instrument",
    "OKXAdapter",
    "OKXClient",
    "OKXOrderManager",
    "OKXWebSocket",
    "Order",
    "OrderBook",
    "OrderBookEntry",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Position",
    "PositionSide",
    "PositionStatus",
    "Trade",
]
