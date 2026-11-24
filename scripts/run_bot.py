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
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
import traceback
from datetime import UTC

from astra_bot.adapters.bingx import BingXClient
from astra_bot.core import trading_schedule
from astra_bot.core.instruments import TRADING_UNIVERSE, to_bingx
from astra_bot.core.logger import setup_logging
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.decision.force_pattern_only import apply as _force_pattern_only
_force_pattern_only()  # ONLY pattern_4h_retest — zoo offline
from astra_bot.telegram.bot import create_telegram_bot


def _log_error_to_file(exc: Exception, context: str = ""):
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "errors.log"
        import time
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n[{ts}] {context}: {exc}\n")
            f.write(traceback.format_exc())
            f.write(f"\n{'='*60}\n")
    except Exception:
        pass

setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("bot_runner")
LISTEN_SECONDS = int(os.environ.get("BOT_LISTEN_SECONDS", "200"))


def _rotate_state() -> None:
    try:
        from astra_bot.core.state_rotation import rotate_all
        rotate_all(PROJECT_ROOT / "models")
    except Exception as exc:
        logger.warning("State rotation error (не блокирует сессию): %s", exc)


def make_telegram_notifier(bot):
    async def _tg_notifier(text: str, severity: str = "info") -> None:
        if bot and hasattr(bot, "_send_to_admins"):
            try:
                await bot._send_to_admins(text, severity=severity)
            except Exception as exc:
                logger.warning("Trade notification send failed: %s", exc)
    return _tg_notifier


async def amain() -> int:
    _rotate_state()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    admin = os.environ.get("TELEGRAM_ADMIN_ID", "")
    if not token or not admin:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_ID не заданы")
        return 2
    admin_ids = [int(x) for x in admin.split(",") if x.strip()]
    allowed = [int(x) for x in os.environ.get("TELEGRAM_USER_ID", str(admin)).split(",") if x.strip()] or admin_ids
    status = trading_schedule.get_status()
    can_trade = status["can_trade_now"]
    logger.info("Старт: торговля %s | осталось %s ч/мес | %s", "разрешена" if can_trade else "на паузе", status["remaining_hours"], status["now_msk"])
    bingx = BingXClient({
        "api_key": os.environ.get("BINGX_API_KEY", ""),
        "api_secret": os.environ.get("BINGX_API_SECRET", ""),
        "enabled": True,
        "rate_limit_qps": 5,
    })
    try:
        await bingx.initialize()
    except Exception as exc:
        logger.warning("BingX init failed (продолжаю в офлайн-режиме): %s", exc)

    from astra_bot.decision.shared_market_data import bind_shared_bingx_client
    bind_shared_bingx_client(bingx)
    candidates = tuple(to_bingx(s) for s in TRADING_UNIVERSE)
    symbols = candidates
    try:
        available = await bingx.get_instruments()
        if available:
            available_ids = {i.symbol for i in available if getattr(i, "trading_status", "") == "trading"}
            filtered = tuple(s for s in candidates if s in available_ids)
            skipped = tuple(s for s in candidates if s not in available_ids)
            logger.info("BingX perps universe: %d/%d instruments available", len(filtered), len(candidates))
            if skipped:
                logger.warning("Skipped unavailable instruments: %s", ", ".join(skipped))
            if filtered:
                symbols = filtered
            else:
                symbols = candidates
        else:
            symbols = candidates
    except Exception as exc:
        logger.warning("BingX get_instruments failed: %s", exc)
        symbols = candidates

    if not symbols:
        logger.error("No configured instruments")
        try:
            await bingx.close()
        except Exception:
            pass
        return 0

    try:
        _lev_max = max(1, int(os.environ.get("ASTRA_LEVERAGE_MAX", "3")))
    except ValueError:
        _lev_max = 3
    logger.info("Paper контур: потолок плеча %dx", _lev_max)

    from decimal import Decimal as _Dec
    _eng_cfg = TradingEngineConfig(
        symbols=symbols,
        poll_interval_seconds=300,
        leverage_max=_lev_max,
        risk_per_trade_pct=_Dec("0.015"),
    )
    engine = TradingEngine(exchange=bingx, config=_eng_cfg)
    bot = await create_telegram_bot(bot_token=token, allowed_user_ids=allowed, admin_user_ids=admin_ids)
    engine._notifier = make_telegram_notifier(bot)
    engine._trade_cards_enabled = True

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await bot.start()
    except Exception as exc:
        logger.warning("Telegram bot start failed: %s", exc)

    async def trade_loop():
        while not stop.is_set():
            try:
                if trading_schedule.can_trade_now():
                    trading_schedule.tick()
                    await engine.step()
                else:
                    logger.debug("Trading schedule paused")
            except Exception as exc:
                logger.exception("trade_loop step failed: %s", exc)
                _log_error_to_file(exc, "trade_loop")
            try:
                await asyncio.wait_for(stop.wait(), timeout=LISTEN_SECONDS)
            except asyncio.TimeoutError:
                pass

    try:
        await trade_loop()
    finally:
        try:
            await bot.stop()
        except Exception:
            pass
        try:
            await bingx.close()
        except Exception:
            pass
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.exception("Fatal: %s", exc)
        _log_error_to_file(exc, "main")
        return 1


if __name__ == "__main__":
    sys.exit(main())
