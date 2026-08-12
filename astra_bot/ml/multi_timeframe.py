"""
ASTRA BOT — Multi-timeframe training.

Self-play обычно работает на 1h. Чтобы бот понимал и долгосрочный
тренд, и короткие движения, этот модуль запускает self-play на
нескольких таймфреймах (15m, 1h, 4h, 1d) и мержит уроки в один
датасет. Каждый урок получает тег ``timeframe``, и модель учится
различать поведение на разных горизонтах.

За счёт этого:
* на 15m-5m бот ловит короткие движения;
* на 4h-1d понимает макро-тренд;
* итоговая модель используется в daily_plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .self_play import SelfPlayConfig, SelfPlayEngine

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAMES = ("15m", "1h", "4h", "1d")


@dataclass
class MultiTimeframeReport:
    per_timeframe: dict[str, dict[str, Any]]
    total_lessons: int
    total_pnl: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_timeframe": self.per_timeframe,
            "total_lessons": self.total_lessons,
            "total_pnl": self.total_pnl,
        }


async def run_multi_timeframe(
    history_provider,
    *,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    target_trades_per_tf: int = 700,
    initial_capital=2000.0,
    output_dir: Path = Path("models"),
) -> MultiTimeframeReport:
    """Прогнать self-play на нескольких таймфреймах и слить уроки.

    ``history_provider(symbol, timeframe, lookback_days)`` должен
    возвращать список свечей. Это позволяет запускать обучение как
    на реальных данных OKX, так и на синтетике.
    """
    from decimal import Decimal

    output_dir.mkdir(parents=True, exist_ok=True)
    reports: dict[str, dict[str, Any]] = {}
    total_lessons = 0
    total_pnl = 0.0

    for tf in timeframes:
        lessons_path = output_dir / f"lessons_{tf}.jsonl"
        config = SelfPlayConfig(
            timeframe=tf,
            initial_capital=Decimal(str(initial_capital)),
            target_trades=target_trades_per_tf,
            lessons_output=lessons_path,
        )
        engine = SelfPlayEngine(config)
        history = await history_provider(tf)
        logger.info("Self-play на %s: %d баров", tf, sum(len(v) for v in history.values()))
        report = await engine.run(history=history, append=False)

        reports[tf] = report.to_dict() if hasattr(report, "to_dict") else {
            "trades": report.total_trades,
            "pnl": report.total_pnl,
            "win_rate": report.win_rate,
        }
        total_lessons += report.total_trades
        total_pnl += report.total_pnl

    # Помечаем каждый урок его таймфреймом — в итоговом JSONL.
    merged_path = output_dir / "lessons.jsonl"
    _merge_timeframe_lessons(output_dir, merged_path, timeframes)

    return MultiTimeframeReport(
        per_timeframe=reports,
        total_lessons=total_lessons,
        total_pnl=total_pnl,
    )


def _merge_timeframe_lessons(
    output_dir: Path,
    merged_path: Path,
    timeframes: tuple[str, ...],
) -> None:
    """Слить lessons_<tf>.jsonl в один lessons.jsonl с тегом timeframe."""
    import json

    with open(merged_path, "w", encoding="utf-8") as out:
        for tf in timeframes:
            path = output_dir / f"lessons_{tf}.jsonl"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    row["timeframe"] = tf
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("Слил уроки таймфреймов в %s", merged_path)
