#!/usr/bin/env python3
"""
Multi-timeframe self-play обучение.

Прогоняет виртуальную торговлю на 15m / 1h / 4h / 1d, мержит уроки
в models/lessons.jsonl и переобучает LightGBM. Это нужно, чтобы бот
понимал и краткосрочные движения, и долгосрочный тренд.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.adapters.okx import OKXClient
from astra_bot.core.logger import setup_logging
from astra_bot.ml.historical_training import fetch_historical_candles
from astra_bot.ml.multi_timeframe import run_multi_timeframe
from astra_bot.ml.weekly_learner import train_weekly

SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")


async def amain(args: argparse.Namespace) -> int:
    async def provider(tf: str):
        client = OKXClient({
            "api_key": "", "api_secret": "",
            "sandbox": False, "enabled": True, "rate_limit_qps": 5,
        })
        await client.initialize()
        history = {}
        try:
            for sym in SYMBOLS:
                bars = await fetch_historical_candles(
                    client=client,
                    symbol=sym.replace("/", "-"),
                    timeframe=tf,
                    lookback_days=args.days,
                )
                for b in bars:
                    b.symbol = sym
                history[sym] = bars
        finally:
            await client.close()
        return history

    report = await run_multi_timeframe(
        history_provider=provider,
        timeframes=tuple(args.timeframes),
        target_trades_per_tf=args.target_trades,
        initial_capital=args.capital,
        output_dir=Path("models"),
    )
    for tf, stats in report.per_timeframe.items():
        print(f"{tf}: {stats.get('total_trades', 0)} сделок, PnL={stats.get('total_pnl', 0):.2f}")
    print(f"Всего уроков: {report.total_lessons}, общий PnL: {report.total_pnl:.2f}")

    training = train_weekly(min_samples=args.min_samples)
    if training.trained:
        print(f"Модель {training.version}: AUC={training.roc_auc:.3f}, ACC={training.accuracy:.3f}")
    else:
        print(f"Дообучение пропущено: {training.message}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--capital", type=float, default=2000.0)
    p.add_argument("--target-trades", type=int, default=700)
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument(
        "--timeframes",
        nargs="+",
        default=["15m", "1h", "4h", "1d"],
    )
    return p.parse_args()


def main() -> None:
    setup_logging()
    asyncio.run(amain(parse_args()))


if __name__ == "__main__":
    main()
