#!/usr/bin/env python3
"""
Дообучение weekly-модели на уроках self-play.

Вызывается каждый вечер из learning_week.py (или вручную). После запуска:

* читает models/lessons.jsonl;
* обучает LightGBM на актуальных признаках;
* сохраняет модель в models/current.pkl, которую подхватывает self-play.

Если уроков меньше --min-samples, ничего не обучает и пишет причину.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.core.logger import setup_logging
from astra_bot.ml.weekly_learner import train_weekly


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Weekly retraining from lessons")
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--model", default="lightgbm")
    p.add_argument(
        "--lessons",
        type=Path,
        default=Path("models/lessons.jsonl"),
    )
    p.add_argument("--output", type=Path, default=Path("models/current.pkl"))
    return p.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    result = train_weekly(
        lessons_path=args.lessons,
        model_path=args.output,
        min_samples=args.min_samples,
        model_type=args.model,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
