ASTRA BOT — Main Entry Point
"""

import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime
from decimal import Decimal

# Добавляем проект в путь
project_root = Path(__file__).parent.parent if '__file__' in dir() else Path.cwd()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from astra_bot.core.config import load_settings, get_settings
from astra_bot.core.logger import setup_logging, get_component_logger
from astra_bot.core.events import get_event_bus
from astra_bot.data.database import init_database, close_database
from astra_bot.adapters.okx import OKXClient
from astra_bot.engines.regime_detector import MarketRegimeDetector, get_regime_detector
from astra_bot.engines.risk_engine import RiskEngine, RiskConfig, get_risk_engine
from astra_bot.engines.execution_engine import ExecutionEngine, get_execution_engine
from astra_bot.strategies import MomentumStrategy, MeanReversionStrategy
from astra_bot.paperengine.paper_engine import PaperTradingEngine

# FastAPI
from fastapi import FastAPI

# Инициализация
setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"), log_dir="/tmp")
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
    return {"status": "healthy"}


@app.get("/ping")
async def ping():
    return {"pong": True}


@app.get("/tick")
async def tick():
    global _bot_instance
    if _bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    result = await _bot_instance.run_one_iteration()
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


class AstraBot:
    def __init__(self):
        self.config = None
        self._exchange_client = None
        self._paper_engine = None

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
            except:
                pass
        
        # Exchange
        if "okx" in settings.exchanges:
            okx_config = settings.exchanges["okx"]
            config_dict = {
                "api_key": okx_config.api_key,
                "api_secret": okx_config.api_secret,
                "passphrase": okx_config.passphrase,
                "sandbox": True,
                "enabled": True,
            }
            self._exchange_client = OKXClient(config_dict)
        
        # Paper engine
        self._paper_engine = PaperTradingEngine(initial_capital=Decimal("1000"))

    async def run_one_iteration(self):
        return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
    
    async def start(self):
        pass
    
    async def stop(self):
        pass


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
