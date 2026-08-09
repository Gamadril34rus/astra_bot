ASTRA BOT — Database Module
Минимальная реализация
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, config: dict):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 5432)
        self.database = config.get("name", "astra_bot")
        self._connected = False
    
    async def connect(self):
        self._connected = True
        logger.info(f"Database connected: {self.host}")
    
    async def disconnect(self):
        self._connected = False
        logger.info("Database disconnected")
    
    async def health_check(self) -> bool:
        return self._connected


_db_manager: Optional[DatabaseManager] = None


async def get_db() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        raise RuntimeError("Database not initialized")
    return _db_manager


async def init_database(config: dict) -> DatabaseManager:
    global _db_manager
    _db_manager = DatabaseManager(config)
    await _db_manager.connect()
    return _db_manager


async def close_database():
    global _db_manager
    if _db_manager:
        await _db_manager.disconnect()
        _db_manager = None
