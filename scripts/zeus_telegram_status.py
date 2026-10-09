#!/usr/bin/env python3
"""Статус paper-Зевса в Telegram (от имени ASTRA-бота).

Минимальный канал:
  — мгновенно: закрытие сделки + инциденты;
  — 2 сводки/сутки (утро/вечер, МСК);
  — аудит уверенности пишет models/zeus_tier_audit.json при каждом тике.

Live не трогает. Только research/paper journal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

MSK = timezone(timedelta(hours=3))
STATE_PATH = Path("models/zeus_tg_notify_state.json")
JOURNAL_DEFAULT = Path("models/zeus_trade_journal.jsonl")
TRADES_PATH = Path("models/zeus_paper_trades.jsonl")
POSITIONS_PATH = Path("models/zeus_paper_positions.json")
AUDIT_PATH = Path("models/zeus_tier_audit.json")

DIR_RU = {"long": "ЛОНГ", "short": "ШОРТ"}
EXIT_REASON_RU = {
    "stop_loss": "стоп",
    "take_profit": "тейк",
    "tp1": "тейк1",
    "tp2": "тейк2",
    "mae_cut": "mae_cut",
    "regime_exit": "regime_exit",
    "VOL_EXPANSION": "VOL_EXPANSION",
    "time_stop": "лимит времени",
    "max_hold": "макс. удержание",
    "panic": "аварийное",
    "closed": "закрыто",
}

# Journal events that are pushable as incidents (⚠️ …).
INCIDENT_EVENTS = {
    "halt",
    "kill_switch",
    "kill-switch",
    "heat_on",
    "heat_off",
    "heat_active",
    "heat_clear",
    "save_state_error",
    "exception",
    "unhandled_exception",
    "error",
}


def _ru_dir(code: object) -> str:
    return DIR_RU.get(str(code or "").lower(), str(code or "—") or "—")


def _ru_reason(code: object) -> str:
    s = str(code or "—")
    return EXIT_REASON_RU.get(s, s)


def _money(v: float | None) -> str:
    """Format signed money with comma decimal (RU style)."""
    if v is None:
        return "+?"
    sign = "+" if v >= 0 else "−"
    return f"{sign}{abs(v):.2f}$".replace(".", ",")


def _sym_short(symbol: object) -> str:
    s = str(symbol or "—")
    return s.replace("-USDT", "").replace("USDT", "")


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _read_jsonl(path: Path, max_lines: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if max_lines is not None:
            lines = lines[-max_lines:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except Exception:
        return []
    return rows


def _ts_ms(row: dict[str, Any]) -> int:
    for k in ("ts", "closed_at", "opened_at"):
        v = row.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return 0


def sum_pnl_for_trade_id(trades: list[dict[str, Any]], trade_id: str) -> float | None:
    """Sum pnl of all parts of one trade (same id). None if no rows."""
    parts = [r for r in trades if str(r.get("id") or "") == str(trade_id)]
    if not parts:
        return None
    total = 0.0
    for p in parts:
        try:
            total += float(p.get("pnl") or 0)
        except (TypeError, ValueError):
            continue
    return total


def match_trade_id_for_exit(
    exit_row: dict[str, Any], trades: list[dict[str, Any]], window_ms: int = 6 * 3600 * 1000
) -> str | None:
    """Find paper-trade id for a journal exit (symbol+direction, closed_at near ts)."""
    sym = str(exit_row.get("symbol") or "").upper()
    direction = str(exit_row.get("direction") or "").lower()
    ts = _ts_ms(exit_row)
    # explicit id fields if present
    for key in ("id", "trade_id", "position_id"):
        if exit_row.get(key):
            return str(exit_row[key])
    feats = exit_row.get("features") or {}
    if isinstance(feats, dict):
        for key in ("id", "trade_id", "position_id"):
            if feats.get(key):
                return str(feats[key])
    best_id: str | None = None
    best_dt = None
    for t in trades:
        if str(t.get("symbol") or "").upper() != sym:
            continue
        if str(t.get("direction") or "").lower() != direction:
            continue
        ct = _ts_ms(t)  # prefers closed_at
        if not ct or not ts:
            continue
        dt = abs(ct - ts)
        if dt > window_ms:
            continue
        if best_dt is None or dt < best_dt:
            best_dt = dt
            best_id = str(t.get("id") or "") or None
    return best_id


def exit_reason_for_trade(trades: list[dict[str, Any]], trade_id: str) -> str:
    """Pick the 'final' exit reason among parts (prefer tp2/stop over tp1)."""
    parts = [r for r in trades if str(r.get("id") or "") == str(trade_id)]
    if not parts:
        return "—"
    reasons = [str(p.get("exit_reason") or "") for p in parts]
    for preferred in ("tp2", "take_profit", "stop_loss", "mae_cut", "regime_exit", "tp1"):
        if preferred in reasons:
            return preferred
    # latest by closed_at
    parts_sorted = sorted(parts, key=_ts_ms)
    return str(parts_sorted[-1].get("exit_reason") or "—")


def format_exit_line(
    symbol: object,
    direction: object,
    pnl: float | None,
    reason: object,
) -> str:
    """🏁 TIA ЛОНГ: закрыта +2,61$ (тейк2)"""
    if pnl is None:
        money_s = "+? (сверка утром)"
        return (
            f"🏁 {_sym_short(symbol)} {_ru_dir(direction)}: закрыта "
            f"{money_s}"
        )
    return (
        f"🏁 {_sym_short(symbol)} {_ru_dir(direction)}: закрыта "
        f"{_money(pnl)} ({_ru_reason(reason)})"
    )


def is_incident(row: dict[str, Any]) -> bool:
    ev = str(row.get("event") or "").lower()
    if ev in INCIDENT_EVENTS:
        return True
    note = str(row.get("note") or row.get("reason") or "").lower()
    if any(k in note for k in ("halt", "kill-switch", "kill_switch", "save-state", "unhandled")):
        return True
    if "heat" in ev and any(x in ev for x in ("on", "off", "active", "clear", "cap")):
        return True
    return False


def format_incident_line(row: dict[str, Any]) -> str:
    ev = str(row.get("event") or "incident")
    note = str(row.get("note") or row.get("reason") or row.get("symbol") or "").strip()
    if note:
        return f"⚠️ {ev}: {note}"
    return f"⚠️ {ev}"


def account_equity(trades: list[dict[str, Any]] | None = None) -> tuple[float, float]:
    """Return (equity, realized_pnl). Prefer positions file."""
    initial = 2000.0
    realized = 0.0
    if POSITIONS_PATH.exists():
        try:
            pos = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
            if isinstance(pos, dict):
                if pos.get("initial_capital") is not None:
                    initial = float(pos["initial_capital"])
                if pos.get("realized_pnl") is not None:
                    realized = float(pos["realized_pnl"])
                    return initial + realized, realized
        except Exception:
            pass
    if trades is None:
        trades = _read_jsonl(TRADES_PATH)
    realized = sum(float(r.get("pnl") or 0) for r in trades)
    return initial + realized, realized


def trades_in_window(
    trades: list[dict[str, Any]], start_ms: int, end_ms: int
) -> list[dict[str, Any]]:
    """Closed trades whose closed_at falls in [start_ms, end_ms). One row per part."""
    out: list[dict[str, Any]] = []
    for t in trades:
        ct = 0
        try:
            ct = int(t.get("closed_at") or 0)
        except (TypeError, ValueError):
            continue
        if start_ms <= ct < end_ms:
            out.append(t)
    return out


def group_trades_by_id(parts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in parts:
        tid = str(p.get("id") or "")
        if not tid:
            continue
        groups.setdefault(tid, []).append(p)
    return groups


def window_bounds_msk(now: datetime, kind: str) -> tuple[int, int, str]:
    """Return (start_ms, end_ms, label) for morning/evening report in MSK."""
    now_msk = now.astimezone(MSK)
    d = now_msk.date()
    if kind == "morning":
        # 21:00 previous calendar day MSK → 09:00 today MSK
        end = datetime(d.year, d.month, d.day, 9, 0, tzinfo=MSK)
        start = end - timedelta(hours=12)
        label = "За ночь"
    else:
        # 09:00 → 21:00 same calendar day MSK
        start = datetime(d.year, d.month, d.day, 9, 0, tzinfo=MSK)
        end = datetime(d.year, d.month, d.day, 21, 0, tzinfo=MSK)
        label = "За день"
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000), label


def due_report_kind(now: datetime, state: dict[str, Any]) -> str | None:
    """Return 'morning' / 'evening' if a summary is due, else None."""
    now_msk = now.astimezone(MSK)
    hour = now_msk.hour
    today = now_msk.date().isoformat()
    last = state.get("last_daily_report") or {}
    last_date = str(last.get("date") or "")
    last_window = str(last.get("window") or "")

    if hour >= 9 and hour < 21:
        # morning report window (after 09:00 MSK / 06:00 UTC)
        if not (last_date == today and last_window == "morning"):
            return "morning"
    if hour >= 21 or hour < 9:
        # evening report: after 21:00 MSK; before 09:00 still evening of previous day
        if hour >= 21:
            key_date = today
        else:
            key_date = (now_msk.date() - timedelta(days=1)).isoformat()
        if not (last_date == key_date and last_window == "evening"):
            return "evening"
    return None


def mark_report_sent(state: dict[str, Any], now: datetime, kind: str) -> None:
    now_msk = now.astimezone(MSK)
    if kind == "morning":
        state["last_daily_report"] = {"date": now_msk.date().isoformat(), "window": "morning"}
    else:
        if now_msk.hour >= 21:
            d = now_msk.date().isoformat()
        else:
            d = (now_msk.date() - timedelta(days=1)).isoformat()
        state["last_daily_report"] = {"date": d, "window": "evening"}


def format_daily_report(
    *,
    label: str,
    window_parts: list[dict[str, Any]],
    equity: float,
    window_pnl: float,
    audit_line: str,
    incidents: list[str],
) -> str:
    groups = group_trades_by_id(window_parts)
    n = len(groups)
    # best / worst by summed pnl per id
    best_sym = best_pnl = worst_sym = worst_pnl = None
    for tid, parts in groups.items():
        pnl = sum(float(p.get("pnl") or 0) for p in parts)
        sym = _sym_short(parts[0].get("symbol"))
        if best_pnl is None or pnl > best_pnl:
            best_pnl, best_sym = pnl, sym
        if worst_pnl is None or pnl < worst_pnl:
            worst_pnl, worst_sym = pnl, sym

    line1 = f"{label}: {n} сделок, итог {_money(window_pnl)}"
    if n and best_sym is not None and worst_sym is not None:
        line1 += f" (лучший {best_sym} {_money(best_pnl)}, худший {worst_sym} {_money(worst_pnl)})"
    line1 += "."

    line2 = f"Счёт {_money(equity).lstrip('+').rstrip('$')}$ ({_money(window_pnl)})."
    # _money always has sign; equity positive display without forcing +
    eq_s = f"{equity:.2f}$".replace(".", ",")
    line2 = f"Счёт {eq_s} ({_money(window_pnl)})."

    line3 = f"Санитар: {audit_line}." if not audit_line.startswith("санитар") else f"{audit_line}."
    if incidents:
        line4 = "Поломки: " + "; ".join(incidents)
    else:
        line4 = "Поломок нет."
    return "\n".join([line1, line2, line3, line4])


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
            chunk = text if len(text) < 4000 else text[:4000] + "…"
            await bot.send_message(chat_id=int(raw_id), text=chunk)
            ok = True
        except Exception as exc:
            print(f"send failed to {raw_id}: {exc}")
    return ok


def collect_exit_pushes(
    journal: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    since_ts: int,
) -> list[str]:
    msgs: list[str] = []
    for r in journal:
        if str(r.get("event") or "") != "exit":
            continue
        ts = _ts_ms(r)
        if since_ts and ts and ts <= since_ts:
            continue
        tid = match_trade_id_for_exit(r, trades)
        pnl = sum_pnl_for_trade_id(trades, tid) if tid else None
        reason = exit_reason_for_trade(trades, tid) if tid else (r.get("reason") or "—")
        msgs.append(format_exit_line(r.get("symbol"), r.get("direction"), pnl, reason))
    return msgs


def collect_incident_pushes(
    journal: list[dict[str, Any]], since_ts: int
) -> list[str]:
    msgs: list[str] = []
    for r in journal:
        ts = _ts_ms(r)
        if since_ts and ts and ts <= since_ts:
            continue
        if not is_incident(r):
            continue
        msgs.append(format_incident_line(r))
    return msgs


def run_tier_audit() -> tuple[dict[str, Any] | None, str]:
    """Run weekly tier audit; return (audit_dict, status_line)."""
    try:
        import sys as _sys

        if str(PROJECT_ROOT) not in _sys.path:
            _sys.path.insert(0, str(PROJECT_ROOT))
        from astra_bot.decision.zeus_tier_audit import run_audit, status_line

        audit = run_audit(trades_path=TRADES_PATH, audit_path=AUDIT_PATH)
        return audit, status_line(audit)
    except Exception as exc:
        print(f"tier audit skip: {exc}")
        return None, "нет данных"


async def amain(journal: Path, force_report: bool = False) -> int:
    state = _load_state()
    rows = _read_jsonl(journal, max_lines=5000)
    trades = _read_jsonl(TRADES_PATH)
    since = int(state.get("last_ts") or 0)
    now_ms = int(time.time() * 1000)
    now = datetime.now(tz=UTC)

    audit, audit_line = run_tier_audit()
    state["tier_status_line"] = audit_line

    sent_any = False

    # Instant pushes: exits + incidents only
    exits = collect_exit_pushes(rows, trades, since)
    incidents = collect_incident_pushes(rows, since)
    for msg in exits + incidents:
        if await _send(msg):
            sent_any = True

    if exits or incidents:
        max_ts = since
        for r in rows:
            ts = _ts_ms(r)
            if ts > max_ts:
                max_ts = ts
        state["last_ts"] = max_ts or now_ms

    # Daily summaries (MSK windows)
    kind = due_report_kind(now, state)
    if force_report and kind is None:
        # pick the window matching current MSK hour
        hour = now.astimezone(MSK).hour
        kind = "morning" if 9 <= hour < 21 else "evening"
    if kind:
        start_ms, end_ms, label = window_bounds_msk(now, kind)
        window_parts = trades_in_window(trades, start_ms, end_ms)
        window_pnl = sum(float(p.get("pnl") or 0) for p in window_parts)
        equity, _ = account_equity(trades)
        # incidents inside the window (for "Поломок нет")
        win_inc = [
            format_incident_line(r)
            for r in rows
            if is_incident(r) and start_ms <= _ts_ms(r) < end_ms
        ]
        body = format_daily_report(
            label=label,
            window_parts=window_parts,
            equity=equity,
            window_pnl=window_pnl,
            audit_line=audit_line,
            incidents=win_inc,
        )
        if await _send(body):
            sent_any = True
            mark_report_sent(state, now, kind)

    if sent_any or audit is not None:
        _save_state(state)
        print("telegram status sent" if sent_any else "audit only / quiet")
    else:
        print("nothing to notify (throttled or quiet)")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--journal", default=str(JOURNAL_DEFAULT))
    p.add_argument(
        "--force-report",
        action="store_true",
        help="Отправить сводный отчёт за текущее окно даже если уже уходил",
    )
    args = p.parse_args()
    raise SystemExit(asyncio.run(amain(Path(args.journal), args.force_report)))


if __name__ == "__main__":
    main()
