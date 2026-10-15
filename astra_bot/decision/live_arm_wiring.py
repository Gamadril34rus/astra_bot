from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from astra_bot.decision.live_arm import (
    MAX_OPEN,
    SignalCandidate,
    format_entry_tg,
    mark_cooldown,
    plan_from_candidate,
    same_side_cluster_blocks,
)
from astra_bot.decision.live_broker import LiveBroker, append_live_trade
from astra_bot.decision.live_positions import load_positions, remove_plan, upsert_plan
from astra_bot.decision.live_tg_flags import try_claim

logger = logging.getLogger(__name__)


async def resolve_step_size(
    broker: LiveBroker, symbol: str, default: Decimal = Decimal("0.001")
) -> Decimal:
    """B-015: step from exchange instrument; fallback default."""
    try:
        client = broker.client
        if hasattr(client, "get_instruments"):
            insts = await client.get_instruments()
            for it in insts or []:
                sym = str(
                    getattr(it, "symbol", None)
                    or (it.get("symbol") if isinstance(it, dict) else "")
                    or ""
                )
                if sym.replace("/", "-").upper() == symbol.replace("/", "-").upper():
                    step = getattr(it, "step_size", None) or getattr(it, "lot_size", None)
                    if step is None and isinstance(it, dict):
                        step = it.get("stepSize") or it.get("quantityPrecision")
                    if step is not None:
                        return Decimal(str(step))
    except Exception as exp:
        logger.warning("resolve_step_size %s: %s", symbol, type(exp).__name__)
    return default


def _archive_prior_plan_if_any(symbol: str, new_signal_id: str | None) -> None:
    plans = load_positions()
    old = plans.get(symbol)
    if not old:
        return
    old_sid = str(old.get("signal_id") or "")
    if new_signal_id and old_sid and old_sid == new_signal_id:
        return
    logger.warning(
        "arm: overwriting plan %s old_sid=%s new_sid=%s — archive interrupted exit",
        symbol,
        old_sid,
        new_signal_id,
    )
    try:
        append_live_trade(
            {
                "event": "exit",
                "symbol": symbol,
                "side": old.get("side"),
                "entry": old.get("entry"),
                "exit": None,
                "exit_reason": "plan_overwritten",
                "pnl": None,
                "signal_id": old_sid or None,
                "ts": time.time(),
            }
        )
    except Exception:
        pass
    try:
        remove_plan(symbol)
    except Exception:
        pass


DEFAULT_STRATEGY = "zeus_channel_boundary"
ENTRIES_PATH = Path("models/zeus_live_entries.jsonl")
JOURNAL_PATH = Path("models/zeus_live_journal.jsonl")
PENDING_PATH = Path("models/zeus_arm_pending.jsonl")
FRESH_MAX_SEC = 45 * 60


def _seen_signal_ids(path: Path = ENTRIES_PATH) -> set[str]:
    seen: set[str] = set()
    if not path.exists():
        return seen
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            sid = row.get("signal_id")
            if sid:
                seen.add(str(sid))
    except Exception:
        pass
    return seen


def append_entry_record(row: dict[str, Any], path: Path = ENTRIES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def append_journal(event: dict[str, Any], path: Path | None = None) -> None:
    path = path or JOURNAL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {**event, "ts": event.get("ts") or time.time()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _row_age_sec(row: dict[str, Any], now: float | None = None) -> float | None:
    now = now if now is not None else time.time()
    for key in ("ts", "signal_ts", "retest_ts", "entry_ts"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v > 1e10:
            v = v / 1000.0
        return max(0.0, now - v)
    return None


def is_fresh(
    row: dict[str, Any], max_sec: float = FRESH_MAX_SEC, now: float | None = None
) -> bool:
    age = _row_age_sec(row, now=now)
    if age is None:
        return True
    return age <= max_sec


def candidate_from_signal_dict(
    row: dict[str, Any],
    *,
    strategy: str = DEFAULT_STRATEGY,
    step_size: Decimal = Decimal("0.001"),
    leverage: int = 5,
) -> SignalCandidate | None:
    side = (row.get("side") or row.get("direction") or "").lower()
    if side not in ("long", "short"):
        return None
    entry, stop = row.get("entry"), row.get("stop")
    if entry is None or stop is None:
        return None
    try:
        e, s = Decimal(str(entry)), Decimal(str(stop))
    except Exception:
        return None
    if e <= 0 or s <= 0 or e == s:
        return None
    tp = row.get("tp") or row.get("target") or row.get("tp1")
    tp_d = Decimal(str(tp)) if tp is not None else None
    try:
        conf_f = float(row["confidence"]) if row.get("confidence") is not None else None
    except (TypeError, ValueError):
        conf_f = None
    sym = str(row.get("symbol") or "").strip()
    if not sym:
        return None
    return SignalCandidate(
        symbol=sym,
        side=side,
        entry=e,
        stop=s,
        take_profit=tp_d,
        confidence=conf_f,
        leverage=int(row.get("leverage") or leverage),
        strategy=str(row.get("strategy") or strategy),
        step_size=step_size,
    )


async def exchange_open_symbols(broker: LiveBroker) -> set[str]:
    positions = await broker.client.get_positions()
    out: set[str] = set()
    for p in positions:
        qty = getattr(p, "quantity", None) or Decimal("0")
        try:
            q = Decimal(str(qty))
        except Exception:
            continue
        if q > 0:
            sym = getattr(p, "symbol", None)
            if sym:
                out.add(str(sym))
    return out


def _normalize_pos_side(p: Any) -> str:
    side = str(getattr(p, "side", "") or "").lower()
    if side in ("long", "short"):
        return side
    try:
        q = Decimal(str(getattr(p, "quantity", None) or "0"))
    except Exception:
        return ""
    if q > 0:
        return "long"
    if q < 0:
        return "short"
    return ""


async def exchange_side_stats(broker: LiveBroker) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {
        "long": {"count": 0, "upnl": 0.0},
        "short": {"count": 0, "upnl": 0.0},
    }
    positions = await broker.client.get_positions()
    for p in positions:
        qty = getattr(p, "quantity", None) or Decimal("0")
        try:
            q = Decimal(str(qty))
        except Exception:
            continue
        if q == 0:
            continue
        side = _normalize_pos_side(p)
        if side not in stats:
            continue
        stats[side]["count"] += 1
        try:
            upnl = float(getattr(p, "unrealized_pnl", None) or 0)
        except (TypeError, ValueError):
            upnl = 0.0
        stats[side]["upnl"] += upnl
    return stats


async def exchange_side_counts(broker: LiveBroker) -> dict[str, int]:
    st = await exchange_side_stats(broker)
    return {k: int(v["count"]) for k, v in st.items()}


def load_pending_rows(path: Path = PENDING_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows
