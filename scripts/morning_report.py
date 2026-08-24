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
        print(text)
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

    text = (
        f"ASTRA BOT — {now} МСК\n\n"
        f"Сделок за сутки: {trades}\n"
        f"В плюс: {wins}\n"
        f"В минус: {losses}\n"
        f"PnL: {pnl:+.2f} USDT\n\n"
        f"Готовность к реальному счёту: {'ДА' if verdict['ready'] else 'НЕТ'}\n"
        f"Demo: {verdict['trading_days']} дн. / {verdict['total_trades']} сделок\n"
        f"Win-rate: {verdict['win_rate']}% | PF: {verdict['profit_factor']} | DD: {verdict['max_drawdown_pct']}%"
    )
    print(text)
    asyncio.run(send_to_telegram(text))


if __name__ == "__main__":
    main()
