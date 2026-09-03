#!/usr/bin/env python3
"""
Скрипт первичного обучения ML-модели на годе истории BingX (публичные свечи).

Депозит не требуется: используются публичные свечи и walk-forward
разметка будущих движений цены.

Пример:
    python scripts/train_historical.py --symbol BTC/USDT --timeframe 1h --days 365
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.core.logger import get_component_logger, setup_logging
from astra_bot.ml.historical_training import (
    HistoricalTrainingConfig,
    train_on_historical_data,
)

logger = get_component_logger("train_historical")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Обучение ML-модели на годовалой истории без депозита"
    )
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--model", default="lightgbm", choices=["lightgbm", "xgboost", "random_forest"])
    parser.add_argument("--min-samples", type=int, default=200)
    parser.add_argument("--output-dir", default="models")
    return parser.parse_args()


async def amain(args: argparse.Namespace) -> int:
    config = HistoricalTrainingConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        lookback_days=args.days,
        model_type=args.model,
        min_samples=args.min_samples,
        output_dir=args.output_dir,
    )
    artifact = await train_on_historical_data(config)
    logger.info("Обучение завершено. Артефакт: %s", artifact)
    return 0


def main() -> None:
    setup_logging()
    args = parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
