"""
ASTRA BOT — BingX Exchange Adapter

Активный адаптер биржи (решение: ретир OKX → BingX). Работает со
спот-счётом BingX: публичные рыночные данные без ключей, приватные
эндпоинты — по BINGX_API_KEY/BINGX_API_SECRET (без passphrase).
"""

from .client import BingXClient
from .websocket import BingXWebSocket

# BingXClient serves as the adapter
BingXAdapter = BingXClient

__all__ = ["BingXAdapter", "BingXClient", "BingXWebSocket"]
