"""Persist live entry plans: models/zeus_live_positions.json.

Written on each successful open_with_protection; used by restore_missing_stops.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("models/zeus_live_positions.json")


def load_positions(path: Path = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict) and "positions" in raw:
        pos = raw["positions"]
        return pos if isinstance(pos, dict) else {}
    if isinstance(raw, dict):
        # flat symbol -> plan
        return {k: v for k, v in raw.items() if isinstance(v, dict)}
    return {}


def save_positions(positions: dict[str, dict[str, Any]], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"positions": positions, "ts": time.time()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def upsert_plan(
    symbol: str,
    *,
    side: str,
    qty: float | str,
    stop: float | str,
    tp: float | str | None = None,
    entry: float | str | None = None,
    leverage: int = 5,
    path: Path = DEFAULT_PATH,
) -> None:
    pos = load_positions(path)
    pos[symbol] = {
        "symbol": symbol,
        "side": side.lower(),
        "qty": str(qty),
        "stop": str(stop),
        "tp": str(tp) if tp is not None else None,
        "entry": str(entry) if entry is not None else None,
        "leverage": int(leverage),
        "ts": time.time(),
    }
    save_positions(pos, path)


def remove_plan(symbol: str, path: Path = DEFAULT_PATH) -> None:
    pos = load_positions(path)
    if symbol in pos:
        del pos[symbol]
        save_positions(pos, path)


def get_plan(symbol: str, path: Path = DEFAULT_PATH) -> dict[str, Any] | None:
    return load_positions(path).get(symbol)


def open_symbols(path: Path = DEFAULT_PATH) -> set[str]:
    return set(load_positions(path).keys())


def count_open(path: Path = DEFAULT_PATH) -> int:
    return len(load_positions(path))
