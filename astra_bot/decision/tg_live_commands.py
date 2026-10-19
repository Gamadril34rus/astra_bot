"""TG live emergency panel → models/zeus_live_commands.json (enqueue).

Polls getUpdates (TELEGRAM_LIVE_BOT_TOKEN / TELEGRAM_BOT_TOKEN), writes
pending commands for zeus_live_clock.apply_pending. Owner №24: same 4
buttons as paper bot (#230), shared constants from telegram.bot.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from astra_bot.decision.live_commands import enqueue

logger = logging.getLogger(__name__)

OFFSET_PATH = Path("models/zeus_tg_commands_offset.json")

# Button labels — shared with paper MAIN_MENU (owner №24: import, no copy)
try:
    from astra_bot.telegram.bot import (
        BTN_FLAT,
        BTN_RESET_KILL,
        BTN_RESUME_ENTRIES,
        BTN_STOP_ENTRIES,
    )
except Exception:  # pragma: no cover — env without python-telegram-bot
    BTN_RESET_KILL = "🛑 Сброс стоп-крана"
    BTN_FLAT = "🧯 ЗАКРЫТЬ ВСЁ"
    BTN_STOP_ENTRIES = "⏸ Стоп новых входов"
    BTN_RESUME_ENTRIES = "▶️ Продолжить входы"

CMD_MAP = {
    "/stop": "stop",
    "/flat": "flat",
    "/resume": "resume",
    "/reset_kill": "reset_kill",
    "/stop_entries": "stop",
    "/resume_entries": "resume",
}

BTN_TO_CMD = {
    BTN_RESET_KILL: "reset_kill",
    BTN_STOP_ENTRIES: "stop",
    BTN_RESUME_ENTRIES: "resume",
    # BTN_FLAT requires inline confirm — not direct enqueue
}

ACK = {
    "reset_kill": "✅ Принято: стоп-кран сброшу в ≤5 мин. Торговля продолжится.",
    "stop": (
        "✅ Принято: новые входы остановлю в ≤5 мин. "
        "Открытые позиции и стопы продолжают работать."
    ),
    "resume": "✅ Принято: входы продолжатся в ≤5 мин.",
    "flat": "✅ Принято: закрою всё в ≤5 мин.",
}


def _live_status_line() -> str:
    bits: list[str] = []
    try:
        kp = Path("models/kill_switch_state.json")
        if kp.exists():
            raw = json.loads(kp.read_text(encoding="utf-8"))
            reason = raw.get("reason") or raw.get("halt_reason") or "kill"
            bits.append(f"стоп-кран: {reason}")
    except Exception:
        pass
    try:
        bp = Path("models/zeus_live_blocked.json")
        if bp.exists():
            raw = json.loads(bp.read_text(encoding="utf-8"))
            if raw.get("blocked"):
                bits.append(f"входы стоп: {raw.get('reason') or 'stop'}")
    except Exception:
        pass
    if bits:
        return "сейчас стоит: " + "; ".join(bits)
    return "блоков не вижу"


def parse_command(text: str | None) -> str | None:
    """Return canonical cmd or None (slash or button text)."""
    if not text:
        return None
    stripped = text.strip()
    if stripped in BTN_TO_CMD:
        return BTN_TO_CMD[stripped]
    if stripped == BTN_FLAT:
        return None  # handled separately — confirm flow
    first = stripped.split()[0] if stripped else ""
    first = first.split("@", 1)[0].lower()
    return CMD_MAP.get(first)


def load_offset(path: Path = OFFSET_PATH) -> int:
    if not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return int(raw.get("offset") or 0)
    except Exception:
        return 0


def save_offset(offset: int, path: Path = OFFSET_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": int(offset)}), encoding="utf-8")


def _api(
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    post_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if post_json is not None:
        data = json.dumps(post_json).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
    else:
        q = urllib.parse.urlencode(params or {})
        if q:
            url = f"{url}?{q}"
        req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=35) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_reply(
    token: str,
    chat_id: int | str,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"chat_id": str(chat_id), "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    try:
        _api(token, "sendMessage", post_json=payload)
    except Exception as exc:
        logger.warning("send_reply failed: %s", type(exc).__name__)


def main_menu_markup() -> dict[str, Any]:
    """ReplyKeyboardMarkup rows matching paper MAIN_MENU live panel rows."""
    return {
        "keyboard": [
            [{"text": BTN_RESET_KILL}, {"text": BTN_FLAT}],
            [{"text": BTN_STOP_ENTRIES}, {"text": BTN_RESUME_ENTRIES}],
            [{"text": "❓ Помощь"}],
        ],
        "resize_keyboard": True,
    }


def flat_confirm_markup() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "⚠️ Да, закрыть всё", "callback_data": "live_cmd:flat_yes"},
                {"text": "Отмена", "callback_data": "live_cmd:flat_no"},
            ]
        ]
    }


def _is_admin(
    from_id: Any,
    chat_id: Any,
    admin_id: str | None,
) -> bool:
    admin = (admin_id if admin_id is not None else os.environ.get("TELEGRAM_ADMIN_ID") or "").strip()
    if not admin:
        logger.warning("tg cmd ignored: TELEGRAM_ADMIN_ID not set")
        return False
    if from_id is not None and str(from_id) == str(admin):
        return True
    if chat_id is not None and str(chat_id) == str(admin):
        return True
    return False


def _enqueue_and_ack(
    cmd: str,
    *,
    token: str | None,
    chat: Any,
    commands_path: Path | None,
) -> str:
    kwargs: dict[str, Any] = {"source": "tg"}
    if commands_path is not None:
        kwargs["path"] = commands_path
    enqueue(cmd, **kwargs)
    st = _live_status_line()
    ack = ACK.get(cmd, f"✅ Принято: {cmd}")
    if token and chat is not None:
        send_reply(token, chat, f"{ack}\n({st})")
    return cmd


def process_callback(
    update: dict[str, Any],
    *,
    commands_path: Path | None = None,
    token: str | None = None,
    admin_id: str | None = None,
) -> str | None:
    """Handle live_cmd:flat_yes / flat_no callbacks."""
    cq = update.get("callback_query") or {}
    data = str(cq.get("data") or "")
    from_id = (cq.get("from") or {}).get("id")
    msg = cq.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if not data.startswith("live_cmd:"):
        return None
    if not _is_admin(from_id, chat, admin_id):
        if token and chat is not None:
            send_reply(token, chat, "❌ Только для владельца")
        return None
    action = data.split(":", 1)[1]
    # answer callback silently via API if possible
    try:
        if token and cq.get("id"):
            _api(token, "answerCallbackQuery", post_json={"callback_query_id": cq["id"]})
    except Exception:
        pass
    if action == "flat_no":
        if token and chat is not None:
            send_reply(token, chat, "Отмена — позиции не трогаю.")
        return None
    if action == "flat_yes":
        return _enqueue_and_ack("flat", token=token, chat=chat, commands_path=commands_path)
    return None


def process_update(
    update: dict[str, Any],
    *,
    commands_path: Path | None = None,
    token: str | None = None,
    admin_id: str | None = None,
) -> str | None:
    """Parse one update; enqueue if command/button. Returns cmd or None."""
    if update.get("callback_query"):
        return process_callback(
            update, commands_path=commands_path, token=token, admin_id=admin_id
        )

    msg = update.get("message") or update.get("edited_message") or {}
    text = msg.get("text")
    text_s = text if isinstance(text, str) else None
    chat = (msg.get("chat") or {}).get("id")
    from_id = (msg.get("from") or {}).get("id")

    # Help / start → show keyboard
    if text_s and text_s.strip().split("@")[0].split()[0].lower() in {
        "/help",
        "/start",
        "❓ помощь",
        "помощь",
    }:
        if token and chat is not None and _is_admin(from_id, chat, admin_id):
            send_reply(
                token,
                chat,
                "LIVE-пульт: кнопки ниже. Исполнение в ближайший тик (≤5 мин).",
                reply_markup=main_menu_markup(),
            )
        return None

    # Flat button → confirm only
    if text_s and text_s.strip() == BTN_FLAT:
        if not _is_admin(from_id, chat, admin_id):
            if token and chat is not None:
                send_reply(token, chat, "❌ Только для владельца")
            return None
        if token and chat is not None:
            send_reply(
                token,
                chat,
                "Закрыть ВСЕ позиции по рынку прямо сейчас?",
                reply_markup=flat_confirm_markup(),
            )
        return None

    cmd = parse_command(text_s)
    if not cmd:
        return None
    if not _is_admin(from_id, chat, admin_id):
        if token and chat is not None:
            send_reply(token, chat, "❌ Только для владельца")
        logger.info("tg cmd ignored: from_id=%s chat_id=%s not admin", from_id, chat)
        return None
    return _enqueue_and_ack(cmd, token=token, chat=chat, commands_path=commands_path)


def poll_once(
    token: str,
    *,
    timeout_sec: int = 25,
    offset_path: Path = OFFSET_PATH,
    commands_path: Path | None = None,
    send_menu_on_start: bool = False,
) -> int:
    """Long-poll getUpdates once window. Returns number of commands handled."""
    offset = load_offset(offset_path)
    if send_menu_on_start and offset == 0:
        admin = (os.environ.get("TELEGRAM_ADMIN_ID") or "").strip()
        chat = (os.environ.get("TELEGRAM_CHAT_ID") or admin).strip()
        if chat:
            send_reply(
                token,
                chat,
                "LIVE-пульт готов. Кнопки ниже.",
                reply_markup=main_menu_markup(),
            )
    params: dict[str, Any] = {
        "timeout": max(1, int(timeout_sec)),
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }
    if offset:
        params["offset"] = offset
    try:
        data = _api(token, "getUpdates", params)
    except urllib.error.HTTPError as exc:
        logger.warning("getUpdates HTTP %s", exc.code)
        return 0
    except Exception as exc:
        logger.warning("getUpdates failed: %s", type(exc).__name__)
        return 0
    if not data.get("ok"):
        return 0
    handled = 0
    max_id = offset
    admin_id = os.environ.get("TELEGRAM_ADMIN_ID")
    for upd in data.get("result") or []:
        if not isinstance(upd, dict):
            continue
        uid = int(upd.get("update_id") or 0)
        if uid >= max_id:
            max_id = uid + 1
        # Audit: every processed update_id (owner №26 — diagnose triple enqueue)
        logger.info(
            "tg_live_commands update_id=%s has_msg=%s has_cb=%s offset_was=%s",
            uid,
            bool(upd.get("message") or upd.get("edited_message")),
            bool(upd.get("callback_query")),
            offset,
        )
        if process_update(
            upd, commands_path=commands_path, token=token, admin_id=admin_id
        ):
            handled += 1
    if max_id > offset:
        save_offset(max_id, offset_path)
    return handled


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Poll TG for live emergency panel")
    p.add_argument("--timeout", type=int, default=25, help="long-poll seconds")
    p.add_argument(
        "--menu",
        action="store_true",
        help="push MAIN_MENU keyboard to admin chat once if offset=0",
    )
    args = p.parse_args()
    token = (
        os.environ.get("TELEGRAM_LIVE_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or ""
    ).strip()
    if not token:
        logger.info("TELEGRAM_LIVE_BOT_TOKEN missing — skip poll")
        return 0
    n = poll_once(token, timeout_sec=args.timeout, send_menu_on_start=args.menu)
    logger.info("tg_live_commands: handled %d", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
