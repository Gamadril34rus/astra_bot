"""Тесты DatabaseManager (без живого Postgres)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astra_bot.data.database import DatabaseManager, close_database, init_database


class _FakeConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *args, **kwargs):
        return None


class _FakeEngine:
    def __init__(self):
        self.disposed = False

    def connect(self):
        return _FakeConnection()

    async def dispose(self):
        self.disposed = True


def test_build_url_from_parts():
    manager = DatabaseManager(
        {"host": "h", "port": 6543, "name": "db", "user": "u", "password": "p"}
    )
    assert manager._build_url() == "postgresql+asyncpg://u:p@h:6543/db"


def test_build_url_prefers_database_url():
    manager = DatabaseManager({"database_url": "postgresql+asyncpg://u:p@h/db"})
    assert manager._build_url() == "postgresql+asyncpg://u:p@h/db"


@pytest.mark.asyncio
async def test_connect_succeeds_with_mocked_engine():
    manager = DatabaseManager({"host": "localhost", "name": "astra"})
    fake_engine = _FakeEngine()

    with patch(
        "astra_bot.data.database.create_async_engine", return_value=fake_engine
    ), patch(
        "astra_bot.data.database.async_sessionmaker",
        return_value=MagicMock(),
    ):
        await manager.connect()
        assert manager.is_connected is True
        assert await manager.health_check() is True
        await manager.disconnect()
        assert fake_engine.disposed is True
        assert manager.is_connected is False


@pytest.mark.asyncio
async def test_connect_falls_back_to_no_db_mode_on_failure():
    manager = DatabaseManager({"host": "127.0.0.1", "name": "x"})
    with patch(
        "astra_bot.data.database.create_async_engine",
        side_effect=RuntimeError("boom"),
    ):
        await manager.connect()
    assert manager.is_connected is False
    assert await manager.health_check() is False


def test_session_raises_when_disconnected():
    manager = DatabaseManager({})
    with pytest.raises(RuntimeError):
        manager.session()


@pytest.mark.asyncio
async def test_init_and_close_global_manager():
    with patch(
        "astra_bot.data.database.DatabaseManager.connect", new=AsyncMock()
    ), patch(
        "astra_bot.data.database.DatabaseManager.disconnect", new=AsyncMock()
    ):
        db = await init_database({"host": "localhost"})
        assert db is not None
        await close_database()
