#!/usr/bin/env python3
"""
ASTRA BOT - Main Entry Point
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

# Подстраховка для запуска `python main.py` из произвольной рабочей директории.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.adapters.okx import OKXClient
from astra_bot.core.config import get_settings, load_settings
from astra_bot.core.logger import get_component_logger, setup_logging
from astra_bot.data.database import close_database, init_database
from astra_bot.engines.risk_engine import get_risk_engine
from astra_bot.paperengine.paper_engine import PaperTradingEngine
from astra_bot.strategies import MeanReversionStrategy, MomentumStrategy
from fastapi import FastAPI, HTTPException

# Инициализация логирования в /tmp/logs (допустимо для записи на Render)
log_dir = "/tmp/logs"
Path(log_dir).mkdir(parents=True, exist_ok=True)
setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"), log_dir=log_dir)

logger = get_component_logger("main")

# Глобальные переменные
_bot_instance = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Управление жизненным циклом FastAPI-приложения."""
    global _bot_instance
    logger.info("FastAPI startup...")
    _bot_instance = AstraBot()
    try:
        await _bot_instance.initialize()
        logger.info("ASTRA BOT ready")
        yield
    finally:
        if _bot_instance is not None:
            await _bot_instance.stop()
        logger.info("ASTRA BOT shut down")


# FastAPI приложение
app = FastAPI(title="ASTRA BOT", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "ASTRA BOT", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}


@app.get("/ping")
async def ping():
    return {"pong": True, "timestamp": datetime.now(UTC).isoformat()}


@app.get("/tick")
async def tick():
    global _bot_instance
    if _bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    result = await _bot_instance.run_one_iteration()
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@app.get("/status")
async def status():
    global _bot_instance
    if _bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    return _bot_instance.get_status()


class AstraBot:

    def __init__(self):
        self.config = None
        self._exchange_client = None
        self._paper_engine = None
        self._risk_engine = None
        self._running = False

    async def initialize(self):
        try:
            settings = get_settings()
        except RuntimeError:
            # Настройки ещё не загружены — подхватываем дефолтный конфиг.
            settings = load_settings()
        self.config = settings

        # Инициализация Risk Engine
        try:
            self._risk_engine = get_risk_engine()
        except Exception as e:
            logger.warning(f"Risk engine init failed: {e}")

        # БД
        if settings.database:
            try:
                db_config = {
                    "host": settings.database.host,
                    "port": settings.database.port,
                    "name": settings.database.name,
                    "user": settings.database.user,
                    "password": settings.database.password,
                }
                await init_database(db_config)
            except Exception as e:
                logger.warning(f"Database not available: {e}")

        # Exchange. ``settings.exchanges[name]`` — это ExchangeConfig, а не
        # словарь, поэтому обращаемся к атрибутам напрямую.
        okx_config = settings.exchanges.get("okx") if settings.exchanges else None
        if okx_config and okx_config.enabled and okx_config.api_key and okx_config.api_secret:
            config_dict = {
                "api_key": okx_config.api_key,
                "api_secret": okx_config.api_secret,
                "passphrase": okx_config.passphrase,
                "sandbox": okx_config.sandbox,
                "enabled": True,
            }
            self._exchange_client = OKXClient(config_dict)
            try:
                await self._exchange_client.initialize()
            except Exception as e:
                logger.warning(f"Exchange init failed: {e}")

        # Paper engine
        self._paper_engine = PaperTradingEngine(
            initial_capital=Decimal("1000")
        )

        # Стратегии
        if settings.strategies.get("momentum", {}).get("enabled", True):
            self._paper_engine.add_strategy("momentum", MomentumStrategy())
        if settings.strategies.get("mean_reversion", {}).get("enabled", True):
            self._paper_engine.add_strategy(
                "mean_reversion", MeanReversionStrategy()
            )

    def get_status(self):
        return {
            "running": self._running,
            "exchange_connected": self._exchange_client is not None,
            "paper_engine": self._paper_engine is not None,
            "risk_engine": self._risk_engine is not None,
            "equity": (
                str(self._paper_engine.account.equity)
                if self._paper_engine
                else "1000"
            ),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def run_one_iteration(self):
        try:
            current_equity = (
                self._paper_engine.account.equity
                if self._paper_engine
                else Decimal("1000")
            )

            if self._risk_engine:
                self._risk_engine.set_capital(current_equity, Decimal("1000"))

            if self._paper_engine:
                self._paper_engine.account.update_equity(current_equity)

            return {
                "status": "ok",
                "timestamp": datetime.now(UTC).isoformat(),
                "equity": str(current_equity),
                "iteration": "completed",
            }
        except Exception as e:
            logger.error(f"Iteration error: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat(),
            }

    async def start(self):
        self._running = True
        while self._running:
            await self.run_one_iteration()
            await asyncio.sleep(60)

    async def stop(self):
        self._running = False
        if self._exchange_client:
            await self._exchange_client.close()
        await close_database()


def run_web_mode():
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    logger.info(f"Starting web server on {host}:{port}")
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_web_mode()
