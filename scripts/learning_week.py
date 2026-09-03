#!/usr/bin/env python3
"""
Планировщик «обучения на неделю».

Каждое утро в 08:00 МСК скрипт:

1. тянет свежие бары BingX по всем инструментам за прошедшие сутки;
2. прогоняет по ним self-play и дописывает уроки в models/lessons.jsonl;
3. переобучает LightGBM на накопленном датасете;
4. в 09:00 шлёт утренний отчёт о виртуальном счёте в Telegram.

Запускать как systemd-сервис или через cron:
    @reboot python /app/scripts/learning_week.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.adapters.bingx import BingXClient
from astra_bot.core.logger import setup_logging
from astra_bot.ml.self_play import (
    SelfPlayConfig,
    SelfPlayEngine,
    format_daily_report,
)

logger = logging.getLogger("learning_week")
MOSCOW = ZoneInfo("Europe/Moscow")
TRAIN_TIME = time(8, 0)
REPORT_TIME = time(9, 0)
LESSONS_PATH = PROJECT_ROOT / "models" / "lessons.jsonl"


async def daily_cycle() -> None:
    """Один суточный цикл: self-play → переобучение → отчёт."""
    from astra_bot.ml.weekly_learner import train_weekly

    logger.info("Запускаю суточный цикл обучения")
    client = BingXClient({
        "api_key": "",
        "api_secret": "",
        "sandbox": False,
        "enabled": True,
        "rate_limit_qps": 5,
    })
    await client.initialize()
    report = None
    try:
        engine = SelfPlayEngine(SelfPlayConfig(initial_capital=Decimal("2000")))
        # Берём последние 30 дней, чтобы накопить уроков поверх годовой
        # истории (уроки дописываются в один lessons.jsonl).
        history = await engine.load_history(client, lookback_days=30)
        report = await engine.run(history=history)
        logger.info("Сгенерировано %d новых уроков", report.total_trades)
    finally:
        await client.close()

    # Переобучаем модель на актуальных уроках — теперь следующий заход
    # self-play будет использовать её как фильтр входов.
    training = train_weekly(min_samples=200)
    if report is not None:
        text = format_daily_report(report)
        if training.trained:
            text += (
                f"\n\n🧠 *Модель переобучена:* {training.version}\n"
                f"   AUC={training.roc_auc:.3f}, "
                f"accuracy={training.accuracy:.3f}, "
                f"win-rate={training.positive_rate*100:.1f}%"
            )
        else:
            text += f"\n\n🧠 {training.message}"
        await _send_telegram(text)


async def _send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    admin_raw = os.environ.get("TELEGRAM_ADMIN_ID", "")
    if not token or not admin_raw:
        logger.warning("TELEGRAM_BOT_TOKEN/ADMIN_ID не заданы, отчёт в stdout:\n%s", text)
        return
    from telegram import Bot

    bot = Bot(token=token)
    for admin_id in [int(x) for x in admin_raw.split(",") if x.strip()]:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        except Exception as exc:  # pragma: no cover - сеть
            logger.error("Не отправил отчёт %s: %s", admin_id, exc)


def _seconds_until(target: time) -> float:
    now = datetime.now(tz=MOSCOW)
    when = datetime.combine(now.date(), target, tzinfo=MOSCOW)
    if when <= now:
        when += timedelta(days=1)
    return (when - now).total_seconds()


async def loop_forever() -> None:
    logger.info("Планировщик обучения запущен (Москва)")
    while True:
        await asyncio.sleep(_seconds_until(TRAIN_TIME))
        try:
            await daily_cycle()
        except Exception:
            logger.exception("Суточный цикл упал")
        # Ждём 09:00 и шлём отчёт по уже накопленным урокам.
        await asyncio.sleep(_seconds_until(REPORT_TIME))


def main() -> None:
    setup_logging()
    asyncio.run(loop_forever())


if __name__ == "__main__":
    main()
