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
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from astra_bot.decision.live_commands import (
    enqueue,
)

logger = logging.getLogger(__name__)

OFFSET_PATH = Path("models/zeus_live_tg_offset.json")  # P8: live-only; never share with paper

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

# P9 live console labels (owner 21.10) — override paper strings for LIVE bot only
BTN_BALANCE = "💰 Баланс"
BTN_FORECAST_DAY = "📅 Прогноз на день"
BTN_FORECAST_WEEK = "📆 Прогноз на неделю"
BTN_FLAT = "🧯 Стоп всех сделок"
BTN_STOP_ENTRIES = "⏸ Пауза торговли"
BTN_RESUME_ENTRIES = "▶️ Продолжить входы"
BTN_RESET_KILL = "🛑 Сброс крана"

CMD_MAP = {
    "/stop": "stop",
    "/flat": "flat",
    "/resume": "resume",
    "/reset_kill": "reset_kill",
    "/stop_entries": "stop",
    "/resume_entries": "resume",
    "/balance": "balance",
    "/forecast_day": "forecast_day",
    "/forecast_week": "forecast_week",
}

# immediate (no enqueue) informational cmds
IMMEDIATE_CMDS = frozenset({"balance", "forecast_day", "forecast_week"})

BTN_TO_CMD = {
    BTN_RESET_KILL: "reset_kill",
    BTN_STOP_ENTRIES: "stop",
    BTN_RESUME_ENTRIES: "resume",
    BTN_BALANCE: "balance",
    BTN_FORECAST_DAY: "forecast_day",
    BTN_FORECAST_WEEK: "forecast_week",
    # BTN_FLAT requires inline confirm — not direct enqueue
}

# text aliases (lowercase)
TEXT_ALIASES = {
    "баланс": "balance",
    "прогноз на день": "forecast_day",
    "прогноз на неделю": "forecast_week",
    "сброс крана": "reset_kill",
    "пауза торговли": "stop",
    "продолжить входы": "resume",
    "стоп всех сделок": "flat",
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


MENU_FLAG_PATH = Path("models/zeus_live_menu_pushed.json")
HTTP_WARN_FLAG = Path("models/zeus_live_tg_http_warn.json")


def _fmt_balance() -> str:
    """Immediate balance from positions/equity files (no network). F2.4: freshness stamp."""
    try:
        eq = None
        pos_n = 0
        data_ts: float | None = None
        for cand in (
            Path("models/zeus_live_positions.json"),
            Path("models/zeus_live_equity.json"),
        ):
            if not cand.exists():
                continue
            raw = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if "equity" in raw:
                    eq = raw.get("equity")
                for key in ("ts", "updated", "updated_at", "time"):
                    if raw.get(key) is not None:
                        try:
                            data_ts = float(raw[key])
                            if data_ts > 1e12:
                                data_ts /= 1000.0
                        except (TypeError, ValueError):
                            pass
                        break
                if data_ts is None:
                    data_ts = cand.stat().st_mtime
                plans = raw.get("positions") or raw.get("plans") or raw
                if isinstance(plans, dict):
                    pos_n = len([k for k in plans if k not in ("equity", "ts", "updated")])
                elif isinstance(plans, list):
                    pos_n = len(plans)
        jp = Path("models/zeus_live_journal.jsonl")
        if eq is None and jp.exists():
            for line in reversed(jp.read_text(encoding="utf-8").splitlines()[-50:]):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("equity") is not None:
                    eq = row["equity"]
                    if data_ts is None:
                        try:
                            data_ts = float(row.get("ts") or jp.stat().st_mtime)
                            if data_ts > 1e12:
                                data_ts /= 1000.0
                        except (TypeError, ValueError):
                            data_ts = jp.stat().st_mtime
                    break
        eq_s = f"{float(eq):.2f}$" if eq is not None else "н/д"
        msk = timezone(timedelta(hours=3))
        if data_ts is None:
            hhmm = datetime.now(msk).strftime("%H:%M")
            age_note = " (нет метки времени)"
        else:
            hhmm = datetime.fromtimestamp(data_ts, tz=msk).strftime("%H:%M")
            age = _time.time() - data_ts
            if age > 15 * 60:
                age_note = " ⚠️ данные устарели"
            elif age > 5 * 60:
                age_note = " (несвежие)"
            else:
                age_note = ""
        prefix = "⚠️ данные устарели. " if age_note.startswith(" ⚠️") else ""
        return f"{prefix}💰 LIVE баланс: {eq_s}; открыто позиций: {pos_n}; данные на {hhmm} МСК{age_note.replace(' ⚠️ данные устарели', '')}"
    except Exception as exc:
        return f"💰 Баланс временно недоступен ({type(exc).__name__})"


def _fmt_forecast(horizon: str) -> str:
    """P9: informational forecast from existing digest/status modules only."""
    lines = [f"{'📅' if horizon == 'day' else '📆'} Прогноз на {'день' if horizon == 'day' else 'неделю'} (справка, не сигнал):"]
    # readiness
    try:
        rp = Path("models/readiness.json")
        if rp.exists():
            raw = json.loads(rp.read_text(encoding="utf-8"))
            lines.append(f"готовность: {raw.get('status') or raw.get('level') or 'см. readiness.json'}")
    except Exception:
        pass
    # digest last
    try:
        dp = Path("models/zeus_live_digest.json")
        if dp.exists():
            raw = json.loads(dp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                lines.append(f"последний свод: {raw.get('last') or raw.get('key') or list(raw.keys())[:3]}")
    except Exception:
        pass
    # HTF / wedge shadow counts if present
    try:
        sp = Path("models/zeus_wedge_1h_signals.jsonl")
        if sp.exists():
            n = sum(1 for _ in sp.open(encoding="utf-8") if _.strip())
            lines.append(f"1h-тень сигналов в журнале: {n}")
    except Exception:
        pass
    # positions risk snapshot
    try:
        pp = Path("models/zeus_live_positions.json")
        if pp.exists():
            raw = json.loads(pp.read_text(encoding="utf-8"))
            plans = raw.get("positions") or raw.get("plans") or raw
            if isinstance(plans, dict):
                syms = [k for k in plans if isinstance(plans.get(k), dict)]
                if syms:
                    lines.append("открыто: " + ", ".join(syms[:8]))
    except Exception:
        pass
    if len(lines) == 1:
        lines.append("данных мало — смотри утренний/вечерний LIVE-свод и paper-статус.")
    return "\n".join(lines)


def _handle_immediate(cmd: str, token: str, chat: Any) -> None:
    if cmd == "balance":
        send_reply(token, chat, _fmt_balance(), reply_markup=main_menu_markup())
    elif cmd == "forecast_day":
        send_reply(token, chat, _fmt_forecast("day"), reply_markup=main_menu_markup())
    elif cmd == "forecast_week":
        send_reply(token, chat, _fmt_forecast("week"), reply_markup=main_menu_markup())


def maybe_push_menu(token: str, *, offset_path: Path = OFFSET_PATH) -> None:
    """P4: push keyboard once until flag file written (retry on fail)."""
    try:
        if MENU_FLAG_PATH.exists():
            return
        # push when no flag (first run / after wipe); offset may already be live-native
        admin = (os.environ.get("TELEGRAM_ADMIN_ID") or "").strip()
        chat = (os.environ.get("TELEGRAM_CHAT_ID") or admin).strip()
        if not chat:
            return
        ok = send_reply(
            token,
            chat,
            "LIVE-пульт готов. Кнопки ниже.",
            reply_markup=main_menu_markup(),
        )
        if not ok:
            logger.warning("menu push failed (retry next tick): send_reply False")
            return
        MENU_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        MENU_FLAG_PATH.write_text(
            json.dumps({"ts": __import__("time").time(), "ok": True}),
            encoding="utf-8",
        )
        logger.info("tg_live_commands menu pushed → %s", MENU_FLAG_PATH)
    except Exception as exc:
        logger.warning("menu push failed (retry next tick): %s", type(exc).__name__)



def parse_command(text: str | None) -> str | None:
    """Return canonical cmd or None (slash or button text)."""
    if not text:
        return None
    stripped = text.strip()
    if stripped in BTN_TO_CMD:
        return BTN_TO_CMD[stripped]
    if stripped == BTN_FLAT:
        return None  # handled separately — confirm flow
    low = stripped.lower()
    if low in TEXT_ALIASES:
        return TEXT_ALIASES[low]
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
) -> bool:
    """Send TG message. Returns True on success, False on failure (never raises)."""
    payload: dict[str, Any] = {"chat_id": str(chat_id), "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    try:
        _api(token, "sendMessage", post_json=payload)
        return True
    except Exception as exc:
        logger.warning("send_reply failed: %s", type(exc).__name__)
        return False


def main_menu_markup() -> dict[str, Any]:
    """P9 live console (owner 21.10): 7 buttons 2+2+2+1."""
    return {
        "keyboard": [
            [{"text": BTN_BALANCE}, {"text": BTN_FLAT}],
            [{"text": BTN_STOP_ENTRIES}, {"text": BTN_RESUME_ENTRIES}],
            [{"text": BTN_FORECAST_DAY}, {"text": BTN_FORECAST_WEEK}],
            [{"text": BTN_RESET_KILL}],
        ],
        "resize_keyboard": True,
        "input_field_placeholder": "LIVE-пульт",
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
) -> str | None:
    """Enqueue + ack. None if deduped — no TG ack (owner №26)."""
    kwargs: dict[str, Any] = {"source": "tg"}
    if commands_path is not None:
        kwargs["path"] = commands_path
    ok = enqueue(cmd, **kwargs)
    if ok is False:
        logger.info("enqueue dedup %s — no ack", cmd)
        return None
    st = _live_status_line()
    ack = ACK.get(cmd, f"✅ Принято: {cmd}")
    if token and chat is not None:
        send_reply(token, chat, ack + "\n(" + st + ")")
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

    cmd = parse_command(text_s)
    # F2.2: ANY path to flat (button/alias/slash) → inline confirm first, never enqueue here
    if (text_s and text_s.strip() == BTN_FLAT) or cmd == "flat":
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

    if not cmd:
        return None
    if not _is_admin(from_id, chat, admin_id):
        if token and chat is not None:
            send_reply(token, chat, "❌ Только для владельца")
        logger.info("tg cmd ignored: from_id=%s chat_id=%s not admin", from_id, chat)
        return None
    # P9: balance / forecast — immediate reply, no enqueue
    if cmd in IMMEDIATE_CMDS:
        if token and chat is not None:
            _handle_immediate(cmd, token, chat)
        return cmd
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
    # P8: own offset file only. Missing file → migrate with offset=0/floor=0
    # (ignore legacy zeus_tg_commands_offset.json and poisoned last_uid ~774M).
    own_file = offset_path.exists()
    offset = load_offset(offset_path) if own_file else 0
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
    # P4: menu push with flag-file retry
    maybe_push_menu(token, offset_path=offset_path)
    try:
        data = _api(token, "getUpdates", params)
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        logger.warning("getUpdates HTTP %s", code)
        # V1: 401/403/404 → ⚠️ once/day + non-zero via main(); 409 = standby silence
        if code in (401, 403, 404):
            try:
                import time as _t
                warn = True
                if HTTP_WARN_FLAG.exists():
                    prev = json.loads(HTTP_WARN_FLAG.read_text(encoding="utf-8"))
                    if _t.time() - float(prev.get("ts") or 0) < 86400:
                        warn = False
                if warn:
                    admin = (os.environ.get("TELEGRAM_ADMIN_ID") or "").strip()
                    chat = (os.environ.get("TELEGRAM_CHAT_ID") or admin).strip()
                    sent_ok = False
                    if chat:
                        sent_ok = send_reply(
                            token,
                            chat,
                            f"⚠️ поллер live-пульта: getUpdates HTTP {code} — канал ослеп",
                        )
                    # F2.3: flag only after successful warn — else retry next tick
                    if sent_ok or not chat:
                        HTTP_WARN_FLAG.write_text(
                            json.dumps({"ts": _t.time(), "code": code}), encoding="utf-8"
                        )
            except Exception:
                pass
            raise SystemExit(1) from exc  # F1.1: non-zero exit (string SystemExit is also 1; keep explicit)
        return 0
    except Exception as exc:
        logger.warning("getUpdates failed: %s", type(exc).__name__)
        return 0
    if not data.get("ok"):
        return 0
    handled = 0
    # P8.8: max_id from 0 (or processed uids), never seed from foreign offset
    max_id = 0
    admin_id = os.environ.get("TELEGRAM_ADMIN_ID")
    # P8: floor only from own offset file; on migration ignore commands last_uid
    if own_file:
        floor = max(0, offset - 1)
    else:
        floor = 0
        logger.info("tg_live_commands P8 migrate: no %s → floor=0", offset_path)
    skipped = 0
    for upd in data.get("result") or []:
        if not isinstance(upd, dict):
            continue
        uid = int(upd.get("update_id") or 0)
        if uid >= max_id:
            max_id = uid + 1
        logger.info(
            "tg_live_commands update_id=%s floor=%s offset_was=%s own_file=%s",
            uid,
            floor,
            offset,
            own_file,
        )
        if uid <= floor:
            skipped += 1
            logger.info("tg_live_commands skip uid=%s <= floor=%s", uid, floor)
            continue
        if process_update(
            upd, commands_path=commands_path, token=token, admin_id=admin_id
        ):
            handled += 1
        # advance own offset watermark (live-native uid space only)
        floor = max(floor, uid)
    if max_id > offset:
        save_offset(max_id, offset_path)
    elif not own_file and max_id > 0:
        # migration: persist even if equal (first write)
        save_offset(max_id, offset_path)
    if skipped:
        logger.warning("tg_live_commands skipped %s update(s) uid<=floor=%s", skipped, floor)
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
