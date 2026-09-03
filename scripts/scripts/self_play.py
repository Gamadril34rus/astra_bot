#!/usr/bin/env python3
"""
Запуск self-play обучения бота на годе истории.

Бот «проживает» год бар-за-баром на виртуальные 2000 ₽, делает 2-5 тысяч
ставок по сигналам стратегий и после каждой фиксирует урок: какие
индикаторы/режим рынка привели к прибыли или убытку.

Результат: models/lessons.jsonl и отчёт в stdout (для Telegram).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datetime import UTC

from astra_bot.adapters.bingx import BingXClient
from astra_bot.core.logger import setup_logging
from astra_bot.ml.self_play import (
    SelfPlayConfig,
    SelfPlayEngine,
    format_daily_report,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Self-play обучение на годе истории")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--capital", type=float, default=2000.0)
    p.add_argument("--target-trades", type=int, default=3000)
    p.add_argument("--max-holding", type=int, default=24)
    p.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    p.add_argument("--offline-candles", type=int, default=0,
                   help="Если >0, сгенерировать указанное число баров на инструмент "
                        "без обращения к бирже (для отладки)")
    return p.parse_args()


async def amain(args: argparse.Namespace) -> int:
    from decimal import Decimal

    config = SelfPlayConfig(
        symbols=tuple(args.symbols),
        timeframe=args.timeframe,
        initial_capital=Decimal(str(args.capital)),
        target_trades=args.target_trades,
        max_holding_bars=args.max_holding,
    )
    engine = SelfPlayEngine(config)

    if args.offline_candles:
        import random
        from datetime import datetime

        from astra_bot.core import models

        history = {}
        for i, symbol in enumerate(config.symbols):
            random.seed(i + 1)
            base = 30000.0 if "BTC" in symbol else 2000.0 if "ETH" in symbol else 100.0
            start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
            bars = []
            for j in range(args.offline_candles):
                base *= 1 + random.uniform(-0.005, 0.0055)
                bars.append(models.Candle(
                    exchange="bingx", symbol=symbol, timeframe=config.timeframe,
                    open_time=start + j * 3_600_000,
                    open=Decimal(str(base * 0.999)),
                    high=Decimal(str(base * 1.004)),
                    low=Decimal(str(base * 0.996)),
                    close=Decimal(str(base)),
                    volume=Decimal(str(random.uniform(5, 30))),
                    quote_volume=Decimal("1"),
                ))
            history[symbol] = bars
        report = await engine.run(history=history)
    else:
        client = BingXClient({
            "enabled": True,
            "rate_limit_qps": 5,
        })
        await client.initialize()
        try:
            report = await engine.run(client=client)
        finally:
            await client.close()

    print(format_daily_report(report))
    return 0


def main() -> None:
    setup_logging()
    args = parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
