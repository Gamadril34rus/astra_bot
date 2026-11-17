"""LIVE TG income reporting — exact sums, sign discipline, funding.

Pure helpers for rocket / funding / pending-queue formatting.
Does not place orders or change risk. Owner 18.10.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PENDING_PATH = Path("models/zeus_live_tg_pending.json")
FUNDING_STATE_PATH = Path("models/zeus_funding_tg_state.json")
FUNDING_DUST = 0.10  # |funding| below this is silent


def _row_type(row: dict[str, Any]) -> str:
    return str(row.get("_incomeType") or row.get("incomeType") or "").upper()


def _row_value(row: dict[str, Any]) -> float | None:
    try:
        return float(row.get("income") or 0)
    except (TypeError, ValueError):
        return None


def _row_time_ms(row: dict[str, Any]) -> int | None:
    for k in ("time", "timestamp", "ts", "incomeTime"):
        if row.get(k) is None:
            continue
        try:
            v = float(row[k])
            # seconds vs ms
            if v < 1e12:
                v *= 1000.0
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def aggregate_income(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum REALIZED_PNL + TRADING_FEE/COMMISSION + FUNDING_FEE (signed).

    Returns {net, pnl, fees, funding, parts[]} where:
      pnl     = sum REALIZED_PNL
      fees    = sum TRADING_FEE + COMMISSION (usually negative)
      funding = sum FUNDING_FEE (signed; long pays short → short gets +)
      net     = pnl + fees + funding
    """
    pnl = 0.0
    fees = 0.0
    funding = 0.0
    parts: list[dict[str, Any]] = []
    for row in rows or []:
        it = _row_type(row)
        val = _row_value(row)
        if val is None:
            continue
        if it == "REALIZED_PNL":
            pnl += val
            parts.append({"type": it, "value": val, "time": _row_time_ms(row)})
        elif it in ("TRADING_FEE", "COMMISSION"):
            fees += val  # keep sign (BingX usually negative)
            parts.append({"type": it, "value": val, "time": _row_time_ms(row)})
        elif it == "FUNDING_FEE":
            funding += val
            parts.append({"type": it, "value": val, "time": _row_time_ms(row)})
    net = pnl + fees + funding
    return {
        "net": net,
        "pnl": pnl,
        "fees": fees,
        "funding": funding,
        "parts": parts,
        "has_realized": any(p["type"] == "REALIZED_PNL" for p in parts),
    }


def format_usd(amount: float | None) -> str | None:
    """Signed USD for TG. None if amount unknown — NEVER invent a sign."""
    if amount is None:
        return None
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return None
    if abs(v) < 5e-3:  # treat ~0 as exact zero display
        return "0.00$"
    if v > 0:
        return f"+{v:.2f}$"
    return f"-{abs(v):.2f}$"


def format_rocket(
    symbol: str,
    side: str,
    *,
    net: float,
    reason: str = "выход",
    funding: float = 0.0,
    fees: float = 0.0,
) -> str:
    """🏁 SYM ЛОНГ|ШОРТ: закрыта +X.XX$ нетто (reason; funding ±Y.YY$, комиссии -Z.ZZ$)."""
    base = symbol.replace("-USDT", "").replace("USDT", "")
    side_l = (side or "").lower()
    side_ru = "ЛОНГ" if side_l == "long" else "ШОРТ"
    net_s = format_usd(net) or "0.00$"
    fund_s = format_usd(funding) if funding else "0.00$"
    # fees usually negative; show as paid amount with minus
    if fees is None:
        fee_s = "0.00$"
    elif fees <= 0:
        fee_s = f"-{abs(fees):.2f}$"
    else:
        fee_s = format_usd(fees) or "0.00$"
    reason_s = reason or "выход"
    return (
        f"🏁 {base} {side_ru}: закрыта {net_s} нетто "
        f"({reason_s}; funding {fund_s}, комиссии {fee_s})"
    )


def format_pending_rocket(symbol: str, side: str) -> str:
    """No sign, no '?'. Follow-up will carry the exact sum."""
    base = symbol.replace("-USDT", "").replace("USDT", "")
    side_l = (side or "").lower()
    side_ru = "ЛОНГ" if side_l == "long" else "ШОРТ"
    return f"🏁 {base} {side_ru}: закрыта — сумма уточняется, пришлю следом"



# ---- pending exit queue (survives tick via models/) ----

def load_pending(path: Path = PENDING_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        return [x for x in items if isinstance(x, dict)]
    except Exception as exc:
        logger.warning("tg_pending load: %s", type(exc).__name__)
        return []


def save_pending(items: list[dict[str, Any]], path: Path = PENDING_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"items": items, "ts": time.time()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def enqueue_pending(item: dict[str, Any], path: Path = PENDING_PATH) -> None:
    items = load_pending(path)
    key = f"{item.get('symbol')}|{item.get('signal_id')}|{item.get('since_ms')}"
    items = [x for x in items if f"{x.get('symbol')}|{x.get('signal_id')}|{x.get('since_ms')}" != key]
    items.append(dict(item))
    save_pending(items, path)


def remove_pending(symbol: str, signal_id: str | None, path: Path = PENDING_PATH) -> None:
    items = load_pending(path)
    sid = signal_id or ""
    items = [x for x in items if not (x.get("symbol") == symbol and (x.get("signal_id") or "") == sid)]
    save_pending(items, path)


# ---- funding TG dedup state ----

def load_funding_state(path: Path = FUNDING_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"seen": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"seen": {}}
        seen = raw.get("seen") if isinstance(raw.get("seen"), dict) else {}
        return {"seen": {str(k): v for k, v in seen.items()}}
    except Exception:
        return {"seen": {}}


def save_funding_state(state: dict[str, Any], path: Path = FUNDING_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"seen": state.get("seen") or {}, "ts": time.time()}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def funding_event_key(symbol: str, time_ms: int, amount: float) -> str:
    return f"{symbol}|{int(time_ms)}|{round(float(amount), 6)}"


def claim_funding_event(
    symbol: str,
    time_ms: int,
    amount: float,
    path: Path = FUNDING_STATE_PATH,
) -> bool:
    """True if this funding event is new and now claimed."""
    state = load_funding_state(path)
    key = funding_event_key(symbol, time_ms, amount)
    if key in (state.get("seen") or {}):
        return False
    seen = dict(state.get("seen") or {})
    # prune > 14d
    now = time.time()
    pruned = {k: v for k, v in seen.items() if now - float(v) < 14 * 24 * 3600}
    pruned[key] = now
    save_funding_state({"seen": pruned}, path)
    return True


def _income_position_side(row: dict[str, Any]) -> str | None:
    raw = row.get("positionSide") or row.get("position_side")
    info = row.get("info")
    if info is None:
        info = row.get("tradeInfo")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except (TypeError, ValueError):
            info = None
    if raw is None and isinstance(info, dict):
        raw = info.get("positionSide") or info.get("position_side")
    side = str(raw or "").strip().lower()
    return side if side in {"long", "short"} else None


def _income_trade_id(row: dict[str, Any]) -> str | None:
    for key in ("tradeId", "trade_id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def filter_income_rows_for_position_side(
    rows: list[dict[str, Any]],
    position_side: str,
    *,
    trade_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Map income rows to one hedge leg using positionSide or matched fill tradeId.

    The user/income endpoint is often symbol-wide and may omit positionSide. In
    that case only a tradeId already attributed from allFillOrders is accepted.
    Unlinked realized-PnL/fee rows make the batch ambiguous and invalidate it.
    Funding without a side or tradeId is omitted rather than assigned to a leg.
    """
    side = str(position_side or "").strip().lower()
    if side not in {"long", "short"}:
        return [], True
    allowed_trade_ids = {str(value).strip() for value in (trade_ids or []) if str(value).strip()}
    selected: list[dict[str, Any]] = []
    ambiguous = False
    for row in rows or []:
        if not isinstance(row, dict):
            ambiguous = True
            continue
        row_side = _income_position_side(row)
        if row_side is not None:
            if row_side == side:
                selected.append(row)
            continue
        trade_id = _income_trade_id(row)
        if trade_id:
            if trade_id in allowed_trade_ids:
                selected.append(row)
            # A different tradeId is not attributable to this position leg.
            continue
        if _row_type(row) == "FUNDING_FEE":
            continue
        if _row_type(row) in {"REALIZED_PNL", "TRADING_FEE", "COMMISSION"}:
            ambiguous = True
    if ambiguous:
        return [], True
    return selected, False


async def exit_net_from_client(
    client: Any,
    symbol: str,
    since_ms: int | None,
    *,
    position_side: str | None = None,
    trade_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Fetch income and, in hedge mode, attribute only provable rows to one leg."""
    if not hasattr(client, "get_realized_income"):
        return aggregate_income([])
    try:
        rows = await client.get_realized_income(symbol, start_ms=since_ms)
    except Exception as exc:
        logger.warning("exit_net_from_client %s: %s", symbol, type(exc).__name__)
        rows = []
    if position_side is None:
        return aggregate_income(rows or [])
    selected, ambiguous = filter_income_rows_for_position_side(
        rows or [], position_side, trade_ids=trade_ids
    )
    if ambiguous:
        return {
            **aggregate_income([]),
            "ambiguous_position_side": True,
            "ambiguous_rows": len(rows or []),
        }
    return {
        **aggregate_income(selected),
        "ambiguous_position_side": False,
    }


async def fetch_income_with_retries(
    client: Any,
    symbol: str,
    since_ms: int | None,
    *,
    attempts: int = 3,
    delay_sec: float = 7.0,
    position_side: str | None = None,
    trade_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Poll income up to attempts; ambiguous hedge attribution is not retryable."""
    import asyncio

    best = aggregate_income([])
    for i in range(max(1, attempts)):
        agg = await exit_net_from_client(
            client,
            symbol,
            since_ms,
            position_side=position_side,
            trade_ids=trade_ids,
        )
        if agg.get("ambiguous_position_side"):
            return agg
        if agg.get("has_realized") or abs(float(agg.get("net") or 0)) > 1e-9:
            return agg
        best = agg
        if bool(getattr(client, "income_endpoint_cooldown_active", False)):
            break
        if i + 1 < attempts:
            try:
                await asyncio.sleep(delay_sec)
            except Exception:
                break
    return best


# ---- dogon (catch-up) for incomplete income rockets ----

TRADES_PATH = Path("models/zeus_live_trades.jsonl")
DOGON_CONFIRM_FRAC = 0.5
DOGON_TTL_SEC = 24 * 3600
SANITY_FRAC = 0.10


def plan_est_risk(entry: float | None, stop: float | None, qty: float | None) -> float | None:
    """|(stop − entry) × qty| — plan risk in $."""
    try:
        e = float(entry)
        s = float(stop)
        q = abs(float(qty))
    except (TypeError, ValueError):
        return None
    if q <= 0:
        return None
    return abs(s - e) * q


def income_too_small(agg: dict[str, Any], est: float | None) -> bool:
    """has_realized but |REALIZED_PNL| < 0.10·est and est > 50."""
    if est is None or est <= 50:
        return False
    if not agg.get("has_realized") or agg.get("ambiguous_position_side"):
        return False
    try:
        rp = abs(float(agg.get("pnl") or 0))
    except (TypeError, ValueError):
        return False
    return rp < SANITY_FRAC * float(est)


def format_rocket_correction(
    symbol: str,
    side: str,
    *,
    net: float,
    reason: str = "выход",
    funding: float = 0.0,
    fees: float = 0.0,
    prefix: str = "уточнение: ",
) -> str:
    return prefix + format_rocket(
        symbol, side, net=net, reason=reason, funding=funding, fees=fees
    )


def format_estimate_rocket(
    symbol: str,
    side: str,
    *,
    est: float,
    reason: str = "стоп",
) -> str:
    """Owner 31.10: NEVER invent −1R dollars. Soft note only (prefer no TG)."""
    base = symbol.replace("-USDT", "").replace("USDT", "")
    side_l = (side or "").lower()
    side_ru = "ЛОНГ" if side_l == "long" else "ШОРТ"
    _ = est  # kept for API compat; intentionally unused in text
    return (
        f"🏁 {base} {side_ru}: закрыта — точная сумма в income не подтвердилась "
        f"({reason}; смотри UI, без оценки $)"
    )


def patch_trade_exit_pnl(
    *,
    symbol: str,
    signal_id: str | None,
    pnl: float,
    fees: float,
    funding: float,
    pnl_source: str = "income_confirmed",
    path: Path = TRADES_PATH,
    exit_price: float | None = None,
    position_id: str | None = None,
    entry: Any = None,
    qty: Any = None,
) -> bool:
    """Patch one unconfirmed exit row; never fan one PnL out across reused signal IDs."""
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.warning("patch_trade load: %s", type(exc).__name__)
        return False

    sid = str(signal_id or "")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            str(row.get("event") or "") != "exit"
            or str(row.get("symbol") or "") != symbol
            or (sid and str(row.get("signal_id") or "") != sid)
        ):
            continue
        source = str(row.get("pnl_source") or "")
        if source in ("income_confirmed", "income_corrected", "position_history"):
            continue
        if row.get("pnl") is not None and source not in (
            "pending_dogon",
            "income",
            "income_backfill",
        ):
            continue
        candidates.append((index, row))

    selected = _select_exit_row_by_context(
        [row for _, row in candidates],
        entry=entry,
        qty=qty,
    )
    if selected is None:
        return False
    selected_index = next(index for index, row in candidates if row is selected)
    selected["pnl"] = round(float(pnl), 4)
    selected["fees"] = float(fees)
    selected["funding_fee"] = float(funding)
    selected["pnl_source"] = pnl_source
    if exit_price is not None:
        selected["exit"] = round(float(exit_price), 8)
        selected["exit_price"] = round(float(exit_price), 8)
    if position_id is not None:
        selected["position_id"] = str(position_id)
    selected["corrected_ts"] = time.time()
    lines[selected_index] = json.dumps(
        selected, ensure_ascii=False, separators=(",", ":")
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(lines)
        if body:
            body += "\n"
        path.write_text(body, encoding="utf-8")
    except Exception as exc:
        logger.warning("patch_trade save: %s", type(exc).__name__)
        return False
    return True


def _find_confirmed_exit_row(
    symbol: str,
    signal_id: str | None,
    path: Path = TRADES_PATH,
    *,
    entry: Any = None,
    qty: Any = None,
) -> dict[str, Any] | None:
    """Find one confirmed exit row for this exact signal and entry context."""
    sid = str(signal_id or "")
    if not sid or not path.exists():
        return None
    matches: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                str(row.get("event") or "") == "exit"
                and str(row.get("symbol") or "") == symbol
                and str(row.get("signal_id") or "") == sid
                and row.get("pnl") is not None
                and str(row.get("pnl_source") or "")
                in {
                    "income",
                    "income_backfill",
                    "income_confirmed",
                    "income_corrected",
                    "position_history",
                    "fills",
                }
            ):
                matches.append(row)
    except Exception as exc:
        logger.warning("confirmed exit lookup %s: %s", symbol, type(exc).__name__)
    return _select_exit_row_by_context(matches, entry=entry, qty=qty)


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def load_entry_events(
    path: Path = TRADES_PATH,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Load execution timestamps and sizing from immutable entry ledger events."""
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if not path.exists():
        return result
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("event") or "") != "entry":
                continue
            symbol = str(row.get("symbol") or "")
            signal_id = str(row.get("signal_id") or "")
            if not symbol or not signal_id:
                continue
            opened_ms = _row_time_ms({"time": row.get("ts")})
            if opened_ms is None:
                opened_ms = _row_time_ms({"time": row.get("opened_at_ms")})
            if opened_ms is None:
                continue
            event = {
                "open_ms": opened_ms,
                "entry": _float_or_none(
                    row.get("fill_price") or row.get("entry") or row.get("plan_entry")
                ),
                "qty": _float_or_none(row.get("qty")),
            }
            result.setdefault((symbol, signal_id), []).append(event)
    except Exception as exc:
        logger.warning("entry event lookup: %s", type(exc).__name__)
    for events in result.values():
        events.sort(key=lambda event: int(event["open_ms"]))
    return result


def select_entry_event(
    events: list[dict[str, Any]],
    *,
    entry: Any = None,
    qty: Any = None,
) -> dict[str, Any] | None:
    """Select one immutable entry event by price/size; ambiguity stays unresolved."""
    candidates = list(events or [])
    if not candidates:
        return None

    entry_target = _float_or_none(entry)
    if entry_target is not None:
        price_matches = [
            event
            for event in candidates
            if event.get("entry") is not None
            and abs(float(event["entry"]) - entry_target) / entry_target <= 0.005
        ]
        if price_matches:
            candidates = price_matches

    qty_target = _float_or_none(qty)
    if qty_target is not None:
        qty_matches = [
            event
            for event in candidates
            if event.get("qty") is not None
            and abs(float(event["qty"]) - qty_target) / qty_target <= 0.05
        ]
        if qty_matches:
            candidates = qty_matches

    return candidates[0] if len(candidates) == 1 else None


def _select_exit_row_by_context(
    rows: list[dict[str, Any]],
    *,
    entry: Any = None,
    qty: Any = None,
) -> dict[str, Any] | None:
    candidates = list(rows or [])
    if not candidates:
        return None

    entry_target = _float_or_none(entry)
    if entry_target is not None:
        comparable = [
            row for row in candidates
            if _float_or_none(row.get("entry") or row.get("entry_price")) is not None
        ]
        if comparable:
            matches = [
                row for row in comparable
                if abs(
                    float(row.get("entry") or row.get("entry_price")) - entry_target
                ) / entry_target <= 0.005
            ]
            if not matches:
                return None
            candidates = matches

    qty_target = _float_or_none(qty)
    if qty_target is not None:
        comparable = [
            row for row in candidates if _float_or_none(row.get("qty")) is not None
        ]
        if comparable:
            matches = [
                row for row in comparable
                if abs(float(row["qty"]) - qty_target) / qty_target <= 0.05
            ]
            if not matches:
                return None
            candidates = matches

    return candidates[0] if len(candidates) == 1 else None


def backfill_unqueued_exit_rows(
    *,
    trades_path: Path = TRADES_PATH,
    pending_path: Path = PENDING_PATH,
    flags_path: Path = Path("models/zeus_live_tg_flags.json"),
    entry_events_by_key: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    now: float | None = None,
    max_age_days: int = 13,
) -> dict[str, int]:
    """Requeue recent exits missing PnL or close price, using exact entry context."""
    from astra_bot.decision.live_tg_flags import already_sent

    now_ts = float(now if now is not None else time.time())
    now_ms = int(now_ts * 1000)
    entry_events = (
        entry_events_by_key
        if entry_events_by_key is not None
        else load_entry_events(trades_path)
    )
    pending = load_pending(pending_path)
    try:
        lines = trades_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {"added": 0, "updated": 0, "ambiguous": 0, "expired": 0}
    except Exception as exc:
        logger.warning("orphan exit backfill load: %s", type(exc).__name__)
        return {"added": 0, "updated": 0, "ambiguous": 0, "expired": 0}

    stats = {"added": 0, "updated": 0, "ambiguous": 0, "expired": 0}
    max_age_ms = max(1, int(max_age_days)) * 24 * 3600 * 1000
    changed = False
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("event") or "") != "exit":
            continue
        if row.get("pnl") is not None and (
            row.get("exit_price") is not None or row.get("exit") is not None
        ):
            continue

        symbol = str(row.get("symbol") or "")
        side = str(row.get("side") or "").strip().lower()
        signal_id = str(row.get("signal_id") or "")
        if not symbol or not side or not signal_id:
            continue
        entry_events_for_row = entry_events.get((symbol, signal_id), [])
        entry_event = select_entry_event(
            entry_events_for_row,
            entry=row.get("entry") or row.get("entry_price"),
            qty=row.get("qty"),
        )
        if entry_event is None:
            stats["ambiguous"] += 1
            continue
        entry_open_ms = int(entry_event["open_ms"])
        if entry_open_ms < now_ms - max_age_ms:
            stats["expired"] += 1
            continue
        if entry_open_ms > now_ms + 60_000:
            stats["ambiguous"] += 1
            continue

        since_ms = max(0, entry_open_ms - 60_000)
        entry_price = row.get("entry") or row.get("entry_price") or entry_event.get("entry")
        qty = row.get("qty") or entry_event.get("qty")
        exit_price = row.get("exit_price")
        if exit_price is None:
            exit_price = row.get("exit")
        raw_reason = str(row.get("reason") or row.get("exit_reason") or "")
        reason = (
            "закрытие по биржевой истории"
            if raw_reason.strip().lower() in {"", "no_fills", "unknown"}
            else raw_reason
        )
        trade_ids = row.get("trade_ids")
        if not isinstance(trade_ids, list):
            trade_ids = []
        flag_sent = (
            already_sent(symbol, "exit", signal_id, flags_path)
            if len(entry_events_for_row) <= 1
            else False
        )

        match_index = None
        for index, existing in enumerate(pending):
            if (
                str(existing.get("symbol") or "") != symbol
                or str(existing.get("signal_id") or "") != signal_id
            ):
                continue
            old_entry = _float_or_none(existing.get("entry"))
            new_entry = _float_or_none(entry_price)
            entry_match = False
            if old_entry is not None and new_entry is not None:
                if abs(old_entry - new_entry) / new_entry > 0.005:
                    continue
                entry_match = True

            old_qty = _float_or_none(existing.get("qty"))
            new_qty = _float_or_none(qty)
            qty_match = False
            if old_qty is not None and new_qty is not None:
                if abs(old_qty - new_qty) / new_qty > 0.05:
                    continue
                qty_match = True

            if not entry_match and not qty_match:
                try:
                    old_since = int(float(existing.get("since_ms") or 0))
                except (TypeError, ValueError, OverflowError):
                    old_since = 0
                if abs(old_since - since_ms) > 120_000:
                    continue
            match_index = index
            break

        if match_index is not None:
            existing = dict(pending[match_index])
            item_changed = False
            try:
                old_since = int(float(existing.get("since_ms") or 0))
            except (TypeError, ValueError, OverflowError):
                old_since = 0
            if old_since != since_ms:
                existing["since_ms"] = since_ms
                existing["opened_at_ms"] = entry_open_ms
                item_changed = True
            for key, value in (
                ("entry", entry_price),
                ("qty", qty),
                ("exit_price", exit_price),
                ("reason", reason),
                ("trade_ids", trade_ids),
            ):
                if value is not None and not existing.get(key):
                    existing[key] = value
                    item_changed = True
            if row.get("tg_card_sent") and not existing.get("card_sent"):
                existing["card_sent"] = True
                item_changed = True
            if (
                (row.get("tg_followup_sent") or flag_sent)
                and not existing.get("followup_sent")
            ):
                existing["followup_sent"] = True
                item_changed = True
            if item_changed:
                existing["ts"] = now_ts
                pending[match_index] = existing
                stats["updated"] += 1
                changed = True
            continue

        pending.append(
            {
                "symbol": symbol,
                "side": side,
                "signal_id": signal_id,
                "since_ms": since_ms,
                "opened_at_ms": entry_open_ms,
                "reason": reason,
                "entry": entry_price,
                "qty": qty,
                "trade_ids": trade_ids,
                "exit_price": exit_price,
                "ts": now_ts,
                "card_sent": bool(row.get("tg_card_sent")),
                "followup_sent": bool(row.get("tg_followup_sent") or flag_sent),
            }
        )
        stats["added"] += 1
        changed = True

    if changed:
        save_pending(pending, pending_path)
    if any(stats.values()):
        logger.info(
            "orphan exit backfill added=%d updated=%d ambiguous=%d expired=%d",
            stats["added"],
            stats["updated"],
            stats["ambiguous"],
            stats["expired"],
        )
    return stats


def mark_exit_delivery(
    item: dict[str, Any],
    *,
    path: Path = TRADES_PATH,
    card_sent: bool | None = None,
    followup_sent: bool | None = None,
) -> bool:
    """Persist Telegram delivery state on the one matching exit ledger row."""
    if not path.exists():
        return False
    symbol = str(item.get("symbol") or "")
    signal_id = str(item.get("signal_id") or "")
    if not symbol or not signal_id:
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.warning("tg delivery ledger load: %s", type(exc).__name__)
        return False
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            str(row.get("event") or "") == "exit"
            and str(row.get("symbol") or "") == symbol
            and str(row.get("signal_id") or "") == signal_id
        ):
            candidates.append((index, row))
    selected = _select_exit_row_by_context(
        [row for _, row in candidates],
        entry=item.get("entry"),
        qty=item.get("qty"),
    )
    if selected is None:
        return False
    selected_index = next(index for index, row in candidates if row is selected)
    if card_sent is not None:
        selected["tg_card_sent"] = bool(card_sent)
        if card_sent:
            selected["tg_card_sent_ts"] = time.time()
    if followup_sent is not None:
        selected["tg_followup_sent"] = bool(followup_sent)
        if followup_sent:
            selected["tg_followup_sent_ts"] = time.time()
    lines[selected_index] = json.dumps(
        selected, ensure_ascii=False, separators=(",", ":")
    )
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("tg delivery ledger save: %s", type(exc).__name__)
        return False
    return True


def match_position_history(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    side: str,
    target_open_ms: int,
    tolerance_ms: int = 120_000,
) -> dict[str, Any] | None:
    """Return one exact closed-position-history match; never guess on ambiguity."""
    target = int(target_open_ms)
    wanted_symbol = str(symbol or "").upper().replace("-", "")
    wanted_side = str(side or "").upper()
    matches: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("symbol") or symbol).upper().replace("-", "")
        row_side = str(row.get("positionSide") or row.get("position_side") or "").upper()
        if row_symbol != wanted_symbol or row_side != wanted_side:
            continue
        open_ms = _row_time_ms({"time": row.get("openTime") or row.get("open_time")})
        if open_ms is None or abs(open_ms - target) > int(tolerance_ms):
            continue
        try:
            funding = float(row.get("totalFunding") or row.get("total_funding") or 0.0)
            commission = float(
                row.get("positionCommission")
                if row.get("positionCommission") is not None
                else row.get("position_commission") or 0.0
            )
            net_raw = row.get("netProfit")
            if net_raw is None:
                net_raw = row.get("net_profit")
            if net_raw is not None:
                net_pnl = float(net_raw)
            else:
                realised = row.get("realisedProfit")
                if realised is None:
                    realised = row.get("realizedProfit")
                if realised is None:
                    continue
                net_pnl = float(realised) + commission + funding
            exit_raw = row.get("avgClosePrice")
            if exit_raw is None:
                exit_raw = row.get("avg_close_price")
            exit_price = float(exit_raw) if exit_raw not in (None, "") else None
        except (TypeError, ValueError):
            continue
        close_ms = _row_time_ms(
            {"time": row.get("updateTime") or row.get("closeTime") or row.get("close_time")}
        )
        matches.append(
            {
                "symbol": symbol,
                "side": side,
                "open_time_ms": open_ms,
                "close_time_ms": close_ms,
                "position_id": row.get("positionId") or row.get("position_id"),
                "net_pnl": net_pnl,
                "pnl": net_pnl - funding,
                "fees": commission,
                "funding": funding,
                "exit_price": exit_price,
            }
        )
    return matches[0] if len(matches) == 1 else None


def _pending_exit_card_metadata(
    item: dict[str, Any], trades_path: Path,
) -> dict[str, Any]:
    """Fill missing card fields from the matching immutable entry row only."""
    details = dict(item)
    sid = str(details.get("signal_id") or "")
    if not sid or not trades_path.exists():
        return details
    needed = ("entry", "qty", "leverage", "stop", "target")
    if all(details.get(key) not in (None, "") for key in needed[:3]):
        return details
    try:
        for line in trades_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                str(row.get("event") or "") == "entry"
                and str(row.get("symbol") or "") == str(details.get("symbol") or "")
                and str(row.get("signal_id") or "") == sid
            ):
                for key in needed:
                    if details.get(key) in (None, ""):
                        candidate = row.get(key)
                        if candidate in (None, "") and key == "entry":
                            candidate = row.get("plan_entry") or row.get("fill_price")
                        if candidate in (None, "") and key == "target":
                            candidate = row.get("tp")
                        if candidate not in (None, ""):
                            details[key] = candidate
                break
    except Exception as exc:
        logger.warning("dogon card metadata lookup %s: %s", details.get("symbol"), type(exc).__name__)
    return details


async def _send_pending_exit_card(
    item: dict[str, Any], pnl: float, *, trades_path: Path,
) -> bool | None:
    """Send a delayed EXIT card from exact-signal metadata; None means insufficient data."""
    details = _pending_exit_card_metadata(item, trades_path)
    try:
        symbol = str(details.get("symbol") or "")
        side = str(details.get("side") or "").strip().lower()
        entry = float(details.get("entry") or 0)
        qty = abs(float(details.get("qty") or details.get("quantity") or 0))
        leverage = float(details.get("leverage") or details.get("lev") or 0)
        closed_at = float(details.get("closed_at") or details.get("ts") or 0) or None
        if (
            not symbol or side not in {"long", "short"} or entry <= 0
            or qty <= 0 or leverage <= 0
        ):
            logger.warning(
                "viz_error event=exit_card_deferred_missing_metadata symbol=%s signal_id=%s",
                symbol, details.get("signal_id"),
            )
            return None
        from astra_bot.decision.trade_viz import derive_exit_price, notify_exit_stats_card
        exit_price = float(details.get("exit_price") or 0.0) or derive_exit_price(
            side=side, entry=entry, pnl=float(pnl), qty=qty,
        )
        if exit_price <= 0:
            raise ValueError("derived_exit_price_not_positive")
        return bool(await notify_exit_stats_card(
            symbol=symbol, side=side, entry=entry, exit_price=exit_price,
            pnl=float(pnl), qty=qty, leverage=leverage,
            closed_at=closed_at, virtual=True,
        ))
    except Exception as exc:
        logger.warning(
            "viz_error event=exit_card_deferred_failed symbol=%s error=%s",
            item.get("symbol"), type(exc).__name__,
        )
        return False


async def _deliver_pending_exit_followup(
    item: dict[str, Any], *, pnl: float, message: str,
    send_tg: Any, trades_path: Path,
) -> tuple[dict[str, Any], bool]:
    """Deliver card + exact text once each; missing card metadata stays queued."""
    updated = dict(item)
    if not updated.get("card_sent"):
        card_result = await _send_pending_exit_card(
            updated, pnl, trades_path=trades_path,
        )
        if card_result is None:
            updated["card_sent"] = False
            updated["card_skipped_missing_metadata"] = True
        elif card_result:
            updated["card_sent"] = True
            updated.pop("card_skipped_missing_metadata", None)
            mark_exit_delivery(
                updated,
                path=trades_path,
                card_sent=True,
                followup_sent=bool(updated.get("followup_sent")),
            )
        else:
            updated["card_sent"] = False

    if not updated.get("followup_sent"):
        if send_tg is None:
            return updated, False
        try:
            await send_tg(message)
            updated["followup_sent"] = True
            mark_exit_delivery(
                updated,
                path=trades_path,
                card_sent=bool(updated.get("card_sent")),
                followup_sent=True,
            )
        except Exception as exc:
            logger.warning("dogon followup TG %s: %s", updated.get("symbol"), type(exc).__name__)
            return updated, False
    return updated, bool(updated.get("card_sent") and updated.get("followup_sent"))


async def resolve_pending_dogon(
    client: Any,
    send_tg: Any = None,
    *,
    path: Path = PENDING_PATH,
    trades_path: Path = TRADES_PATH,
    flags_path: Path = Path("models/zeus_live_tg_flags.json"),
    now: float | None = None,
    max_items: int = 5,
) -> int:
    """Resolve pending exits from confirmed ledger/income; never discard silently."""
    now_ts = float(now if now is not None else time.time())
    entry_events_by_key = load_entry_events(trades_path)
    backfill_unqueued_exit_rows(
        trades_path=trades_path,
        pending_path=path,
        flags_path=flags_path,
        entry_events_by_key=entry_events_by_key,
        now=now_ts,
    )
    items = load_pending(path)
    if not items:
        return 0

    def _pending_sort_key(item: dict[str, Any]) -> int:
        try:
            return int(float(item.get("since_ms") or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    items.sort(key=_pending_sort_key)
    batch_limit = max(1, int(max_items))
    batch = items[:batch_limit]
    remaining: list[dict[str, Any]] = list(items[len(batch):])
    sent = 0
    position_history_cache: dict[str, list[dict[str, Any]]] = {}
    position_history_start_by_symbol: dict[str, int] = {}
    for queued in batch:
        queued_symbol = str(queued.get("symbol") or "")
        queued_signal_id = str(queued.get("signal_id") or "")
        queued_entry_events = entry_events_by_key.get(
            (queued_symbol, queued_signal_id), []
        )
        entry_event = select_entry_event(
            queued_entry_events,
            entry=queued.get("entry"),
            qty=queued.get("qty"),
        )
        if len(queued_entry_events) > 1 and entry_event is None:
            continue
        entry_open_ms = int(entry_event["open_ms"]) if entry_event else None
        try:
            queued_since = int(float(queued.get("since_ms") or 0))
        except (TypeError, ValueError):
            queued_since = 0
        candidate_starts = [value for value in (
            queued_since,
            max(0, entry_open_ms - 60_000) if entry_open_ms is not None else 0,
        ) if value > 0]
        if queued_symbol and candidate_starts:
            candidate_start = min(candidate_starts)
            position_history_start_by_symbol[queued_symbol] = min(
                candidate_start,
                position_history_start_by_symbol.get(queued_symbol, candidate_start),
            )
    for item in batch:
        sym = str(item.get("symbol") or "")
        side = str(item.get("side") or "").strip().lower()
        sid = item.get("signal_id")
        since_ms = item.get("since_ms")
        item_entry_events = entry_events_by_key.get(
            (sym, str(sid) if sid is not None else ""), []
        )
        entry_event = select_entry_event(
            item_entry_events,
            entry=item.get("entry"),
            qty=item.get("qty"),
        )
        entry_event_ambiguous = len(item_entry_events) > 1 and entry_event is None
        entry_open_ms = int(entry_event["open_ms"]) if entry_event else None
        entry_price = item.get("entry") or (entry_event.get("entry") if entry_event else None)
        entry_qty = item.get("qty") or (entry_event.get("qty") if entry_event else None)
        reason = str(item.get("reason") or "стоп")
        try:
            est_f = float(item["est"]) if item.get("est") is not None else None
        except (TypeError, ValueError):
            est_f = None
        enqueued_ts = float(item.get("ts") or now_ts)
        age = now_ts - enqueued_ts
        if not sym:
            continue
        if side not in {"long", "short"}:
            logger.warning(
                "dogon: %s pending income has no valid LONG/SHORT side; retain without patch",
                sym,
            )
            remaining.append(item)
            continue

        # The independent income-backfill worker may already have resolved this
        # exact exit row. Consume that confirmed ledger result instead of polling
        # an ambiguous symbol-wide income endpoint forever.
        exit_price = item.get("exit_price")
        confirmed_row = _find_confirmed_exit_row(
            sym,
            str(sid) if sid is not None else None,
            trades_path,
            entry=entry_price,
            qty=entry_qty,
        )
        if confirmed_row is not None:
            source = str(confirmed_row.get("pnl_source") or "")
            try:
                amount = float(confirmed_row.get("pnl") or 0.0)
            except (TypeError, ValueError):
                logger.warning("dogon backfill has invalid PnL %s", sym)
                remaining.append(item)
                continue
            if source == "income_backfill":
                msg = (
                    f"уточнение: 🏁 {sym.replace('-USDT', '')} {side.upper()}: "
                    f"закрыта {format_usd(amount)} "
                    "после торговых комиссий (funding не включён: "
                    "backfill не подтвердил его отдельно)"
                )
            else:
                funding = float(confirmed_row.get("funding_fee") or 0.0)
                fees = float(confirmed_row.get("fees") or 0.0)
                if source in {"fills", "income"}:
                    fees = -abs(fees)
                prefix = (
                    "уточнение: подтверждено BingX Position History. "
                    if source == "position_history"
                    else ""
                )
                msg = prefix + format_rocket_correction(
                    sym,
                    side,
                    net=amount + funding,
                    reason=reason,
                    funding=funding,
                    fees=fees,
                )
            confirmed_exit_price = (
                confirmed_row.get("exit_price")
                if confirmed_row.get("exit_price") is not None
                else confirmed_row.get("exit")
            )
            if confirmed_exit_price is not None or item.get("exit_price") is not None:
                updated_item = dict(item)
                if not updated_item.get("entry") and entry_price is not None:
                    updated_item["entry"] = entry_price
                if not updated_item.get("qty") and entry_qty is not None:
                    updated_item["qty"] = entry_qty
                if not updated_item.get("exit_price"):
                    updated_item["exit_price"] = (
                        confirmed_exit_price
                        if confirmed_exit_price is not None
                        else item.get("exit_price")
                    )
                updated, delivered = await _deliver_pending_exit_followup(
                    updated_item, pnl=amount, message=msg, send_tg=send_tg,
                    trades_path=trades_path,
                )
                if delivered:
                    sent += 1
                else:
                    remaining.append(updated)
                continue

        income_since_ms = since_ms
        if entry_open_ms is not None:
            income_since_ms = max(0, int(entry_open_ms) - 60_000)
        elif since_ms is not None:
            try:
                income_since_ms = int(float(since_ms))
            except (TypeError, ValueError, OverflowError):
                income_since_ms = None
        try:
            agg = await exit_net_from_client(
                client,
                sym,
                income_since_ms,
                position_side=side,
                trade_ids=item.get("trade_ids"),
            )
        except Exception as exc:
            logger.warning("dogon exit_net %s: %s", sym, type(exc).__name__)
            remaining.append(item)
            continue
        rp = abs(float(agg.get("pnl") or 0))
        has = bool(agg.get("has_realized"))
        confirmed = has and (est_f is None or est_f <= 0 or rp >= DOGON_CONFIRM_FRAC * est_f)
        exit_price = agg.get("exit_price") or exit_price

        if not confirmed and entry_event_ambiguous:
            logger.warning(
                "dogon position_history %s: reused signal_id has ambiguous entry ledger; retain pending",
                sym,
            )

        # BingX Position History is a separate, position-level source. It can
        # resolve exits when fill history is empty and income rows lack hedge-side
        # identifiers. Match only one exact symbol/side/open-time record.
        if (
            (not confirmed or exit_price is None)
            and not entry_event_ambiguous
            and (since_ms is not None or entry_open_ms is not None)
            and hasattr(client, "get_position_history")
        ):
            try:
                since_value = int(float(since_ms)) if since_ms is not None else 0
                if sym not in position_history_cache:
                    fallback_start = (
                        max(0, entry_open_ms - 60_000)
                        if entry_open_ms is not None
                        else since_value
                    )
                    history_start = position_history_start_by_symbol.get(sym, fallback_start)
                    history_end = int(now_ts * 1000)
                    history_start = max(
                        history_start,
                        history_end - 30 * 24 * 3600 * 1000,
                    )
                    position_history_cache[sym] = await client.get_position_history(
                        sym,
                        start_ms=history_start,
                        end_ms=history_end,
                        page_index=0,
                        page_size=100,
                    )
                target_open_ms = int(
                    entry_open_ms
                    or item.get("opened_at_ms")
                    or (since_value + 60_000)
                )
                history = match_position_history(
                    position_history_cache[sym],
                    symbol=sym,
                    side=side,
                    target_open_ms=target_open_ms,
                )
            except Exception as exc:
                logger.warning("dogon position_history %s: %s", sym, type(exc).__name__)
                position_history_cache.setdefault(sym, [])
                history = None
            if history is not None:
                trade_pnl = float(history["pnl"])
                funding = float(history["funding"])
                fees = float(history["fees"])
                exit_price = history.get("exit_price")
                patched = patch_trade_exit_pnl(
                    symbol=sym,
                    signal_id=str(sid) if sid is not None else None,
                    pnl=trade_pnl,
                    fees=fees,
                    funding=funding,
                    pnl_source="position_history",
                    exit_price=exit_price,
                    position_id=(
                        str(history["position_id"])
                        if history.get("position_id") is not None
                        else None
                    ),
                    entry=entry_price,
                    qty=entry_qty,
                    path=trades_path,
                )
                if not patched:
                    logger.warning(
                        "dogon position_history %s matched but no exit ledger row; retain pending",
                        sym,
                    )
                    remaining.append(item)
                    continue
                msg = "уточнение: подтверждено BingX Position History. " + format_rocket_correction(
                    sym,
                    side,
                    net=float(history["net_pnl"]),
                    reason="выход",
                    funding=funding,
                    fees=fees,
                )
                updated_item = dict(item)
                if entry_price is not None:
                    updated_item["entry"] = entry_price
                if entry_qty is not None:
                    updated_item["qty"] = entry_qty
                if exit_price is not None:
                    updated_item["exit_price"] = float(exit_price)
                updated, delivered = await _deliver_pending_exit_followup(
                    updated_item,
                    pnl=trade_pnl,
                    message=msg,
                    send_tg=send_tg,
                    trades_path=trades_path,
                )
                if delivered:
                    sent += 1
                else:
                    remaining.append(updated)
                continue

        if agg.get("ambiguous_position_side"):
            logger.warning(
                "dogon: %s %s income rows cannot be mapped to the closed hedge leg; keep pending",
                sym,
                side,
            )
            updated_item = dict(item)
            if age >= DOGON_TTL_SEC:
                last_notice = float(updated_item.get("last_notice_ts") or 0.0)
                if now_ts - last_notice >= DOGON_TTL_SEC and send_tg is not None:
                    try:
                        age_hours = max(0, int(age // 3600))
                        await send_tg(
                            f"⚠️ {sym} {side.upper()}: биржевые строки дохода нельзя "
                            f"однозначно связать со сделкой спустя {age_hours} ч. "
                            "Запись сохранена для повторной сверки; сумму не угадываю."
                        )
                        updated_item["last_notice_ts"] = now_ts
                    except Exception as exc:
                        logger.warning("dogon ambiguous notice %s: %s", sym, type(exc).__name__)
            remaining.append(updated_item)
            continue

        if confirmed:
            msg = format_rocket_correction(
                sym,
                side,
                net=float(agg["net"]),
                reason=reason,
                funding=float(agg.get("funding") or 0),
                fees=float(agg.get("fees") or 0),
            )
            trade_pnl = float(agg.get("pnl") or 0) + float(agg.get("fees") or 0)
            try:
                patch_trade_exit_pnl(
                    symbol=sym,
                    signal_id=str(sid) if sid is not None else None,
                    pnl=trade_pnl,
                    fees=float(agg.get("fees") or 0),
                    funding=float(agg.get("funding") or 0),
                    pnl_source="income_confirmed",
                    entry=entry_price,
                    qty=entry_qty,
                    path=trades_path,
                )
            except Exception as exc:
                logger.warning("dogon patch %s: %s", sym, type(exc).__name__)
            updated, delivered = await _deliver_pending_exit_followup(
                item, pnl=trade_pnl, message=msg, send_tg=send_tg,
                trades_path=trades_path,
            )
            if delivered:
                sent += 1
            else:
                remaining.append(updated)
            continue

        # Never silently discard a closed trade whose exchange income is still
        # unavailable. Keep it durable and notify at most once per 24h after TTL.
        updated_item = dict(item)
        if age >= DOGON_TTL_SEC:
            last_notice = float(updated_item.get("last_notice_ts") or 0.0)
            if now_ts - last_notice >= DOGON_TTL_SEC and send_tg is not None:
                try:
                    age_hours = max(0, int(age // 3600))
                    await send_tg(
                        f"⚠️ {sym} {side.upper()}: финансовый итог закрытой сделки "
                        f"не подтверждён биржей спустя {age_hours} ч. "
                        "Сделка оставлена в очереди, проверка продолжится; "
                        "неподтверждённую сумму не подставляю."
                    )
                    updated_item["last_notice_ts"] = now_ts
                except Exception as exc:
                    logger.warning("dogon pending notice %s: %s", sym, type(exc).__name__)
        remaining.append(updated_item)

    try:
        save_pending(remaining, path)
    except Exception as exc:
        logger.warning("dogon save_pending: %s", type(exc).__name__)
    return sent


# --- F13: income fill-tape + strategy from signal_id ---

FILL_TAPE_STATE_PATH = Path("models/zeus_live_income_tape_seen.json")


def strategy_from_signal_id(signal_id: str | None) -> str | None:
    """Derive strategy name from signal_id prefix (before | or :)."""
    if not signal_id:
        return None
    s = str(signal_id).strip()
    if not s:
        return None
    for sep in ("|", ":", "/"):
        if sep in s:
            head = s.split(sep, 1)[0].strip()
            if head:
                return head
    if s.startswith("zeus") or "_" in s:
        return s
    return None


def _income_row_id(row: dict) -> str:
    for k in ("tranId", "tran_id", "id", "incomeId", "tradeId"):
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    it = str(row.get("_incomeType") or row.get("incomeType") or "")
    ts = str(row.get("time") or row.get("ts") or "")
    amt = str(row.get("income") or row.get("amount") or "")
    sym = str(row.get("symbol") or "")
    return f"{sym}|{it}|{ts}|{amt}"


def load_fill_tape_seen(path: Path = FILL_TAPE_STATE_PATH) -> set[str]:
    try:
        if not path.exists():
            return set()
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = data.get("ids") if isinstance(data, dict) else data
        if isinstance(ids, list):
            return set(str(x) for x in ids)
    except Exception:
        pass
    return set()


def save_fill_tape_seen(ids: set[str], path: Path = FILL_TAPE_STATE_PATH, keep: int = 5000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lst = list(ids)
    if len(lst) > keep:
        lst = lst[-keep:]
    path.write_text(
        json.dumps({"ids": lst, "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _parse_income_fill(row: dict, symbol: str) -> dict | None:
    it = str(row.get("_incomeType") or row.get("incomeType") or "")
    if it not in ("REALIZED_PNL",):
        return None
    try:
        amount = float(row.get("income") or row.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if abs(amount) < 1e-12:
        return None
    ts_raw = row.get("time") or row.get("ts")
    try:
        ts_ms = int(float(ts_raw))
        ts = ts_ms / 1000.0 if ts_ms > 1e12 else float(ts_ms)
    except (TypeError, ValueError):
        ts = time.time()
    qty = row.get("qty") or row.get("quantity")
    if qty is None and isinstance(row.get("info"), dict):
        qty = row["info"].get("qty")
    price = row.get("price") or row.get("markPrice")
    try:
        qty_f = float(qty) if qty is not None else None
    except (TypeError, ValueError):
        qty_f = None
    try:
        px_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        px_f = None
    return {
        "event": "partial_fill",
        "symbol": symbol,
        "side": _income_position_side(row),
        "position_side": _income_position_side(row),
        "qty": qty_f,
        "price": px_f,
        "pnl": amount,
        "income_type": it,
        "income_id": _income_row_id(row),
        "trade_id": _income_trade_id(row),
        "signal_id": str(row.get("signal_id") or row.get("signalId") or "").strip() or None,
        "source": "income_tape",
        "ts": ts,
    }


async def process_income_fill_tape(
    client,
    symbols: list[str],
    *,
    lookback_ms: int = 36 * 3600 * 1000,
    state_path: Path = FILL_TAPE_STATE_PATH,
    trades_path: Path | None = None,
) -> dict:
    """Each tick: poll REALIZED_PNL, append unseen rows (dedup by income id)."""
    from astra_bot.decision.live_broker import append_live_trade

    if not hasattr(client, "get_realized_income"):
        return {"written": 0, "seen": 0, "error": "no_get_realized_income"}
    seen = load_fill_tape_seen(state_path)
    written = 0
    scanned = 0
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(lookback_ms)
    for sym in symbols:
        if not sym:
            continue
        try:
            rows = await client.get_realized_income(
                sym,
                start_ms=start_ms,
                end_ms=now_ms,
                income_types=("REALIZED_PNL",),
            )
        except Exception as exc:
            logger.warning("fill_tape %s: %s", sym, type(exc).__name__)
            continue
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            scanned += 1
            rid = _income_row_id(row)
            if rid in seen:
                continue
            fill = _parse_income_fill(row, sym)
            if fill is None:
                continue
            seen.add(rid)
            try:
                append_live_trade(fill, path=trades_path)
                written += 1
            except Exception as exc:
                logger.warning("fill_tape append %s: %s", sym, type(exc).__name__)
                seen.discard(rid)
    try:
        save_fill_tape_seen(seen, state_path)
    except Exception as exc:
        logger.warning("fill_tape save seen: %s", type(exc).__name__)
    return {"written": written, "scanned": scanned, "seen_n": len(seen)}
