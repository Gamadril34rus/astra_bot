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


async def exit_net_from_client(
    client: Any,
    symbol: str,
    since_ms: int | None,
) -> dict[str, Any]:
    """Fetch income via client.get_realized_income and aggregate."""
    if not hasattr(client, "get_realized_income"):
        return aggregate_income([])
    try:
        rows = await client.get_realized_income(symbol, start_ms=since_ms)
    except Exception as exc:
        logger.warning("exit_net_from_client %s: %s", symbol, type(exc).__name__)
        rows = []
    return aggregate_income(rows or [])


async def fetch_income_with_retries(
    client: Any,
    symbol: str,
    since_ms: int | None,
    *,
    attempts: int = 3,
    delay_sec: float = 7.0,
) -> dict[str, Any]:
    """Poll income up to attempts (~20s total). Return best aggregate."""
    import asyncio

    best = aggregate_income([])
    for i in range(max(1, attempts)):
        agg = await exit_net_from_client(client, symbol, since_ms)
        if agg.get("has_realized") or abs(float(agg.get("net") or 0)) > 1e-9:
            return agg
        best = agg
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
    if not agg.get("has_realized"):
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
    base = symbol.replace("-USDT", "").replace("USDT", "")
    side_l = (side or "").lower()
    side_ru = "ЛОНГ" if side_l == "long" else "ШОРТ"
    net_s = format_usd(-abs(float(est))) or "0.00$"
    return (
        f"🏁 {base} {side_ru}: закрыта {net_s} нетто "
        f"({reason}; оценка, сверь с UI)"
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
) -> bool:
    """Patch matching exit row. Never deletes. Skip income_confirmed/corrected."""
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.warning("patch_trade load: %s", type(exc).__name__)
        return False
    sid = str(signal_id or "")
    changed = False
    out: list[str] = []
    now = time.time()
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if str(row.get("event") or "") != "exit":
            out.append(line)
            continue
        if str(row.get("symbol") or "") != symbol:
            out.append(line)
            continue
        if sid and str(row.get("signal_id") or "") != sid:
            out.append(line)
            continue
        src = str(row.get("pnl_source") or "")
        if src in ("income_confirmed", "income_corrected"):
            out.append(line)
            continue
        # patch null / pending_dogon / weak income*
        if row.get("pnl") is not None and src not in (
            "pending_dogon",
            "income",
            "income_backfill",
        ):
            out.append(line)
            continue
        row["pnl"] = round(float(pnl), 4)
        row["fees"] = float(fees)
        row["funding_fee"] = float(funding)
        row["pnl_source"] = pnl_source
        row["corrected_ts"] = now
        out.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        changed = True
    if not changed:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(out)
        if body:
            body += "\n"
        path.write_text(body, encoding="utf-8")
    except Exception as exc:
        logger.warning("patch_trade save: %s", type(exc).__name__)
        return False
    return True


async def resolve_pending_dogon(
    client: Any,
    send_tg: Any = None,
    *,
    path: Path = PENDING_PATH,
    trades_path: Path = TRADES_PATH,
    now: float | None = None,
) -> int:
    """Confirm income (≥0.5·est) → correction TG + trades patch; 24h → estimate TG."""
    items = load_pending(path)
    if not items:
        return 0
    now_ts = float(now if now is not None else time.time())
    remaining: list[dict[str, Any]] = []
    sent = 0
    for item in items:
        sym = str(item.get("symbol") or "")
        side = str(item.get("side") or "long")
        sid = item.get("signal_id")
        since_ms = item.get("since_ms")
        reason = str(item.get("reason") or "стоп")
        try:
            est_f = float(item["est"]) if item.get("est") is not None else None
        except (TypeError, ValueError):
            est_f = None
        enqueued_ts = float(item.get("ts") or now_ts)
        age = now_ts - enqueued_ts
        if not sym:
            continue
        try:
            agg = await exit_net_from_client(client, sym, since_ms)
        except Exception as exc:
            logger.warning("dogon exit_net %s: %s", sym, type(exc).__name__)
            remaining.append(item)
            continue

        rp = abs(float(agg.get("pnl") or 0))
        has = bool(agg.get("has_realized"))
        confirmed = has and (est_f is None or est_f <= 0 or rp >= DOGON_CONFIRM_FRAC * est_f)

        if confirmed:
            msg = format_rocket_correction(
                sym,
                side,
                net=float(agg["net"]),
                reason=reason,
                funding=float(agg.get("funding") or 0),
                fees=float(agg.get("fees") or 0),
            )
            if send_tg is not None:
                try:
                    await send_tg(msg)
                    sent += 1
                except Exception as exc:
                    logger.warning("dogon TG %s: %s", sym, type(exc).__name__)
                    remaining.append(item)
                    continue
            trade_pnl = float(agg.get("pnl") or 0) + float(agg.get("fees") or 0)
            try:
                patch_trade_exit_pnl(
                    symbol=sym,
                    signal_id=str(sid) if sid is not None else None,
                    pnl=trade_pnl,
                    fees=float(agg.get("fees") or 0),
                    funding=float(agg.get("funding") or 0),
                    pnl_source="income_confirmed",
                    path=trades_path,
                )
            except Exception as exc:
                logger.warning("dogon patch %s: %s", sym, type(exc).__name__)
            continue

        if age >= DOGON_TTL_SEC:
            if est_f is not None and est_f > 0 and send_tg is not None:
                try:
                    await send_tg(
                        format_estimate_rocket(sym, side, est=est_f, reason=reason)
                    )
                    sent += 1
                except Exception as exc:
                    logger.warning("dogon estimate TG %s: %s", sym, type(exc).__name__)
                    remaining.append(item)
                    continue
            continue

        remaining.append(item)

    try:
        save_pending(remaining, path)
    except Exception as exc:
        logger.warning("dogon save_pending: %s", type(exc).__name__)
    return sent
