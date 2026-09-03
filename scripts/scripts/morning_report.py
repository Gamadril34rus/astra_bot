#!/usr/bin/env python3
"""Единый утренний отчёт ASTRA BOT, 09:00 МСК."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from astra_bot.core import readiness

from telegram import Bot

STATE_PATH = Path("models/demo_state.json")
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "daily_trades": 0,
            "daily_wins": 0,
            "daily_losses": 0,
            "daily_pnl": 0.0,
        }
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def send_to_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    admin_raw = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()
    if not token or not admin_raw:
        # Без токена отправка невозможна; текст main() уже вывел в лог.
        return

    bot = Bot(token=token)
    for raw_id in admin_raw.split(","):
        raw_id = raw_id.strip()
        if raw_id:
            await bot.send_message(chat_id=int(raw_id), text=text)


def main() -> None:
    state = load_state()
    now = datetime.now(tz=MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
    trades = int(state.get("daily_trades", 0))
    wins = int(state.get("daily_wins", 0))
    losses = int(state.get("daily_losses", 0))
    pnl = float(state.get("daily_pnl", state.get("daily_pnl_usdt", 0.0)))
    verdict = readiness.evaluate()

    # «Чему научилась» вместо счётчиков: уроки, переходы гипотез,
    # исходы NO_TRADE, база знаний (EV по strategy × regime).
    from astra_bot.ml.learning_digest import (
        build_digest,
        save_watermark,
    )

    models_dir = Path("models")
    digest, new_wm = build_digest(models_dir)

    text = (
        f"ASTRA BOT — {now} МСК\n\n"
        f"{digest}\n\n"
        f"📊 Сделок за сутки: {trades} (+{wins}/−{losses}), PnL: {pnl:+.2f} USDT\n"
        f"🎯 Готовность: {'ДА' if verdict['ready'] else 'НЕТ'} "
        f"({verdict.get('score', '?')}/{verdict.get('threshold', '?')}) | "
        f"{verdict['trading_days']} дн. | WR {verdict['win_rate']}% | "
        f"PF {verdict['profit_factor']} | DD {verdict['max_drawdown_pct']}%"
    )
    # Вывод: в логе CI (или в консоль, если токена нет — fallback внутри
    # send_to_telegram). Не дублируем.
    print(text)
    asyncio.run(send_to_telegram(text))
    # Watermark только после успешной отправки: сбой → повторное
    # окно при следующем запуске, знания не теряются.
    try:
        save_watermark(models_dir, new_wm)
    except Exception as exc:
        print(f"watermark: {exc}")


if __name__ == "__main__":
    main()
