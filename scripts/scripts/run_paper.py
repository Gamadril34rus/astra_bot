#!/usr/bin/env python3
"""
Запуск live paper-trading на данных BingX spot.

Этот скрипт:
1. Загружает .env;
2. Подключается к BingX (публичные рыночные данные + опц. ключи);
3. Собирает рыночные данные по BTC/ETH/SOL;
4. Прогоняет DecisionPipeline;
5. Исполняет сигналы через PaperBroker (виртуальный счёт);
6. Пишет метрики в models/paper_trades.jsonl и models/paper_positions.json;
7. При получении SIGINT/SIGTERM корректно завершается.

Запуск:
    python scripts/run_paper.py
"""

from __future__ import annotations

import argparse
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

from astra_bot.adapters.bingx import BingXClient
from astra_bot.core.instruments import TRADING_UNIVERSE, to_bingx
from astra_bot.core.logger import setup_logging
from astra_bot.decision.trading_engine import (
    TradingEngine,
    TradingEngineConfig,
)
from astra_bot.strategies import PullbackStrategy

logger = logging.getLogger("paper_runner")

# 10 ликвидных пар к USDT в BingX-формате.
DEFAULT_SYMBOLS = [to_bingx(s) for s in TRADING_UNIVERSE]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    p.add_argument("--interval", type=int, default=300, help="polling interval, seconds")
    p.add_argument("--capital", type=float, default=2000.0)
    p.add_argument(
        "--once",
        action="store_true",
        help="Прогнать один цикл и выйти (для теста соединения)",
    )
    return p.parse_args()


async def amain(args: argparse.Namespace) -> int:
    setup_logging()

    # BingX: ключи опциональны (нужны только для баланса спот-счёта).
    # Демо/песочницы spot у BingX нет; бумажные сделки исполняет
    # PaperBroker, на биржу ордера не уходят.
    api_key = os.environ.get("BINGX_API_KEY", "")
    api_secret = os.environ.get("BINGX_API_SECRET", "")
    bingx = BingXClient(
        {
            "api_key": api_key,
            "api_secret": api_secret,
            "enabled": True,
            "rate_limit_qps": 5,
        }
    )
    await bingx.initialize()

    # Проверка аккаунта.
    if api_key and api_secret:
        try:
            bals = await bingx.get_account_balance()
            usdt = bals.get("USDT")
            if usdt:
                logger.info(
                    "BingX spot account connected. USDT: free=%s total=%s",
                    usdt.free,
                    usdt.total,
                )
            else:
                logger.warning("Баланс USDT не найден — проверьте счёт BingX.")
        except Exception as exc:
            logger.error("Не удалось получить баланс BingX: %s", exc)
            await bingx.close()
            return 3
    else:
        logger.info("BINGX_API_KEY не задан — работаю на публичных данных BingX")

    config = TradingEngineConfig(
        symbols=tuple(args.symbols),
        poll_interval_seconds=args.interval,
    )

    # Уведомления в Telegram (если задан токен).
    notifier = None
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_admin = os.environ.get("TELEGRAM_ADMIN_ID", "")
    if tg_token and tg_admin:
        from telegram import Bot

        bot = Bot(token=tg_token)
        admin_ids = [int(x) for x in tg_admin.split(",") if x.strip()]

        def notifier(text: str, severity: str = "info"):
            emoji = {"info": "ℹ️", "warning": "⚠️",
                     "error": "❌", "critical": "🚨"}.get(severity, "📢")
            async def _send():
                for aid in admin_ids:
                    try:
                        await bot.send_message(
                            chat_id=aid,
                            text=f"{emoji} {text}",
                            parse_mode="Markdown",
                        )
                    except Exception as exc:
                        logger.warning("Telegram send failed: %s", exc)
            return asyncio.ensure_future(_send())

    engine = TradingEngine(
        exchange=bingx,
        pipeline=None,  # TradingEngine соберёт Pipeline из стратегий
        config=config,
        notifier=notifier,
    )
    # Подсовываем наши стратегии: scalp даёт частые мелкие сделки на 15m
    # для быстрого обучения, pullback — более крупные на 1h.
    from astra_bot.strategies import (
        MeanReversionStrategy,
        MomentumStrategy,
        Scalp5mStrategy,
        ScalpStrategy,
    )
    engine.pipeline.strategies = [
        Scalp5mStrategy(),
        ScalpStrategy(),
        PullbackStrategy(),
        MomentumStrategy(),
        MeanReversionStrategy(),
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    logger.info(
        "Paper trading запущен: symbols=%s interval=%ss capital=%.0f",
        config.symbols,
        config.poll_interval_seconds,
        args.capital,
    )

    if args.once:
        await engine.step()
        logger.info("Один цикл завершён.")
    else:
        task = asyncio.create_task(engine.run_forever())
        await stop.wait()
        logger.info("Получен сигнал остановки...")
        engine.stop()
        await task

    await bingx.close()
    return 0


def main() -> None:
    args = parse_args()
    try:
        code = asyncio.run(amain(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
