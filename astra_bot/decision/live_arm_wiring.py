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

# ---- arm entry error guard (owner №25) ----
MARGIN_FAIL_PATH = Path("models/zeus_arm_margin_fails.json")
MARGIN_FAIL_LIMIT = 3


def _is_clean_margin_reject(exc: BaseException) -> bool:
    """Only explicit insufficient-margin — no fill possible (owner F1)."""
    return "insufficient margin" in str(exc).lower()


def _is_uncertain_entry_error(exc: BaseException) -> bool:
    """Timeout / other ExchangeError — order may have filled; need pending_verify."""
    if _is_clean_margin_reject(exc):
        return False
    name = type(exc).__name__
    msg = str(exc).lower()
    if name in ("ExchangeError", "TimeoutError"):
        return True
    if "timeout" in msg:
        return True
    return False


def _is_margin_or_exchange_reject(exc: BaseException) -> bool:
    """Any entry error that must not kill the tick (margin or uncertain)."""
    return _is_clean_margin_reject(exc) or _is_uncertain_entry_error(exc)


def _margin_fail_key(symbol: str, signal_id: str | None) -> str:
    return f"{symbol}|{signal_id or ''}"


def load_margin_fails(path: Path | None = None) -> dict[str, int]:
    path = path or MARGIN_FAIL_PATH
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fails = raw.get("fails") if isinstance(raw, dict) else raw
        if isinstance(fails, dict):
            return {str(k): int(v) for k, v in fails.items()}
    except Exception:
        pass
    return {}


def save_margin_fails(fails: dict[str, int], path: Path = MARGIN_FAIL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"fails": fails, "ts": time.time()}, ensure_ascii=False, indent=2)
    path.write_text(body + "\n", encoding="utf-8")


def bump_margin_fail(symbol: str, signal_id: str | None, path: Path | None = None) -> int:
    path = path or MARGIN_FAIL_PATH
    fails = load_margin_fails(path)
    key = _margin_fail_key(symbol, signal_id)
    n = int(fails.get(key) or 0) + 1
    fails[key] = n
    save_margin_fails(fails, path)
    return n


def clear_margin_fail(symbol: str, signal_id: str | None, path: Path | None = None) -> None:
    path = path or MARGIN_FAIL_PATH
    fails = load_margin_fails(path)
    key = _margin_fail_key(symbol, signal_id)
    if key in fails:
        del fails[key]
        save_margin_fails(fails, path)




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


async def run_arm_entries(
    broker: LiveBroker,
    rows: Iterable[dict[str, Any]],
    *,
    max_entries: int = 3,
    vst: bool | None = None,
    send_tg: Any = None,
    fresh_max_sec: float = FRESH_MAX_SEC,
    entries_path: Path = ENTRIES_PATH,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """≤3 entries per tick. Returns (entered, skip_counts)."""
    if vst is None:
        vst = (os.environ.get("ZEUS_MAINNET") or "").strip() != "1"

    skipped: dict[str, int] = {
        "same_side_guard": 0,
        "vol_cooldown": 0,
        "conf": 0,
        "margin": 0,
        "other": 0,
    }

    def _skip(reason: str) -> None:
        if reason in skipped:
            skipped[reason] += 1
        else:
            skipped["other"] += 1

    row_list = list(rows)
    if not row_list:
        logger.info("arm: empty feed — 0 entries (Stage A idle)")
        return [], skipped

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
            _skip("other")
            continue
        if not is_fresh(row, max_sec=fresh_max_sec):
            logger.info(
                "arm: skip %s — stale retest age>%ss",
                row.get("symbol"),
                int(fresh_max_sec),
            )
            _skip("other")
            continue
        cand = candidate_from_signal_dict(row)
        if cand is None:
            _skip("conf")
            continue
        if cand.symbol in open_syms:
            logger.info("arm: skip %s — already open on exchange", cand.symbol)
            _skip("other")
            continue
        if open_n >= MAX_OPEN:
            logger.info("arm: skip — exchange max_open=%d", MAX_OPEN)
            _skip("other")
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
            _skip("same_side_guard")
            continue

        try:
            from astra_bot.decision.reentry_guard import check as _vol_cd_check
            from astra_bot.decision.reentry_guard import note_skip as _vol_cd_skip

            _vb, _vu = _vol_cd_check(cand.symbol, side_l)
            if _vb:
                logger.info(
                    "arm: skip %s %s — vol_cooldown until=%.0f",
                    cand.symbol,
                    side_l,
                    _vu or 0,
                )
                _vol_cd_skip(cand.symbol, side_l, _vu)
                _skip("vol_cooldown")
                continue
        except Exception as exc:
            logger.debug("arm vol_cooldown check: %s", type(exc).__name__)

        if cand.step_size is None or cand.step_size == Decimal("0.001"):
            cand.step_size = await resolve_step_size(broker, cand.symbol)

        # Anti-loop: skip symbols with ≥3 consecutive margin rejects
        try:
            _mf = load_margin_fails()
            _mk = _margin_fail_key(cand.symbol, sid or None)
            if int(_mf.get(_mk) or 0) >= MARGIN_FAIL_LIMIT:
                logger.warning(
                    "arm: skip %s — margin_fail_limit %d reached",
                    cand.symbol,
                    MARGIN_FAIL_LIMIT,
                )
                _skip("margin")
                if sid:
                    seen.add(sid)
                    append_entry_record(
                        {
                            "signal_id": sid,
                            "symbol": cand.symbol,
                            "side": cand.side,
                            "ts": time.time(),
                            "event": "entry_rejected_margin_limit",
                        },
                        entries_path,
                    )
                continue
        except Exception:
            pass

        plan = None
        try:
            plan = await plan_from_candidate(
                broker,
                cand,
                exchange_symbols=open_syms,
                exchange_open_count=open_n,
            )
            if plan is None:
                _skip("margin")
                continue
            try:
                dist = abs(float(cand.entry) - float(cand.stop)) / max(
                    abs(float(cand.entry)), 1e-12
                )
            except Exception:
                dist = 0.0
            if dist > 0.25:
                logger.error(
                    "arm: REJECT %s ghost stop entry=%s stop=%s dist=%.1f%%",
                    cand.symbol,
                    cand.entry,
                    cand.stop,
                    dist * 100,
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
                _skip("other")
                continue
            try:
                res = await broker.open_with_protection(plan)
            except Exception as protect_exc:
                err_s = f"{type(protect_exc).__name__}: {protect_exc}"
                append_journal(
                    {
                        "event": "entry_protect_failed",
                        "symbol": cand.symbol,
                        "signal_id": sid or None,
                        "error": err_s,
                    }
                )
                if send_tg is not None:
                    try:
                        await send_tg(
                            f"⚠️ {cand.symbol}: защита при входе отклонена — {err_s}"
                        )
                    except Exception as tg_exc:
                        logger.warning("arm protect TG: %s", type(tg_exc).__name__)
                if _is_clean_margin_reject(protect_exc):
                    nfail = bump_margin_fail(cand.symbol, sid or None)
                    _skip("margin")
                    if sid:
                        seen.add(sid)
                        append_entry_record(
                            {
                                "signal_id": sid,
                                "symbol": cand.symbol,
                                "side": cand.side,
                                "ts": time.time(),
                                "event": "entry_rejected",
                                "error": type(protect_exc).__name__,
                                "error_msg": str(protect_exc)[:300],
                                "margin_fails": nfail,
                            },
                            entries_path,
                        )
                    logger.warning(
                        "arm: %s clean margin reject swallowed (fail=%d) — tick continues",
                        cand.symbol,
                        nfail,
                    )
                    continue
                # Timeout / other ExchangeError: fall through to outer except for pending_verify
                raise
            fill_px = (res or {}).get("fill_price")
            slip = (res or {}).get("slip_pct")
            fact_qty = (res or {}).get("fact_qty") or plan.quantity
            plan_qty = (res or {}).get("plan_qty") or plan.quantity
            qty_ratio = (res or {}).get("qty_ratio")
            be_moved = bool((res or {}).get("be_moved"))
            _archive_prior_plan_if_any(plan.symbol, sid or None)
            upsert_plan(
                plan.symbol,
                side=plan.side,
                qty=fact_qty,
                stop=plan.stop_price,
                tp=plan.take_profit_price,
                entry=fill_px or cand.entry,
                leverage=plan.leverage,
                signal_id=sid or None,
                be_moved=be_moved,
                plan_qty=plan_qty,
                qty_ratio=qty_ratio,
            )
            # qty-parity / protect alerts: one claim per signal, send ALL
            alert_list = list((res or {}).get("alerts") or [])
            if alert_list:
                claim_ok = try_claim(plan.symbol, "qty_parity_alert", sid or None)
                for alert in alert_list:
                    if claim_ok and send_tg is not None:
                        try:
                            await send_tg(alert)
                        except Exception as exc:
                            logger.warning("arm alert TG: %s", type(exc).__name__)
                    append_journal(
                        {
                            "event": "qty_parity_alert",
                            "symbol": plan.symbol,
                            "signal_id": sid or None,
                            "text": alert,
                            "fact_qty": str(fact_qty),
                            "plan_qty": str(plan_qty),
                            "qty_ratio": qty_ratio,
                        }
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
                        "tp": str(plan.take_profit_price)
                        if plan.take_profit_price
                        else None,
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
                "arm entry %s failed: %s",
                cand.symbol,
                type(exc).__name__,
            )
            try:
                mark_cooldown(cand.strategy, cand.symbol, cand.side)
            except Exception:
                pass
            clean = _is_clean_margin_reject(exc)
            uncertain = _is_uncertain_entry_error(exc)
            nfail = bump_margin_fail(cand.symbol, sid or None) if clean else 0
            event = (
                "entry_rejected"
                if clean
                else ("entry_pending_verify" if uncertain else "entry_failed")
            )
            append_entry_record(
                {
                    "signal_id": sid or f"failed|{cand.symbol}|{int(time.time())}",
                    "symbol": cand.symbol,
                    "side": cand.side,
                    "ts": time.time(),
                    "event": event,
                    "error": type(exc).__name__,
                    "error_msg": str(exc)[:300],
                    "margin_fails": nfail or None,
                },
                entries_path,
            )
            append_journal(
                {
                    "event": event,
                    "symbol": cand.symbol,
                    "side": cand.side,
                    "signal_id": sid or None,
                    "error": type(exc).__name__,
                    "error_msg": str(exc)[:200],
                }
            )
            if send_tg is not None:
                try:
                    if clean:
                        await send_tg(
                            f"⚠️ {cand.symbol}: вход отклонён — {type(exc).__name__}: {exc}"
                        )
                    elif uncertain:
                        await send_tg(
                            f"⚠️ {cand.symbol}: вход требует проверки"
                        )
                    else:
                        await send_tg(
                            f"⚠️ {cand.symbol}: вход ошибка — {type(exc).__name__}: {exc}"
                        )
                except Exception:
                    pass
            if clean:
                # Explicit insufficient margin — no fill, no ghost
                _skip("margin")
                if sid:
                    seen.add(sid)
                logger.warning(
                    "arm: %s clean margin reject (fail=%d/%d) — tick continues",
                    cand.symbol,
                    nfail,
                    MARGIN_FAIL_LIMIT,
                )
                continue
            if uncertain and plan is not None:
                # Timeout / other ExchangeError — order may exist; verify next tick
                try:
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
                _skip("other")
                logger.warning(
                    "arm: %s uncertain entry error — pending_verify ghost, tick continues",
                    cand.symbol,
                )
                continue
            # Unknown error — still do not kill whole tick (owner №25)
            logger.error(
                "arm entry %s unexpected error — continuing tick (no re-raise)",
                cand.symbol,
            )
            _skip("other")
            continue

        try:
            clear_margin_fail(cand.symbol, sid or None)
        except Exception:
            pass
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
            Decimal(str((res or {}).get("fact_qty") or plan.quantity)),
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
                "qty": str((res or {}).get("fact_qty") or plan.quantity),
                "plan_qty": str((res or {}).get("plan_qty") or plan.quantity),
                "qty_ratio": (res or {}).get("qty_ratio"),
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
    return results, skipped
