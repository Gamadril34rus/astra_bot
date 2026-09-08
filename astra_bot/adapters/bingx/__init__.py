"""
ASTRA BOT — BingX Exchange Adapter (USDT-M perpetual futures).

Активный адаптер биржи (решение: ретир OKX → BingX; бот торгует ТОЛЬКО
бессрочными фьючерсами). Публичные swap-данные без ключей, приватные
эндпоинты — по BINGX_API_KEY/BINGX_API_SECRET (без passphrase).
Live-ордера отключены: сделки исполняет PaperBroker.
"""

from .client import BingXClient
from .websocket import BingXWebSocket

# BingXClient serves as the adapter
BingXAdapter = BingXClient

__all__ = ["BingXAdapter", "BingXClient", "BingXWebSocket"]
