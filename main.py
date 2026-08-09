#!/usr/bin/env python3
"""
ASTRA BOT - Main Entry Point
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from datetime import datetime
from decimal import Decimal

# Добавляем проект в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from astra_bot.core.config import load_settings, get_settings
from astra_bot.core.logger import setup_logging, get_component_logger
from astra_bot.core.events import get_event_bus
from astra_bot.data.database import init_database, close_database
from astra_bot.adapters.okx import OKXClient
from astra_bot.engines.regime_detector import get_regime_detector
from astra_bot.engines.risk_engine import get_risk_engine
from astra_bot.engines.execution_engine import get_execution_engine
from astra_bot.strategies import MomentumStrategy, MeanReversionStrategy
from astra_bot.paperengine.paper_engine import PaperTradingEngine

# FastAPI
from fastapi import FastAPI, HTTPException

# Инициализация логирования - ВАЖНО: /tmp/logs для Render!
log_dir = "/tmp/logs"
Path(log_dir).mkdir(parents=True, exist_ok=True)
setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"), log_dir=log_dir)

logger = get_component_logger("main")

# FastAPI приложение
app = FastAPI(title="ASTRA BOT", version="1.0.0")

# Глобальные переменные
_bot_instance = None


@app.on_event("startup")
async def startup_event():
    global _bot_instance
    logger.info("FastAPI startup...")
    _bot_instance = AstraBot()
    await _bot_instance.initialize()
    logger.info("ASTRA BOT ready")


@app.get("/")
async def root():
    return {"service": "ASTRA BOT", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/ping")
async def ping():
    return {"pong": True, "timestamp": datetime.utcnow().isoformat()}


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
        self._running = False

    async def initialize(self):
        settings = get_settings()
        self.config = settings
        
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
        
        # Exchange
        if "okx" in settings.exchanges and settings.exchanges["okx"].get("enabled", False):
            okx_config = settings.exchanges["okx"]
            if okx_config.api_key and okx_config.api_secret:
                config_dict = {
                    "api_key": okx_config.api_key,
                    "api_secret": okx_config.api_secret,
                    "passphrase": okx_config.passphrase,
                    "sandbox": True,
                    "enabled": True,
                }
                self._exchange_client = OKXClient(config_dict)
                try:
                    await self._exchange_client.initialize()
                except Exception as e:
                    logger.warning(f"Exchange init failed: {e}")
        
        # Paper engine
        self._paper_engine = PaperTradingEngine(initial_capital=Decimal("1000"))
        
        # Стратегии
        if settings.strategies.get("momentum", {}).get("enabled", True):
            self._paper_engine.add_strategy("momentum", MomentumStrategy())
        if settings.strategies.get("mean_reversion", {}).get("enabled", True):
            self._paper_engine.add_strategy("mean_reversion", MeanReversionStrategy())

    async def run_one_iteration(self):
        try:
            settings = get_settings()
            current_equity = self._paper_engine.account.equity if self._paper_engine else Decimal("1000")
            
            if self._risk_engine:
                self._risk_engine.set_capital(current_equity, Decimal("1000"))
            
            if self._paper_engine:
                self._paper_engine.account.update_equity(current_equity)
            
            return {
                "status": "ok",
                "timestamp": datetime.utcnow().isoformat(),
                "equity": str(current_equity),
                "iteration": "completed"
            }
        except Exception as e:
            logger.error(f"Iteration error: {e}")
            return {"status": "error", "error": str(e), "timestamp": datetime.utcnow().isoformat()}
    
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
