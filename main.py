#!/usr/bin/env python3
"""
ASTRA BOT — Main Entry Point
Работает в двух режимах:
- Web mode (рендер): FastAPI с /tick эндпоинтом для крон-пингера
- Normal mode (VPS): полноценный цикл бота
"""

import asyncio
import logging
import sys
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
from astra_bot.adapters.okx import OKXClient, OKXWebSocket, OKXOrderManager
from astra_bot.engines.regime_detector import MarketRegimeDetector, get_regime_detector
from astra_bot.engines.risk_engine import RiskEngine, RiskConfig, get_risk_engine
from astra_bot.engines.execution_engine import ExecutionEngine, ExecutionConfig, get_execution_engine
from astra_bot.strategies import MomentumStrategy, MeanReversionStrategy
from astra_bot.paperengine.paper_engine import PaperTradingEngine

# FastAPI для web-режима
try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    print("WARNING: FastAPI not installed, install with: pip install fastapi uvicorn")

# Настройка логирования
log_dir = os.environ.get("LOG_DIR", "/tmp/logs")
Path(log_dir).mkdir(parents=True, exist_ok=True)
setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"), log_dir=log_dir)
logger = get_component_logger("main")


class AstraBot:
    """Основной класс бота"""
    
    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.environ.get("ASTRA_CONFIG", "config/settings.yaml")
        self.config = None
        self._exchange_client = None
        self._regime_detector = None
        self._risk_engine = None
        self._execution_engine = None
        self._paper_engine = None
        self._strategies = {}
        self._running = False
    
    async def initialize(self):
        """Инициализация"""
        logger.info("ASTRA BOT initializing...")
        
        # 1. Конфигурация
        self.config = load_settings(self.config_path)
        
        # 2. БД (если есть)
        settings = get_settings()
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
                logger.info("Database connected")
            except Exception as e:
                logger.warning(f"Database not available (ok for paper): {e}")
        
        # 3. Exchange
        if "okx" in settings.exchanges:
            okx_config = settings.exchanges["okx"]
            config_dict = {
                "api_key": okx_config.api_key,
                "api_secret": okx_config.api_secret,
                "passphrase": okx_config.passphrase,
                "sandbox": okx_config.sandbox,
                "enabled": okx_config.enabled,
            }
            self._exchange_client = OKXClient(config_dict)
            await self._exchange_client.initialize()
        
        # 4. Движки
        self._regime_detector = get_regime_detector()
        self._risk_engine = get_risk_engine()
        self._execution_engine = get_execution_engine()
        
        if self._exchange_client:
            self._execution_engine.set_exchange(self._exchange_client)
        
        # 5. Стратегии
        settings = get_settings()
        if settings.strategies.get("momentum", {}).get("enabled", True):
            self._strategies["momentum"] = MomentumStrategy()
        if settings.strategies.get("mean_reversion", {}).get("enabled", True):
            self._strategies["mean_reversion"] = MeanReversionStrategy()
        
        # 6. Paper engine
        initial_capital = Decimal("1000")
        self._paper_engine = PaperTradingEngine(initial_capital=initial_capital)
        for name, strategy in self._strategies.items():
            self._paper_engine.add_strategy(name, strategy)
        
        logger.info("ASTRA BOT initialized")
    
    async def run_one_iteration(self) -> dict:
        """
        Одна итерация бота (для /tick эндпоинта).
        Вызывается крон-пингером каждые 15 минут.
        """
        start_time = datetime.utcnow()
        
        try:
            # Логика одной итерации
            settings = get_settings()
            current_equity = self._paper_engine.account.equity if self._paper_engine else Decimal("1000")
            
            # Обновляем риск-движок
            if self._risk_engine:
                self._risk_engine.set_capital(current_equity, Decimal("1000"))
            
            # Получаем рыночные данные (упрощённо для web-режима)
            # В реальности тут было бы получение через WebSocket/REST
            
            # Проверяем стратегии
            for symbol in settings.instruments[:1]:  # Для начала только BTC
                # В реальности тут получение свечей и проверка стратегий
                pass
            
            # Обновляем состояние
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
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def start(self):
        """Запуск в обычном режиме (для VPS)"""
        if not self._exchange_client:
            raise RuntimeError("Not initialized")
        
        self._running = True
        logger.info("ASTRA BOT starting (normal mode)...")
        
        # Запуск основного цикла
        while self._running:
            try:
                await self.run_one_iteration()
                await asyncio.sleep(60)  # Каждую минуту
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                await asyncio.sleep(5)
        
        await self.stop()
    
    async def stop(self):
        """Остановка"""
        self._running = False
        logger.info("ASTRA BOT stopping...")
        
        if self._exchange_client:
            await self._exchange_client.close()
        await close_database()
    
    def get_status(self) -> dict:
        """Текущий статус"""
        return {
            "running": self._running,
            "environment": get_settings().environment if self.config else "unknown",
            "strategies": list(self._strategies.keys()),
            "exchange": "okx" if self._exchange_client else None,
        }


# FastAPI приложение для Render Web Service
app = FastAPI(title="ASTRA BOT", version="1.0.0")

# Глобальный экземпляр бота (создаётся при первом запросе)
_bot_instance: AstraBot = None


@app.on_event("startup")
async def startup_event():
    """Инициализация при старте web-сервиса"""
    global _bot_instance
    logger.info("FastAPI startup...")
    _bot_instance = AstraBot()
    await _bot_instance.initialize()
    logger.info("ASTRA BOT ready")


@app.get("/")
async def root():
    """Главная страница"""
    return {
        "service": "ASTRA BOT",
        "version": "1.0.0",
        "status": "running",
        "mode": "render-web",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health")
async def health():
    """Проверка здоровья"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/ping")
async def ping():
    """Пинг для крон-пингера"""
    return {"pong": True, "timestamp": datetime.utcnow().isoformat()}


@app.get("/tick")
async def tick():
    """
    Эндпоинт для крон-пингера.
    Вызывается каждые 15 минут для выполнения одной итерации бота.
    """
    global _bot_instance
    
    if _bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    
    result = await _bot_instance.run_one_iteration()
    
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    
    return result


@app.get("/status")
async def status():
    """Текущий статус бота"""
    global _bot_instance
    
    if _bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    
    return _bot_instance.get_status()


@app.get("/metrics")
async def metrics():
    """Метрики для мониторинга (Prometheus format)"""
    # В реальности тут были бы метрики
    return {
        "bot_status": "running",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/ready")
async def ready():
    """Проверка готовности"""
    return {"ready": True, "timestamp": datetime.utcnow().isoformat()}


def run_web_mode():
    """Запуск в веб-режиме (для Render)"""
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    logger.info(f"Starting web server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


def run_normal_mode():
    """Запуск в обычном режиме (для VPS)"""
    async def _run():
        bot = AstraBot()
        await bot.initialize()
        await bot.start()
    
    asyncio.run(_run())


if __name__ == "__main__":
    # Определяем режим запуска
    if os.environ.get("RENDER_WEB") == "true" or os.environ.get("PORT"):
        run_web_mode()
    else:
        run_normal_mode()
