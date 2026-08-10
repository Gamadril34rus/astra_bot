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
from astra_bot.core.metrics import SYSTEM_ERRORS, render_metrics
from astra_bot.data.database import close_database, init_database
from astra_bot.engines.risk_engine import get_risk_engine
from astra_bot.paperengine.paper_engine import PaperTradingEngine
from astra_bot.strategies import MeanReversionStrategy, MomentumStrategy
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse

# Каталог логов настраивается через LOG_DIR; по умолчанию /tmp/logs,
# который доступен на запись в контейнере/на Render.
log_dir = os.environ.get("LOG_DIR", "/tmp/logs")
try:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
except OSError:
    log_dir = None
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


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request, exc: Exception):
    """Поймать необработанное исключение, записать в метрики и логи."""
    SYSTEM_ERRORS.labels(
        component="web", error_type=type(exc).__name__
    ).inc()
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "error": str(exc)},
    )


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


@app.middleware("http")
async def add_request_id(request, call_next):
    """Пробросить/сгенерировать X-Request-Id для трассировки в логах."""
    request_id = request.headers.get("X-Request-Id") or os.urandom(8).hex()
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.get("/metrics")
async def metrics():
    """Prometheus-метрики в text/exposition-format."""
    return Response(
        content=render_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.post("/train")
async def train(days: int = 365, timeframe: str = "1h", symbol: str = "BTC/USDT"):
    """Запустить обучение на истории OKX без депозита.

    Эндпоинт предназначен для первичного обучения модели: тянет год
    свечей публичного рынка, строит walk-forward разметку и обучает
    ML-классификатор. Реальные ордера не выставляются.
    """
    try:
        from astra_bot.ml.historical_training import (
            HistoricalTrainingConfig,
            train_on_historical_data,
        )

        config = HistoricalTrainingConfig(
            symbol=symbol,
            timeframe=timeframe,
            lookback_days=days,
        )
        artifact = await train_on_historical_data(config)
        return {
            "status": "ok",
            "artifact": str(artifact),
            "days": days,
            "symbol": symbol,
            "timeframe": timeframe,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/self_play")
async def self_play(
    target_trades: int = 3000,
    timeframe: str = "1h",
    offline_bars: int = 3000,
):
    """Запустить walk-forward self-play на виртуальные 2000 ₽.

    Бот проходит год истории бар-за-баром, делает ~2-5k виртуальных
    ставок, сохраняет уроки в models/lessons.jsonl и возвращает отчёт.
    Депозит не используется.
    """
    try:
        from decimal import Decimal

        from astra_bot.ml.self_play import SelfPlayConfig, SelfPlayEngine

        config = SelfPlayConfig(
            timeframe=timeframe,
            target_trades=target_trades,
            initial_capital=Decimal("2000"),
        )
        engine = SelfPlayEngine(config)
        report = await engine.run(offline_bars=offline_bars)
        return {
            "status": "ok",
            "trades": report.total_trades,
            "wins": report.wins,
            "losses": report.losses,
            "win_rate": round(report.win_rate, 2),
            "profit_factor": round(report.profit_factor, 3),
            "pnl": round(report.total_pnl, 2),
            "final_equity": round(report.final_equity, 2),
            "max_drawdown_pct": round(report.max_drawdown_pct, 2),
            "started_learning": report.started_learning,
            "message": report.message,
            "lessons": str(report.lessons_path),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/retrain")
async def retrain(min_samples: int = 200):
    """Переобучить weekly-модель на накопленных уроках self-play."""
    try:
        from astra_bot.ml.weekly_learner import train_weekly

        result = train_weekly(min_samples=min_samples)
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



class AstraBot:

    def __init__(self):
        self.config = None
        self._exchange_client = None
        self._paper_engine = None
        self._risk_engine = None
        self._telegram_bot = None
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

        # БД. На Render передаётся одна переменная DATABASE_URL; локально —
        # отдельные DB_HOST/DB_PORT/... через YAML.
        database_url = os.environ.get("DATABASE_URL")
        if settings.database or database_url:
            try:
                db_config = {
                    "host": settings.database.host if settings.database else "localhost",
                    "port": settings.database.port if settings.database else 5432,
                    "name": settings.database.name if settings.database else "astra_bot",
                    "user": settings.database.user if settings.database else "",
                    "password": settings.database.password if settings.database else "",
                    "pool_size": (
                        settings.database.pool_size if settings.database else 10
                    ),
                }
                if database_url:
                    # asyncpg требует схему postgresql+asyncpg://, а на
                    # Render приходит postgres:// — нормализуем.
                    if database_url.startswith("postgres://"):
                        database_url = database_url.replace(
                            "postgres://", "postgresql+asyncpg://", 1
                        )
                    elif database_url.startswith("postgresql://"):
                        database_url = database_url.replace(
                            "postgresql://", "postgresql+asyncpg://", 1
                        )
                    db_config["database_url"] = database_url
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

        # Telegram-бот поднимается только если задан токен и хотя бы один
        # админский ID — иначе приложение спокойно работает без него.
        await self._init_telegram(settings)

    async def _init_telegram(self, settings) -> None:
        tg = getattr(settings, "telegram", None)
        if not tg or not tg.bot_token:
            return
        try:
            from astra_bot.telegram.bot import create_telegram_bot

            self._telegram_bot = await create_telegram_bot(
                bot_token=tg.bot_token,
                allowed_user_ids=list(tg.allowed_user_ids or []),
                admin_user_ids=list(tg.admin_user_ids or []),
            )
            await self._telegram_bot.start()
            logger.info("Telegram bot started")
        except Exception as exc:
            # Сбой Telegram не должен ронять весь сервис.
            logger.warning("Telegram bot init failed: %s", exc)
            self._telegram_bot = None

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
        if self._telegram_bot is not None:
            try:
                await self._telegram_bot.stop()
            except Exception as exc:
                logger.warning("Telegram stop failed: %s", exc)
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
