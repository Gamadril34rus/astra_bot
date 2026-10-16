"""Persist live entry plans: models/zeus_live_positions.json.

Key = symbol|side (A-013). Legacy flat symbol keys migrated on load.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("models/zeus_live_positions.json")


def plan_key(symbol: str, side: str) -> str:
    return f"{symbol}|{str(side).lower()}"


def _normalize(raw_pos: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Migrate legacy symbol-only keys → symbol|side."""
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw_pos.items():
        if not isinstance(v, dict):
            continue
        if "|" in k:
            out[k] = v
            continue
        side = str(v.get("side") or "long").lower()
        sym = str(v.get("symbol") or k)
        out[plan_key(sym, side)] = v
    return out


def load_positions(path: Path = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict) and "positions" in raw:
        pos = raw["positions"]
        return _normalize(pos) if isinstance(pos, dict) else {}
    if isinstance(raw, dict):
        return _normalize({k: v for k, v in raw.items() if isinstance(v, dict)})
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
    pending_verify: bool = False,
    signal_id: str | None = None,
    tp1: float | str | None = None,
    be_moved: bool = False,
    path: Path = DEFAULT_PATH,
) -> None:
    pos = load_positions(path)
    key = plan_key(symbol, side)
    # drop legacy symbol-only key if present
    pos.pop(symbol, None)
    row = {
        "symbol": symbol,
        "side": side.lower(),
        "qty": str(qty),
        "stop": str(stop),
        "tp": str(tp) if tp is not None else None,
        "entry": str(entry) if entry is not None else None,
        "leverage": int(leverage),
        "pending_verify": bool(pending_verify),
        "signal_id": signal_id,
        "ts": time.time(),
        "be_moved": bool(be_moved),
    }
    # tp1 level (1.5R partial); omit on legacy-style plans
    if tp1 is not None:
        row["tp1"] = str(tp1)
    elif tp is not None:
        # new opens: take_profit_price is TP1 @ 1.5R
        row["tp1"] = str(tp)
    pos[key] = row
    save_positions(pos, path)


def remove_plan(symbol: str, path: Path = DEFAULT_PATH, side: str | None = None) -> None:
    pos = load_positions(path)
    if side:
        key = plan_key(symbol, side)
        if key in pos:
            del pos[key]
            save_positions(pos, path)
            return
    # remove all keys for symbol
    keys = [k for k, v in pos.items() if k == symbol or k.startswith(symbol + "|") or v.get("symbol") == symbol]
    for k in keys:
        del pos[k]
    if keys:
        save_positions(pos, path)


def get_plan(symbol: str, path: Path = DEFAULT_PATH, side: str | None = None) -> dict[str, Any] | None:
    pos = load_positions(path)
    if side:
        return pos.get(plan_key(symbol, side))
    for k, v in pos.items():
        if k == symbol or k.startswith(symbol + "|") or v.get("symbol") == symbol:
            return v
    return None


def open_symbols(path: Path = DEFAULT_PATH) -> set[str]:
    return {str(v.get("symbol") or k.split("|")[0]) for k, v in load_positions(path).items()}


def count_open(path: Path = DEFAULT_PATH) -> int:
    return len(load_positions(path))
