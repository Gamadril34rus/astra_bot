#!/usr/bin/env python3
"""Paper-clock: только Зевс (wedge false-break retest 4h) на BTC.

НЕ трогает production settings.yaml / live.
Стратегия в коде по умолчанию enabled=False; здесь включаем явно для paper.

Запуск:
    python scripts/run_paper_zeus.py
    python scripts/run_paper_zeus.py --once
    python scripts/run_paper_zeus.py --symbol BTC-USDT --interval 300

Журнал входов/выходов: models/zeus_trade_journal.jsonl
Сопровождение стопа: TradingEngine structural_stop + ExitController SMART
(BREAKEVEN после ~0.8R, trailing ATR) — как «следить за сделкой по графику».
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
from astra_bot.core.logger import setup_logging
from astra_bot.decision.config import DecisionConfig
from astra_bot.decision.pipeline import DecisionPipeline
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.decision.zeus_trade_log import ZeusTradeLog
from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)

logger = logging.getLogger("paper_zeus")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Zeus paper-clock (BTC 4h, no live)")
    p.add_argument("--symbol", default="BTC-USDT", help="BingX swap symbol")
    p.add_argument("--interval", type=int, default=300, help="poll seconds")
    p.add_argument("--capital", type=float, default=2000.0)
    p.add_argument(
        "--once",
        action="store_true",
        help="Один цикл decide/step и выход",
    )
    p.add_argument(
        "--journal",
        default="models/zeus_trade_journal.jsonl",
        help="Путь журнала Зевса",
    )
    return p.parse_args()


async def amain(args: argparse.Namespace) -> int:
    setup_logging()

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

    if not api_key:
        logger.info("BINGX_API_KEY не задан — публичные данные BingX")

    # Узкий paper-контур: 1 символ, 1–2 позиции, structural stop on
    config = TradingEngineConfig(
        symbols=(args.symbol,),
        poll_interval_seconds=args.interval,
        max_open_positions=2,
        structural_stop=True,
        smart_exit_default=True,
    )

    # ТОЛЬКО Зевс — изолированный pipeline, без 41 стратегии и kill-switch шума
    zeus_cfg = ZeusWedgeRetestConfig(enabled=True)
    zeus = ZeusWedgeRetestStrategy(zeus_cfg)
    dcfg = DecisionConfig()
    dcfg.min_rr = 1.5
    dcfg.min_ml_probability = 0.0
    dcfg.min_expected_edge_pct = 0.0
    dcfg.min_ev_r = 0.0  # paper research: не режем по EV
    pipeline = DecisionPipeline(config=dcfg, strategies=[zeus], model=None)

    engine = TradingEngine(
        exchange=bingx,
        pipeline=pipeline,
        config=config,
        notifier=None,
    )

    journal = ZeusTradeLog(args.journal)
    logger.info(
        "Zeus paper-clock: symbol=%s tf=4h enabled=True journal=%s "
        "(prod settings НЕ изменены, strategies=1)",
        args.symbol,
        args.journal,
    )
    journal._write(
        {
            "event": "clock_start",
            "symbol": args.symbol,
            "strategy": "zeus_wedge_retest_4h",
            "note": "paper-only; isolated pipeline; live settings untouched",
        }
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    if args.once:
        await engine.step()
        # tick-маркер: даже без сигнала видно, что цикл прошёл
        journal._write(
            {
                "event": "tick",
                "symbol": args.symbol,
                "strategy": "zeus_wedge_retest_4h",
                "note": "engine.step completed",
            }
        )
        logger.info("Один цикл Zeus paper-clock завершён.")
    else:
        task = asyncio.create_task(engine.run_forever())
        await stop.wait()
        logger.info("Остановка Zeus paper-clock...")
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
