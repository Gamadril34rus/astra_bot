#!/usr/bin/env python3
"""Статус paper-Зевса в Telegram (от имени ASTRA-бота).

Пишет admin'у при важных событиях journal; heartbeat не чаще чем раз в
HEARTBEAT_SEC, чтобы не спамить каждые 5 минут.

Live не трогает. Только research/paper journal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

HEARTBEAT_SEC = 12 * 3600
DIGEST_SEC = 2 * 3600
PUSH_EVENTS = {"entry", "exit", "stop_adjust"}
SIGNAL_SYMBOLS: set[str] = {
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT",
    "LTC-USDT", "BCH-USDT", "NEAR-USDT", "APT-USDT", "SUI-USDT",
    "ARB-USDT", "OP-USDT", "UNI-USDT", "FIL-USDT", "ATOM-USDT",
    "INJ-USDT", "TIA-USDT", "WIF-USDT",
}
STRONG_CONF = 0.85
STATE_PATH = Path("models/zeus_tg_notify_state.json")
JOURNAL_DEFAULT = Path("models/zeus_trade_journal.jsonl")

DIR_RU = {"long": "ЛОНГ", "short": "ШОРТ"}
STAGE_RU = {
    "retest": "ретест",
    "breakout": "пробой",
    "risk": "риск",
    "signal": "сигнал",
    "structure": "структура",
    "pattern": "паттерн",
    "data": "данные",
}
REJECT_REASONS_RU = {
    "no_retest_close_inside": "нет закрытия внутри клина после ретеста",
    "bars_outside_limit": "цена слишком долго держалась вне клина",
    "rr_too_low": "риск/прибыль ниже минимума",
    "no_false_break": "ложного пробоя не было",
}
EXIT_REASONS_RU = {
    "stop_loss": "сработал стоп",
    "take_profit": "сработал тейк",
    "time_stop": "лимит времени в позиции",
    "max_hold": "максимальное время удержания",
    "panic": "аварийное закрытие",
    "closed": "закрыто",
}


def _ru(code: object, table: dict[str, str]) -> str:
    code_s = str(code or "—")
    return table.get(code_s, code_s)


def _ru_dir(code: object) -> str:
    code_s = str(code or "").lower()
    return DIR_RU.get(code_s, code_s or "—")


def _pct(fraction: object) -> str:
    try:
        return f"{float(fraction) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fp(v: object) -> str:
    try:
        return f"{float(v):.5g}"
    except (TypeError, ValueError):
        return "—"


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"last_ts": 0, "last_heartbeat": 0, "last_notified_line": 0}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_ts": 0, "last_heartbeat": 0, "last_notified_line": 0}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _read_journal(path: Path, max_lines: int = 40) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-max_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return rows


def _format_important(rows: list[dict], since_ts: int, only_symbols: bool = False) -> list[str]:
    """События, о которых стоит писать сразу."""
    msgs: list[str] = []
    for r in rows:
        ts = int(r.get("ts") or 0)
        if ts and since_ts and ts <= since_ts:
            continue
        ev = r.get("event")
        if ev not in PUSH_EVENTS:
            continue
        if only_symbols and SIGNAL_SYMBOLS and str(r.get("symbol") or "").upper() not in SIGNAL_SYMBOLS:
            continue
        if only_symbols and ev == "entry":
            try:
                conf = float((r.get("features") or {}).get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf < STRONG_CONF:
                continue
        if only_symbols and ev == "stop_adjust":
            try:
                entry_p = float(str(r.get("why") or "").split("entry=")[-1])
            except (TypeError, ValueError):
                entry_p = 0.0
            if entry_p > 0:
                new_p = float(r.get("new_stop") or 0)
                is_long = str(r.get("direction") or "").lower() == "long"
                locked = new_p >= entry_p if is_long else (new_p <= entry_p and new_p > 0)
                if not locked:
                    continue
        if ev == "entry":
            msgs.append(
                f"🟢 {r.get('symbol')}: {_ru_dir(r.get('direction'))} по ${_fp(r.get('entry_price'))}\n"
                f"стоп ${_fp(r.get('stop_loss'))} · тейк ${_fp(r.get('take_profit'))}"
            )
        elif ev == "exit":
            r_mult = r.get("r_multiple")
            r_part = f"{float(r_mult):+.2f}R · " if r_mult is not None else ""
            msgs.append(
                f"🏁 {r.get('symbol')}: закрыта {_ru_dir(r.get('direction'))} · итог: {r_part}{_ru(r.get('reason'), EXIT_REASONS_RU)}"
            )
        elif ev == "stop_adjust":
            msgs.append(
                f"🔧 {r.get('symbol')}: стоп подтянут ${_fp(r.get('old_stop'))} → ${_fp(r.get('new_stop'))}"
            )
        elif ev == "ltf_impulse" and r.get("near_structure"):
            msgs.append(
                f"⚡ Импульс на 15м {r.get('symbol')} "
                f"({_ru_dir(r.get('direction'))})\n"
                f"диапазон {_pct(r.get('range_pct'))} · "
                f"объём ×{r.get('volume_ratio')}\n"
                f"(память, не вход)"
            )
        elif ev == "structure_state" and r.get("snapshot", {}).get("would_signal"):
            snap = r.get("snapshot") or {}
            msgs.append(
                f"📌 Сигнал готов (пока не сделка): {r.get('symbol')} "
                f"{_ru_dir(snap.get('direction'))} {snap.get('pattern') or ''}\n"
                f"(решение — на шаге paper-исполнения)"
            )
        elif ev == "reject":
            stage = r.get("stage")
            reason = r.get("reason")
            snap = r.get("snapshot") or {}
            # только «почти сетап», не каждый no_wedge
            if stage in ("retest", "breakout", "risk", "signal") or snap.get("has_wedge"):
                if reason in (
                    "no_retest_close_inside",
                    "bars_outside_limit",
                    "rr_too_low",
                    "no_false_break",
                ) or snap.get("has_wedge"):
                    msgs.append(
                        f"⏸ Сигнал отфильтрован ({_ru(stage, STAGE_RU)}): "
                        f"{_ru(reason, REJECT_REASONS_RU)}\n"
                        f"клин: {'есть' if snap.get('has_wedge') else 'нет'} · "
                        f"ширина {_pct(snap.get('width_pct'))}"
                    )
    # дедуп одинаковых подряд
    out: list[str] = []
    prev = ""
    for m in msgs:
        if m != prev:
            out.append(m)
        prev = m
    return out[-8:]  # не раздувать


def _heartbeat_text(rows: list[dict]) -> str:
    return "📊 Zeus paper: тихо, сделок нет"


async def _send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    admin = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()
    if not token or not admin:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_ID missing — skip")
        return False
    try:
        from telegram import Bot
    except ImportError:
        print("python-telegram-bot not installed — skip")
        return False

    bot = Bot(token=token)
    ok = False
    for raw_id in admin.split(","):
        raw_id = raw_id.strip()
        if not raw_id:
            continue
        try:
            # Telegram limit ~4096
            chunk = text if len(text) < 4000 else text[:4000] + "…"
            await bot.send_message(chat_id=int(raw_id), text=chunk)
            ok = True
        except Exception as exc:
            print(f"send failed to {raw_id}: {exc}")
    return ok


async def amain(journal: Path, force_heartbeat: bool) -> int:
    state = _load_state()
    rows = _read_journal(journal)
    since = int(state.get("last_ts") or 0)
    now = int(time.time() * 1000)

    important = _format_important(rows, since)
    sent_any = False

    signals = _format_important(rows, since, only_symbols=True)
    if signals:
        body = "✅ Zeus:\n\n" + "\n\n".join(signals)
        if await _send(body):
            sent_any = True
            max_ts = since
            for r in rows:
                ts = int(r.get("ts") or 0)
                if ts > max_ts:
                    max_ts = ts
            state["last_ts"] = max_ts or now

    digest_due = (now - int(state.get("last_digest") or 0)) >= DIGEST_SEC * 1000
    if digest_due:
        digest = _format_important(rows, int(state.get("last_digest") or 0))
        if digest:
            body = "📊 Zeus paper:\n\n" + "\n\n".join(digest)
            if await _send(body):
                sent_any = True
        state["last_digest"] = now

    last_hb = int(state.get("last_heartbeat") or 0)
    if force_heartbeat or (
        not important and (now - last_hb) >= HEARTBEAT_SEC * 1000
    ):
        if await _send(_heartbeat_text(rows)):
            sent_any = True
            state["last_heartbeat"] = now
            if not state.get("last_ts"):
                state["last_ts"] = now

    if sent_any:
        _save_state(state)
        print("telegram status sent")
    else:
        print("nothing to notify (throttled or quiet)")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--journal", default=str(JOURNAL_DEFAULT))
    p.add_argument(
        "--force-heartbeat",
        action="store_true",
        help="Отправить heartbeat даже если недавно был",
    )
    args = p.parse_args()
    raise SystemExit(asyncio.run(amain(Path(args.journal), args.force_heartbeat)))


if __name__ == "__main__":
    main()
