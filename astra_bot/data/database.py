"""
ASTRA BOT — Database manager.

Использует SQLAlchemy 2.x (asyncpg) когда доступна строка подключения,
и прозрачно откатывается на no-op реализацию (для юнит-тестов / запуска
без Postgres). Это позволяет веб-приложению подниматься на Render free-tier
без обязательной базы и в то же время использовать реальную БД там, где
она сконфигурирована.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Высокоуровневая обёртка над SQLAlchemy AsyncEngine."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = int(config.get("port", 5432))
        self.database = config.get("name", "astra_bot")
        self.user = config.get("user", "")
        self.password = config.get("password", "")
        self.pool_size = int(config.get("pool_size", 10))

        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._connected = False

    def _build_url(self) -> str:
        """Собрать SQLAlchemy URL.

        Поддерживается либо явный ``database_url`` в config (приоритет),
        либо отдельные поля host/port/name/user/password.
        """
        if self.config.get("database_url"):
            return str(self.config["database_url"])
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    async def connect(self) -> None:
        url = self._build_url()
        try:
            self._engine = create_async_engine(
                url,
                pool_size=self.pool_size,
                pool_pre_ping=True,
                future=True,
            )
            self._session_factory = async_sessionmaker(
                bind=self._engine, expire_on_commit=False
            )
            # Проверяем, что соединение действительно работает.
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            self._connected = True
            logger.info("Database connected: %s:%s/%s", self.host, self.port, self.database)
        except Exception as exc:  # pragma: no cover - зависит от окружения
            logger.warning(
                "Database connection failed (%s); running in no-DB mode", exc
            )
            self._engine = None
            self._session_factory = None
            self._connected = False

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None
        self._connected = False
        logger.info("Database disconnected")

    async def health_check(self) -> bool:
        if not self._connected or self._engine is None:
            return False
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            logger.warning("DB health check failed: %s", exc)
            return False

    def session(self) -> AsyncSession:
        """Вернуть новую сессию.

        Raises:
            RuntimeError: БД не инициализирована.
        """
        if self._session_factory is None:
            raise RuntimeError("Database is not connected")
        return self._session_factory()

    @property
    def engine(self) -> AsyncEngine | None:
        return self._engine

    @property
    def is_connected(self) -> bool:
        return self._connected


# Глобальный менеджер — модуль является точкой доступа для приложения.
_db_manager: DatabaseManager | None = None


async def get_db() -> DatabaseManager:
    if _db_manager is None:
        raise RuntimeError("Database not initialized")
    return _db_manager


async def init_database(config: dict) -> DatabaseManager:
    """Инициализировать глобальный менеджер БД."""
    global _db_manager
    _db_manager = DatabaseManager(config)
    await _db_manager.connect()
    return _db_manager


async def close_database() -> None:
    global _db_manager
    if _db_manager is not None:
        await _db_manager.disconnect()
        _db_manager = None
