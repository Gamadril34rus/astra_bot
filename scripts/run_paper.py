#!/usr/bin/env python3
"""
Запуск live paper-trading на OKX demo-счёте.

Этот скрипт:
1. Загружает .env;
2. Подключается к OKX;
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

from astra_bot.adapters.okx import OKXClient
from astra_bot.core.instruments import TRADING_UNIVERSE, to_okx
from astra_bot.core.logger import setup_logging
from astra_bot.decision.trading_engine import (
    TradingEngine,
    TradingEngineConfig,
)
from astra_bot.strategies import PullbackStrategy

logger = logging.getLogger("paper_runner")

# 10 ликвидных пар к USDT в OKX-формате.
DEFAULT_SYMBOLS = [to_okx(s) for s in TRADING_UNIVERSE]


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

    api_key = os.environ.get("OKX_API_KEY", "")
    api_secret = os.environ.get("OKX_API_SECRET", "")
    passphrase = os.environ.get(
        "OKX_API_PASSPHRASE",
        os.environ.get("OKX_PASSPHRASE", ""),
    )
    if not all([api_key, api_secret, passphrase]):
        logger.error(
            "OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE не заданы. "
            "Создайте .env из .env.example."
        )
        return 2

    # OKX_DEMO=1 (по умолчанию) — ключи от demo-trading; для реального
    # счёта выставить OKX_DEMO=0 и заменить ключи.
    demo = os.environ.get("OKX_DEMO", "1").lower() not in {"0", "false", "no"}
    okx = OKXClient(
        {
            "api_key": api_key,
            "api_secret": api_secret,
            "passphrase": passphrase,
            "sandbox": demo,
            "enabled": True,
            "rate_limit_qps": 4,
        }
    )
    await okx.initialize()

    # Проверка аккаунта.
    try:
        bals = await okx.get_account_balance()
        usdt = bals.get("USDT")
        if usdt:
            logger.info(
                "OKX demo account connected. USDT: free=%s total=%s",
                usdt.free,
                usdt.total,
            )
        else:
            logger.warning("Баланс USDT не найден — проверьте demo-счёт.")
    except Exception as exc:
        logger.error("Не удалось получить баланс OKX: %s", exc)
        await okx.close()
        return 3

    config = TradingEngineConfig(
        symbols=tuple(args.symbols),
        poll_interval_seconds=args.interval,
    )
    engine = TradingEngine(
        okx=okx,
        pipeline=None,  # TradingEngine соберёт Pipeline из стратегий
        config=config,
    )
    # Подсовываем нашу оптимизированную стратегию.
    engine.pipeline.strategies = [PullbackStrategy()]

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

    await okx.close()
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
