"""Anti-chatter re-entry guard after VOL_EXPANSION exits (owner 15.10).

Data-only: cooldown 30 min on symbol|side. Shared by paper + live.
Thresholds of ExitManager are NOT touched.

Idempotency (P0 arena 15.10): until = closed_ts + COOLDOWN (not wall-clock
now). Repeated set for the same trade_id does not extend until — critical
because live sync_closed_plans re-sees the same exit row every tick.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VOL_EXPANSION_COOLDOWN_SEC = 1800  # 30 minutes
DEFAULT_PATH = Path("models/zeus_vol_cooldowns.json")
EVENTS_PATH = Path("models/zeus_vol_cooldown_events.jsonl")


def _key(symbol: str, side: str) -> str:
    return f"{str(symbol).strip().upper()}|{str(side).strip().lower()}"


def _now() -> float:
    return time.time()


def _load(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("reentry_guard load %s: %s", path, type(exc).__name__)
        return {}


def _purge(data: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Drop expired entries. Do NOT rewrite remaining until timestamps."""
    now = now if now is not None else _now()
    out: dict[str, Any] = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        try:
            until = float(v.get("until") or 0)
        except (TypeError, ValueError):
            continue
        if until > now:
            out[k] = v
    return out


def _save(
    data: dict[str, Any],
    path: Path = DEFAULT_PATH,
    *,
    now: float | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    purged = _purge(data, now=now)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".vol_cd_", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(purged, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_event(event: dict[str, Any], path: Path | None = None) -> None:
    path = Path(path if path is not None else EVENTS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {**event, "ts": event.get("ts") or _now()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def is_vol_expansion_reason(exit_reason: str | None) -> bool:
    r = str(exit_reason or "").strip()
    return r.startswith("VOL_EXPANSION")


def set_cooldown(
    symbol: str,
    side: str,
    *,
    trade_id: str | None = None,
    exit_reason: str = "VOL_EXPANSION",
    closed_ts: float | None = None,
    path: Path | None = None,
    cooldown_sec: float = VOL_EXPANSION_COOLDOWN_SEC,
    now: float | None = None,
) -> float:
    """Arm symbol|side cooldown until closed_ts+cooldown_sec.

    Idempotent: if an active entry already exists for the same trade_id,
    return its until without rewriting (prevents eternal extension when
    sync_closed_plans re-processes the same exit every tick).
    Base clock is closed_ts of the trade, not wall-clock now.
    """
    path = path if path is not None else DEFAULT_PATH
    now = now if now is not None else _now()
    closed = float(closed_ts if closed_ts is not None else now)
    until = closed + float(cooldown_sec)
    tid = str(trade_id).strip() if trade_id else None
    key = _key(symbol, side)
    data = _load(path)
    existing = data.get(key)
    if isinstance(existing, dict):
        try:
            ex_until = float(existing.get("until") or 0)
        except (TypeError, ValueError):
            ex_until = 0.0
        ex_tid = existing.get("trade_id")
        if tid and ex_tid is not None and str(ex_tid) == tid and ex_until > now:
            return ex_until
        if ex_until > until and ex_until > now:
            return ex_until
    data[key] = {
        "until": until,
        "exit_reason": str(exit_reason),
        "closed_ts": closed,
        "trade_id": tid,
    }
    _save(data, path, now=now)
    append_event(
        {
            "event": "vol_cooldown_set",
            "symbol": str(symbol).strip().upper(),
            "side": str(side).strip().lower(),
            "until": until,
            "trade_id": tid,
            "exit_reason": str(exit_reason),
            "closed_ts": closed,
        }
    )
    logger.info(
        "vol_cooldown set %s|%s until=%.0f trade_id=%s",
        symbol,
        side,
        until,
        tid,
    )
    return until


def check(
    symbol: str,
    side: str,
    *,
    path: Path | None = None,
    now: float | None = None,
) -> tuple[bool, float | None]:
    """Return (blocked, until). Expired entries treated as free."""
    path = path if path is not None else DEFAULT_PATH
    now = now if now is not None else _now()
    data = _load(path)
    row = data.get(_key(symbol, side))
    if not row or not isinstance(row, dict):
        return False, None
    try:
        until = float(row.get("until") or 0)
    except (TypeError, ValueError):
        return False, None
    if until <= now:
        return False, None
    return True, until


def note_skip(
    symbol: str,
    side: str,
    until: float | None,
) -> None:
    append_event(
        {
            "event": "entry_skipped_vol_cooldown",
            "symbol": str(symbol).strip().upper(),
            "side": str(side).strip().lower(),
            "until": until,
        }
    )
