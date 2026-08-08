"""
ASTRA BOT — OKX Exchange Adapter
"""

from .client import OKXClient

# OKXClient serves as the adapter
OKXAdapter = OKXClient

from .websocket import OKXWebSocket
from .order_manager import OKXOrderManager

__all__ = ["OKXAdapter", "OKXClient", "OKXWebSocket", "OKXOrderManager"]
