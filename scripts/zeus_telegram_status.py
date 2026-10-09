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
        ct = _ts_ms(t)
        if not ct or not ts:
            continue
        dt = abs(ct - ts)
        if dt > window_ms:
            continue
        if best_dt is None or dt < best_dt:
            best_dt = dt
            best_id = str(t.get("id") or "") or None
    return best_id
