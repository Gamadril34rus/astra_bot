"""Dedup TG rockets: one signal_id|event → one send (persistent)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("models/zeus_live_tg_flags.json")
# prune flags older than 14d
_MAX_AGE_SEC = 14 * 24 * 3600


def _load(path: Path = DEFAULT_PATH) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        flags = raw.get("flags") if isinstance(raw, dict) else raw
        if not isinstance(flags, dict):
            return {}
        return {str(k): float(v) for k, v in flags.items()}
    except Exception as exc:
        logger.warning("tg_flags load: %s", type(exc).__name__)
        return {}


def _save(flags: dict[str, float], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    pruned = {k: v for k, v in flags.items() if now - float(v) < _MAX_AGE_SEC}
    path.write_text(
        json.dumps({"flags": pruned, "ts": now}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def flag_key(symbol: str, event: str, signal_id: str | None = None) -> str:
    sid = (signal_id or "").strip() or "_"
    return f"{symbol}|{event}|{sid}"


def already_sent(symbol: str, event: str, signal_id: str | None = None, path: Path = DEFAULT_PATH) -> bool:
    return flag_key(symbol, event, signal_id) in _load(path)


def mark_sent(symbol: str, event: str, signal_id: str | None = None, path: Path = DEFAULT_PATH) -> None:
    flags = _load(path)
    flags[flag_key(symbol, event, signal_id)] = time.time()
    _save(flags, path)


def try_claim(symbol: str, event: str, signal_id: str | None = None, path: Path = DEFAULT_PATH) -> bool:
    """Return True if this is the first claim (caller should send TG)."""
    if already_sent(symbol, event, signal_id, path):
        return False
    mark_sent(symbol, event, signal_id, path)
    return True
