#!/usr/bin/env python3
"""Короткие непрерывные paper-сессии ASTRA на GitHub Actions."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
from astra_bot.adapters.okx import OKXClient
from astra_bot.core import trading_schedule
from astra_bot.core.instruments import TRADING_UNIVERSE, to_okx
from astra_bot.core.logger import setup_logging
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.telegram.bot import create_telegram_bot

setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("bot_runner")
LISTEN_SECONDS = int(os.environ.get("BOT_LISTEN_SECONDS", "200"))

async def amain() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    admin = os.environ.get("TELEGRAM_ADMIN_ID", "")
    if not token or not admin:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_ID не заданы")
        return 2
    admin_ids = [int(x) for x in admin.split(",") if x.strip()]
    allowed = [int(x) for x in os.environ.get("TELEGRAM_USER_ID", str(admin)).split(",") if x.strip()] or admin_ids
    status = trading_schedule.get_status(); can_trade = status["can_trade_now"]
    logger.info("Старт: торговля %s | осталось %s ч/мес | %s", "разрешена" if can_trade else "на паузе", status["remaining_hours"], status["now_msk"])
    okx = OKXClient({
        "api_key": os.environ.get("OKX_API_KEY", ""),
        "api_secret": os.environ.get("OKX_API_SECRET", ""),
        "passphrase": os.environ.get("OKX_API_PASSPHRASE", os.environ.get("OKX_PASSPHRASE", "")),
        "sandbox": os.environ.get("OKX_DEMO", "1").lower() not in {"0", "false", "no"},
        "enabled": True,
        "rate_limit_qps": 4,
    })
    await okx.initialize()
    # Never send a delisted/unsupported symbol to the candle endpoint. OKX is
    # the source of truth; the static universe is only a candidate list.
    candidates = tuple(to_okx(s) for s in TRADING_UNIVERSE)
    available = await okx.get_instruments()
    available_ids = {i.symbol for i in available if getattr(i, "trading_status", "") in {"trading", "live"}}
    symbols = tuple(s for s in candidates if s in available_ids)
    skipped = tuple(s for s in candidates if s not in available_ids)
    logger.info("OKX spot universe: %d/%d instruments available", len(symbols), len(candidates))
    if skipped:
        logger.warning("Skipped unavailable instruments: %s", ", ".join(skipped))
    if not symbols:
        logger.error("No configured instruments are currently tradable on OKX")
        await okx.close(); return 3
    engine = TradingEngine(okx=okx, config=TradingEngineConfig(symbols=symbols, poll_interval_seconds=300))
    bot = await create_telegram_bot(bot_token=token, allowed_user_ids=allowed, admin_user_ids=admin_ids)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError: pass
    await bot.start()
    async def trade_loop():
        while not stop.is_set():
            try:
                if trading_schedule.can_trade_now():
                    trading_schedule.tick(); await engine.step()
                else:
                    logger.info("Вне торгового расписания — шаг пропущен")
            except Exception as exc:
                logger.exception("Ошибка торгового шага: %s", exc)
            try: await asyncio.wait_for(stop.wait(), timeout=45)
            except TimeoutError: pass
    trade_task = asyncio.create_task(trade_loop())
    try:
        await asyncio.wait_for(stop.wait(), timeout=LISTEN_SECONDS)
    except TimeoutError: pass
    finally:
        stop.set(); trade_task.cancel()
        try: await trade_task
        except (asyncio.CancelledError, Exception): pass
        await bot.stop(); await okx.close()
    logger.info("Сессия завершена.")
    return 0

def main() -> None:
    try: code = asyncio.run(amain())
    except KeyboardInterrupt: code = 0
    sys.exit(code or 0)
if __name__ == "__main__": main()
