#!/usr/bin/env python3
"""Единственный автоматический Telegram-отчёт ASTRA в 09:00 МСК."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

STATE_PATH = Path("models/demo_state.json")


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ADMIN_ID", "").strip() or os.getenv("TELEGRAM_USER_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN и TELEGRAM_ADMIN_ID обязательны")

    if not STATE_PATH.exists():
        text = "ASTRA — утренний отчёт\n\nСостояние demo-trader ещё не создано."
    else:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        text = (
            "ASTRA — утренний отчёт\n\n"
            f"Сделок за сутки: {int(state.get('daily_trades', 0))}\n"
            f"В плюс: {int(state.get('daily_wins', 0))}\n"
            f"В минус: {int(state.get('daily_losses', 0))}\n"
            f"PnL за сутки: {float(state.get('daily_pnl_usdt', 0.0)):.2f} USDT\n\n"
            f"Открытых позиций: {len(state.get('positions', {}))}\n"
            f"Всего сделок: {int(state.get('total_trades', 0))}\n"
            f"Общий PnL: {float(state.get('total_pnl_usdt', 0.0)):.2f} USDT"
        )

    with httpx.Client(timeout=20) as client:
        response = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        response.raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
