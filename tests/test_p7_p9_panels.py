"""P7 paper menu + P9 live console (owner 21.10)."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from astra_bot.decision import tg_live_commands as tlc


def test_paper_main_menu_six_buttons_no_live():
    src = Path("astra_bot/telegram/bot.py").read_text(encoding="utf-8")
    # extract MAIN_MENU keyboard labels
    m = re.search(r"MAIN_MENU = ReplyKeyboardMarkup\(\s*keyboard=\[(.*?)\]\s*,\s*resize_keyboard", src, re.S)
    assert m, "MAIN_MENU not found"
    labels = re.findall(r'KeyboardButton\("([^"]+)"\)', m.group(1))
    assert labels == [
        "📊 Статус",
        "📈 Отчёт",
        "📍 Позиции",
        "💰 Баланс",
        "🛡️ Риск",
        "❓ Помощь",
    ]
    for live in ("🛑", "🧯", "⏸", "▶️"):
        assert not any(live in lb for lb in labels)


def test_live_constants_still_in_bot_source():
    src = Path("astra_bot/telegram/bot.py").read_text(encoding="utf-8")
    assert 'BTN_RESET_KILL =' in src
    assert 'BTN_FLAT =' in src
    assert 'LIVE_FLAT_CONFIRM' in src


def test_live_menu_seven_buttons():
    kb = tlc.main_menu_markup()["keyboard"]
    labels = [c["text"] for row in kb for c in row]
    assert len(labels) == 7
    assert tlc.BTN_BALANCE in labels
    assert tlc.BTN_FLAT in labels
    assert tlc.BTN_STOP_ENTRIES in labels
    assert tlc.BTN_RESUME_ENTRIES in labels
    assert tlc.BTN_FORECAST_DAY in labels
    assert tlc.BTN_FORECAST_WEEK in labels
    assert tlc.BTN_RESET_KILL in labels


def test_parse_aliases_and_immediate(monkeypatch):
    assert tlc.parse_command("💰 Баланс") == "balance"
    assert tlc.parse_command("прогноз на день") == "forecast_day"
    assert tlc.parse_command("/forecast_week") == "forecast_week"
    replies = []
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: replies.append(a[2] if len(a) > 2 else ""))
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")
    upd = {
        "update_id": 1,
        "message": {"text": "💰 Баланс", "from": {"id": 1}, "chat": {"id": 1}},
    }
    cmd = tlc.process_update(upd, token="t", admin_id="1")
    assert cmd == "balance"
    assert replies and "баланс" in replies[0].lower()


def test_menu_flag_push_once(tmp_path, monkeypatch):
    flag = tmp_path / "menu.json"
    monkeypatch.setattr(tlc, "MENU_FLAG_PATH", flag)
    off = tmp_path / "off.json"
    sent = []
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: sent.append(1))
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")
    tlc.maybe_push_menu("tok", offset_path=off)
    assert flag.exists()
    assert len(sent) == 1
    tlc.maybe_push_menu("tok", offset_path=off)
    assert len(sent) == 1
