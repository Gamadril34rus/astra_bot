#!/usr/bin/env python3
"""
ASTRA BOT - Main Entry Point
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

# Подстраховка для запуска `python main.py` из произвольной рабочей директории.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.core.api_auth import ApiKeyMiddleware
from astra_bot.core.config import get_settings
from astra_bot.core.logger import get_component_logger, setup_logging
from astra_bot.core.metrics import SYSTEM_ERRORS, render_metrics
from astra_bot.core.request_context import set_request_id
from astra_bot.main import AstraBot
from fastapi import FastAPI, HTTPException, Request, Response
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
        # ASTRA_CONTINUOUS=1: непрерывный торговый цикл в фоне (24/7).
        # По умолчанию выключен — Render/CI дёргают /tick по расписанию.
        continuous = os.environ.get("ASTRA_CONTINUOUS") == "1"
        if continuous and _bot_instance._trading_engine is not None:
            _bot_instance._running = True
            _bot_instance._background_tasks.add(
                asyncio.create_task(_bot_instance._run())
            )
            logger.warning(
                "Continuous trading loop STARTED (ASTRA_CONTINUOUS=1): "
                "тик каждые %s сек",
                get_settings().market_data.tick_interval_seconds,
            )
        await _init_telegram(_bot_instance)
        logger.info("ASTRA BOT ready")
        yield
    finally:
        if _bot_instance is not None:
            tg_bot = getattr(_bot_instance, "_telegram_bot", None)
            if tg_bot is not None:
                try:
                    await tg_bot.stop()
                except Exception:
                    pass
            await _bot_instance.stop()
        logger.info("ASTRA BOT shut down")


async def _init_telegram(bot) -> None:
    """Telegram-бот веб-сервиса (webhook на Render / polling локально).

    Пакетный AstraBot Telegram не поднимает — раньше это делала
    легаси-копия класса здесь; функциональность сохранена.
    """
    settings = get_settings()
    tg = getattr(settings, "telegram", None)
    if not tg or not tg.bot_token:
        return
    try:
        from astra_bot.telegram.bot import create_telegram_bot

        bot._telegram_bot = await create_telegram_bot(
            bot_token=tg.bot_token,
            allowed_user_ids=list(tg.allowed_user_ids or []),
            admin_user_ids=list(tg.admin_user_ids or []),
        )
        base_url = (
            os.environ.get("RENDER_EXTERNAL_URL")
            or os.environ.get("WEBHOOK_BASE_URL")
            or ""
        ).rstrip("/")
        webhook_url = f"{base_url}/telegram/webhook" if base_url else None
        await bot._telegram_bot.start(webhook_url=webhook_url)
        logger.info("Telegram bot started (webhook=%s)", bool(webhook_url))
    except Exception as exc:
        logger.warning("Telegram bot init failed: %s", exc)
        bot._telegram_bot = None


# FastAPI приложение
app = FastAPI(title="ASTRA BOT", version="1.0.0", lifespan=lifespan)
app.add_middleware(ApiKeyMiddleware)


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
    # Реальный торговый шаг: данные → pipeline → risk → PaperBroker.
    # Внутри троттлинг по tick_interval_seconds (повторный вызов — no-op).
    try:
        await _bot_instance._tick()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "engine": _bot_instance._trading_engine is not None,
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Приём обновлений от Telegram в режиме webhook."""
    tg_bot = getattr(_bot_instance, "_telegram_bot", None) if _bot_instance else None
    if tg_bot is None:
        return JSONResponse(status_code=503, content={"status": "bot not ready"})
    try:
        data = await request.json()
        await tg_bot.process_update(data)
    except Exception as exc:
        logger.exception("Telegram webhook error: %s", exc)
        return JSONResponse(status_code=500, content={"status": "error"})
    return {"status": "ok"}


@app.get("/status")
async def status():
    global _bot_instance
    if _bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    status = await _bot_instance.get_status()
    engine = _bot_instance._trading_engine
    if engine is not None:
        broker = engine.broker
        status["equity"] = str(broker.equity)
        status["realized_pnl"] = str(broker.realized_pnl)
        status["open_positions"] = len(broker.positions)
        status["strategies_loaded"] = len(getattr(engine.pipeline, "strategies", []))
        status["simulated"] = os.environ.get("ASTRA_SIMULATE") == "1"
    return status


@app.middleware("http")
async def add_request_id(request, call_next):
    """Пробросить/сгенерировать X-Request-Id для трассировки в логах."""
    request_id = request.headers.get("X-Request-Id") or os.urandom(8).hex()
    set_request_id(request_id)
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
    """Запустить обучение на истории BingX без депозита.

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



def run_web_mode():
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    logger.info(f"Starting web server on {host}:{port}")
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_web_mode()
