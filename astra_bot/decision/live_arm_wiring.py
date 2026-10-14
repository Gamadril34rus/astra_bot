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

async def resolve_step_size(broker: LiveBroker, symbol: str, default: Decimal = Decimal("0.001")) -> Decimal:
    """B-015: step from exchange instrument; fallback default."""
    try:
        client = broker.client
        if hasattr(client, "get_instruments"):
            insts = await client.get_instruments()
            for it in insts or []:
                sym = str(getattr(it, "symbol", None) or (it.get("symbol") if isinstance(it, dict) else "") or "")
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
    """If a stale plan for symbol still in file, write interrupted exit before overwrite."""
    plans = load_positions()
    old = plans.get(symbol)
    if not old:
        return
    old_sid = str(old.get("signal_id") or "")
    if new_signal_id and old_sid and old_sid == new_signal_id:
        return
    logger.warning(
        "arm: overwriting plan %s old_sid=%s new_sid=%s — archive interrupted exit",
        symbol, old_sid, new_signal_id,
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



DEFAULT_STRATEGY = "zeus_channel_boundary"  # fallback; row.strategy from feed wins
ENTRIES_PATH = Path("models/zeus_live_entries.jsonl")
JOURNAL_PATH = Path("models/zeus_live_journal.jsonl")
PENDING_PATH = Path("models/zeus_arm_pending.jsonl")
FRESH_MAX_SEC = 45 * 60  # 45 min — skip overnight stale retests


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


def append_journal(event: dict[str, Any], path: Path = JOURNAL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {**event, "ts": event.get("ts") or time.time()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _row_age_sec(row: dict[str, Any], now: float | None = None) -> float | None:
    """Age from retest_ts / signal_ts / ts (seconds). None if unknown."""
    now = now if now is not None else time.time()
    for key in ("retest_ts", "signal_ts", "ts", "entry_ts"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        # ms vs sec: epoch-ms timestamps are >=1e12, anything above ~1e10
        # cannot be a plausible epoch-seconds value → treat as milliseconds.
        if v > 1e10:
            v = v / 1000.0
        return max(0.0, now - v)
    return None


def is_fresh(row: dict[str, Any], max_sec: float = FRESH_MAX_SEC, now: float | None = None) -> bool:
    age = _row_age_sec(row, now=now)
    if age is None:
        return True  # unknown age: allow (feed must set ts for production)
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
    """Source of truth: exchange positions with qty > 0."""
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
    """Robust side: attribute, else qty sign. Empty → '' (not counted)."""
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
    """Per-side count + sum(unrealizedProfit) from exchange positions."""
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
    """Compat wrapper: counts only."""
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


async def run_arm_entries(
    broker: LiveBroker,
    rows: Iterable[dict[str, Any]],
    *,
    max_entries: int = 1,
    vst: bool | None = None,
    send_tg: Any = None,
    fresh_max_sec: float = FRESH_MAX_SEC,
    entries_path: Path = ENTRIES_PATH,
) -> list[dict[str, Any]]:
    """At most max_entries opens. Exchange guards + signal_id dedup + freshness."""
    if vst is None:
        vst = (os.environ.get("ZEUS_MAINNET") or "").strip() != "1"

    row_list = list(rows)
    if not row_list:
        logger.info("arm: empty feed — 0 entries (Stage A idle)")
        return []

    seen = _seen_signal_ids(entries_path)
    open_syms = await exchange_open_symbols(broker)
    open_n = len(open_syms)
    side_stats = await exchange_side_stats(broker)
    results: list[dict[str, Any]] = []

    for row in row_list:
        if len(results) >= max_entries:
            break
        sid = str(row.get("signal_id") or "").strip()
        if sid and sid in seen:
            logger.info("arm: skip signal_id=%s — already in entries journal", sid)
            continue
        if not is_fresh(row, max_sec=fresh_max_sec):
            logger.info(
                "arm: skip %s — stale retest age>%ss",
                row.get("symbol"),
                int(fresh_max_sec),
            )
            continue
        cand = candidate_from_signal_dict(row)
        if cand is None:
            continue
        if cand.symbol in open_syms:
            logger.info("arm: skip %s — already open on exchange", cand.symbol)
            continue
        if open_n >= MAX_OPEN:
            logger.info("arm: skip — exchange max_open=%d", MAX_OPEN)
            break
        side_l = str(cand.side or "").lower()
        st = side_stats.get(side_l) or {"count": 0, "upnl": 0.0}
        if same_side_cluster_blocks(
            side_l, side_count=int(st["count"]), side_upnl=st["upnl"]
        ):
            logger.info(
                "arm: skip %s — same_side cluster %s n=%d upnl=%.4f < 0 "
                "(4th/5th blocked while underwater)",
                cand.symbol,
                side_l,
                int(st["count"]),
                float(st["upnl"]),
            )
            continue

        # B-015: step from instrument when missing or still default 0.001
        if cand.step_size is None or cand.step_size == Decimal("0.001"):
            cand.step_size = await resolve_step_size(broker, cand.symbol)

        plan = None
        try:
            plan = await plan_from_candidate(
                broker,
                cand,
                exchange_symbols=open_syms,
                exchange_open_count=open_n,
            )
            if plan is None:
                continue
            # Sanity: stop within 25% of entry (ghost-price guard)
            try:
                dist = abs(float(cand.entry) - float(cand.stop)) / max(abs(float(cand.entry)), 1e-12)
            except Exception:
                dist = 0.0
            if dist > 0.25:
                logger.error(
                    "arm: REJECT %s ghost stop entry=%s stop=%s dist=%.1f%%",
                    cand.symbol, cand.entry, cand.stop, dist * 100,
                )
                append_journal(
                    {
                        "event": "entry_rejected_ghost_price",
                        "symbol": cand.symbol,
                        "entry": str(cand.entry),
                        "stop": str(cand.stop),
                        "dist_pct": round(dist * 100, 3),
                    }
                )
                continue
            res = await broker.open_with_protection(plan)
            fill_px = (res or {}).get("fill_price")
            slip = (res or {}).get("slip_pct")
            # Journal IMMEDIATELY after successful open (entry+protect returned)
            _archive_prior_plan_if_any(plan.symbol, sid or None)
            upsert_plan(
                plan.symbol,
                side=plan.side,
                qty=plan.quantity,
                stop=plan.stop_price,
                tp=plan.take_profit_price,
                entry=fill_px or cand.entry,
                leverage=plan.leverage,
                signal_id=sid or None,
            )
            mark_cooldown(cand.strategy, plan.symbol, plan.side)
            try:
                append_live_trade(
                    {
                        "event": "entry",
                        "signal_id": sid or None,
                        "symbol": cand.symbol,
                        "side": cand.side,
                        "plan_entry": str(cand.entry),
                        "fill_price": fill_px,
                        "slip_pct": slip,
                        "qty": str(plan.quantity),
                        "stop": str(plan.stop_price),
                        "tp": str(plan.take_profit_price) if plan.take_profit_price else None,
                        "leverage": plan.leverage,
                        "ts": time.time(),
                    }
                )
            except Exception:
                pass
            if slip is not None:
                logger.info("arm entry slip %s %.4f%%", cand.symbol, slip)
        except Exception as exc:
            logger.exception(
                "arm entry %s failed AFTER possible fill — job must go red: %s",
                cand.symbol,
                type(exc).__name__,
            )
            # One attempt: cooldown so same strategy|symbol|side not retried this process
            try:
                mark_cooldown(cand.strategy, cand.symbol, cand.side)
            except Exception:
                pass
            # Durable: entries.jsonl is in Save-list (blocks same signal_id next tick)
            append_entry_record(
                {
                    "signal_id": sid or f"failed|{cand.symbol}|{int(time.time())}",
                    "symbol": cand.symbol,
                    "side": cand.side,
                    "ts": time.time(),
                    "event": "entry_failed",
                    "error": type(exc).__name__,
                    "error_msg": str(exc)[:300],
                },
                entries_path,
            )
            append_journal(
                {
                    "event": "entry_failed",
                    "symbol": cand.symbol,
                    "side": cand.side,
                    "signal_id": sid or None,
                    "error": type(exc).__name__,
                }
            )
            # Partial plan only if plan exists; pending_verify avoids false 🏁
            try:
                if plan is not None:
                    upsert_plan(
                        plan.symbol,
                        side=plan.side,
                        qty=plan.quantity,
                        stop=plan.stop_price,
                        tp=plan.take_profit_price,
                        entry=cand.entry,
                        leverage=plan.leverage,
                        pending_verify=True,
                    )
            except Exception:
                pass
            # Do NOT continue to next symbol (would stack naked positions)
            raise

        results.append(res)
        open_syms.add(cand.symbol)
        open_n += 1
        side_l = str(cand.side or "").lower()
        if side_l in side_stats:
            side_stats[side_l]["count"] = int(side_stats[side_l]["count"]) + 1
        if sid:
            seen.add(sid)
            append_entry_record(
                {
                    "signal_id": sid,
                    "symbol": cand.symbol,
                    "side": cand.side,
                    "ts": time.time(),
                },
                entries_path,
            )
        msg = format_entry_tg(
            cand.symbol,
            cand.side,
            plan.quantity,
            cand.entry,
            cand.stop,
            cand.take_profit,
            vst=vst,
        )
        append_journal(
            {
                "event": "entry",
                "signal_id": sid or None,
                "symbol": cand.symbol,
                "side": cand.side,
                "qty": str(plan.quantity),
                "entry": str(cand.entry),
                "stop": str(cand.stop),
                "tp": str(cand.take_profit) if cand.take_profit else None,
            }
        )
        entry_ts = time.time()
        if send_tg is not None and try_claim(
            cand.symbol, "entry", sid or None, ts=None if sid else entry_ts
        ):
            try:
                await send_tg(msg)
            except Exception as exc:
                logger.warning("arm TG send failed: %s", type(exc).__name__)
        elif send_tg is not None:
            logger.info("arm TG skip dup entry %s %s", cand.symbol, sid)
        logger.info("arm entry done: %s", msg)
    return results
