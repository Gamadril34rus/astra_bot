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

HEARTBEAT_SEC = 2 * 3600  # не чаще раза в 2 часа, если тихо
STATE_PATH = Path("models/zeus_tg_notify_state.json")
JOURNAL_DEFAULT = Path("models/zeus_trade_journal.jsonl")


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


def _format_important(rows: list[dict], since_ts: int) -> list[str]:
    """События, о которых стоит писать сразу."""
    msgs: list[str] = []
    for r in rows:
        ts = int(r.get("ts") or 0)
        if ts and since_ts and ts <= since_ts:
            continue
        ev = r.get("event")
        if ev == "entry":
            msgs.append(
                f"🟢 ENTRY {r.get('direction')} @ {r.get('entry_price')}\n"
                f"reason: {r.get('reason')}\n"
                f"SL {r.get('stop_loss')} TP {r.get('take_profit')}"
            )
        elif ev == "exit":
            msgs.append(
                f"🏁 EXIT {r.get('direction')} @ {r.get('exit_price')}\n"
                f"R={r.get('r_multiple')} reason={r.get('reason')}"
            )
        elif ev == "stop_adjust":
            msgs.append(
                f"🔧 STOP {r.get('why')}: {r.get('old_stop')} → {r.get('new_stop')} "
                f"(MFE_R={r.get('mfe_r')})"
            )
        elif ev == "ltf_impulse" and r.get("near_structure"):
            msgs.append(
                f"⚡ 15m impulse near 4h wedge ({r.get('direction')})\n"
                f"range={r.get('range_pct')} vol_x={r.get('volume_ratio')}\n"
                f"(память, не вход)"
            )
        elif ev == "structure_state" and r.get("snapshot", {}).get("would_signal"):
            snap = r.get("snapshot") or {}
            msgs.append(
                f"📌 WOULD SIGNAL {snap.get('direction')} {snap.get('pattern')}\n"
                f"(paper step решает исполнение)"
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
                        f"⏸ reject [{stage}] {reason}\n"
                        f"wedge={snap.get('has_wedge')} "
                        f"width={snap.get('width_pct')}"
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
    last = rows[-1] if rows else {}
    rejects = [r for r in rows if r.get("event") == "reject"]
    last_rej = rejects[-1] if rejects else {}
    return (
        "🤖 Zeus paper-clock OK (research)\n"
        f"last_event={last.get('event', '—')}\n"
        f"last_reject={last_rej.get('reason', '—')} "
        f"[{last_rej.get('stage', '—')}]\n"
        "live не включён · risk% не трогаем"
    )


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

    if important:
        body = "📊 Zeus paper\n\n" + "\n\n".join(important)
        if await _send(body):
            sent_any = True
            # max ts among new rows
            max_ts = since
            for r in rows:
                ts = int(r.get("ts") or 0)
                if ts > max_ts:
                    max_ts = ts
            state["last_ts"] = max_ts or now

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
