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


def format_funding_tg(symbol: str, side: str, amount: float) -> str | None:
    """💰 SYM SIDE: funding ±X.XX$ (role text). Dust |x|<0.10 → None."""
    if amount is None or abs(float(amount)) < FUNDING_DUST:
        return None
    base = symbol.replace("-USDT", "").replace("USDT", "")
    side_l = (side or "").lower()
    side_ru = "ЛОНГ" if side_l == "long" else "ШОРТ"
    amt_s = format_usd(float(amount))
    if amt_s is None:
        return None
    if side_l == "short":
        # positive funding for short = long pays short
        role = "лонг платит шорту" if amount > 0 else "мы платим лонгу"
    else:
        role = "мы платим шорту" if amount < 0 else "шорт платит лонгу"
    return f"💰 {base} {side_ru}: funding {amt_s} ({role})"


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
