"""Backfill null-pnl live exits from BingX user/income (audit 438 / week-HALT).

Does NOT modify client_live.py. Uses client.get_realized_income already present.
Idempotent rewrite of models/zeus_live_trades.jsonl — never deletes rows.

Match rules (owner 15.10):
  exchange_flat: income REALIZED_PNL in [ts-30m, ts+30m]
  no_fills:      income REALIZED_PNL in [ts-24h, ts];
                 1 candidate → fill; many → qty match if possible; else unresolved
Rows that already have pnl are left untouched.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRADES_PATH = Path("models/zeus_live_trades.jsonl")
EXCHANGE_FLAT_WINDOW_SEC = 30 * 60
NO_FILLS_LOOKBACK_SEC = 24 * 3600


def _ts_sec(v: Any) -> float | None:
    try:
        t = float(v)
    except (TypeError, ValueError):
        return None
    if t > 1e12:
        t /= 1000.0
    return t


def _income_realized_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize get_realized_income rows → only REALIZED_PNL with ts/value."""
    out: list[dict[str, Any]] = []
    for r in rows:
        it = str(r.get("_incomeType") or r.get("incomeType") or "")
        if it != "REALIZED_PNL":
            continue
        ts = _ts_sec(r.get("time") or r.get("ts"))
        if ts is None:
            continue
        try:
            val = float(r.get("income") or 0)
        except (TypeError, ValueError):
            continue
        if val == 0.0:
            continue
        qty = None
        for k in ("qty", "quantity", "info"):
            raw = r.get(k)
            if raw is None:
                continue
            try:
                qty = abs(float(raw))
                break
            except (TypeError, ValueError):
                pass
        out.append({"ts": ts, "pnl": val, "qty": qty, "raw": r})
    return out


def _fee_sum(rows: list[dict[str, Any]], lo: float, hi: float) -> float:
    s = 0.0
    for r in rows:
        it = str(r.get("_incomeType") or r.get("incomeType") or "")
        if it != "TRADING_FEE":
            continue
        ts = _ts_sec(r.get("time") or r.get("ts"))
        if ts is None or ts < lo or ts > hi:
            continue
        try:
            s += abs(float(r.get("income") or 0))
        except (TypeError, ValueError):
            pass
    return s


def match_income_for_exit(
    exit_row: dict[str, Any],
    income_rows: list[dict[str, Any]],
) -> tuple[float | None, str]:
    """Return (pnl, status) where status is matched|unresolved|skip.

    pnl is REALIZED_PNL − TRADING_FEE in the chosen window (paper-style, no funding).
    """
    if exit_row.get("pnl") is not None:
        return None, "skip"
    reason = str(exit_row.get("exit_reason") or exit_row.get("reason") or "")
    ts = _ts_sec(exit_row.get("ts"))
    if ts is None:
        return None, "unresolved"

    events = _income_realized_events(income_rows)
    if reason == "exchange_flat":
        lo, hi = ts - EXCHANGE_FLAT_WINDOW_SEC, ts + EXCHANGE_FLAT_WINDOW_SEC
    elif reason == "no_fills":
        lo, hi = ts - NO_FILLS_LOOKBACK_SEC, ts
    else:
        lo, hi = ts - EXCHANGE_FLAT_WINDOW_SEC, ts + EXCHANGE_FLAT_WINDOW_SEC

    cands = [e for e in events if lo <= e["ts"] <= hi]
    if not cands:
        return None, "unresolved"

    chosen: dict[str, Any] | None = None
    if len(cands) == 1:
        chosen = cands[0]
    else:
        row_qty = None
        for k in ("qty", "quantity"):
            if exit_row.get(k) is not None:
                try:
                    row_qty = abs(float(exit_row[k]))
                    break
                except (TypeError, ValueError):
                    pass
        if row_qty is not None and row_qty > 0:
            qty_hits = [
                e
                for e in cands
                if e.get("qty") is not None
                and abs(float(e["qty"]) - row_qty) / row_qty < 0.02
            ]
            if len(qty_hits) == 1:
                chosen = qty_hits[0]
        if chosen is None:
            logger.info(
                "income_backfill unresolved %s %s reason=%s cands=%d",
                exit_row.get("symbol"),
                exit_row.get("side"),
                reason,
                len(cands),
            )
            return None, "unresolved"

    fee = _fee_sum(income_rows, chosen["ts"] - 300, chosen["ts"] + 300)
    pnl = float(chosen["pnl"]) - fee
    return pnl, "matched"


def apply_backfill_to_rows(
    rows: list[dict[str, Any]],
    income_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return (new_rows, n_updated). Never drops rows."""
    now = float(now if now is not None else time.time())
    out: list[dict[str, Any]] = []
    n_upd = 0
    for row in rows:
        if str(row.get("event") or "") != "exit":
            out.append(row)
            continue
        if row.get("pnl") is not None:
            out.append(row)
            continue
        sym = str(row.get("symbol") or "")
        income = income_by_symbol.get(sym) or []
        pnl, status = match_income_for_exit(row, income)
        if status != "matched" or pnl is None:
            out.append(row)
            continue
        patched = dict(row)
        patched["pnl"] = round(float(pnl), 6)
        patched["pnl_source"] = "income_backfill"
        patched["backfill_ts"] = now
        if not patched.get("exit_reason"):
            patched["exit_reason"] = "income_backfill"
        out.append(patched)
        n_upd += 1
        logger.info(
            "income_backfill set %s %s pnl=%.4f (was null, reason=%s)",
            sym,
            row.get("side"),
            pnl,
            row.get("exit_reason"),
        )
    return out, n_upd


def _est_from_entry_row(exit_row: dict[str, Any], all_rows: list[dict[str, Any]]) -> float | None:
    """Plan risk from matching entry (same signal_id) or exit fields."""
    try:
        entry = exit_row.get("entry")
        stop = exit_row.get("stop")
        qty = exit_row.get("qty")
        if entry is not None and stop is not None and qty is not None:
            return abs(float(stop) - float(entry)) * abs(float(qty))
    except (TypeError, ValueError):
        pass
    sid = str(exit_row.get("signal_id") or "")
    if not sid:
        return None
    for r in all_rows:
        if str(r.get("event") or "") != "entry":
            continue
        if str(r.get("signal_id") or "") != sid:
            continue
        try:
            entry = r.get("entry") or r.get("price")
            stop = r.get("stop")
            qty = r.get("qty")
            if entry is None or stop is None or qty is None:
                return None
            return abs(float(stop) - float(entry)) * abs(float(qty))
        except (TypeError, ValueError):
            return None
    return None


def correct_tiny_income_rows(
    rows: list[dict[str, Any]],
    income_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Re-match weak income/income_backfill rows (|pnl|<0.10·est, est>50).

    Sets pnl_source=income_corrected. Skips income_confirmed/income_corrected.
    Never deletes rows.
    """
    from astra_bot.decision.live_income_report import aggregate_income

    now_ts = float(now if now is not None else time.time())
    out: list[dict[str, Any]] = []
    n_upd = 0
    for row in rows:
        if str(row.get("event") or "") != "exit":
            out.append(row)
            continue
        src = str(row.get("pnl_source") or "")
        if src in ("income_confirmed", "income_corrected"):
            out.append(row)
            continue
        if src not in ("income", "income_backfill"):
            out.append(row)
            continue
        est = _est_from_entry_row(row, rows)
        if est is None or est <= 50:
            out.append(row)
            continue
        try:
            pnl_abs = abs(float(row.get("pnl")))
        except (TypeError, ValueError):
            out.append(row)
            continue
        if pnl_abs >= 0.10 * est:
            out.append(row)
            continue
        sym = str(row.get("symbol") or "")
        income_rows = income_by_symbol.get(sym) or []
        # 48h window around exit ts
        ts = _ts_sec(row.get("ts"))
        if ts is None:
            out.append(row)
            continue
        lo = ts - 48 * 3600
        hi = ts + 3600
        window = []
        for r in income_rows:
            t = _ts_sec(r.get("time") or r.get("ts"))
            if t is None or t < lo or t > hi:
                continue
            window.append(r)
        agg = aggregate_income(window)
        if not agg.get("has_realized"):
            out.append(row)
            continue
        rp = abs(float(agg.get("pnl") or 0))
        if rp < 0.5 * est:
            # still weak — leave as-is
            out.append(row)
            continue
        patched = dict(row)
        # trades.pnl = realized + fees (fees signed)
        patched["pnl"] = round(float(agg["pnl"]) + float(agg["fees"]), 4)
        patched["fees"] = float(agg.get("fees") or 0)
        patched["funding_fee"] = float(agg.get("funding") or 0)
        patched["pnl_source"] = "income_corrected"
        patched["corrected_ts"] = now_ts
        out.append(patched)
        n_upd += 1
        logger.info(
            "income_backfill correct %s pnl %.4f → %.4f (est=%.2f)",
            sym,
            pnl_abs,
            patched["pnl"],
            est,
        )
    return out, n_upd



def load_trades(path: Path = TRADES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def save_trades(rows: list[dict[str, Any]], path: Path = TRADES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "\n".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows
    )
    if body:
        body += "\n"
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


async def run_income_backfill(
    client: Any,
    *,
    path: Path = TRADES_PATH,
    now: float | None = None,
) -> dict[str, Any]:
    """Fetch income for null-pnl exits + correct tiny income rows. Fail-open."""
    rows = load_trades(path)
    null_exits = [
        r
        for r in rows
        if str(r.get("event") or "") == "exit" and r.get("pnl") is None
    ]
    weak_exits = [
        r
        for r in rows
        if str(r.get("event") or "") == "exit"
        and str(r.get("pnl_source") or "") in ("income", "income_backfill")
        and r.get("pnl") is not None
    ]
    symbols = sorted(
        {
            str(r.get("symbol") or "")
            for r in (null_exits + weak_exits)
            if r.get("symbol")
        }
    )
    if not symbols:
        return {"updated": 0, "nulls": 0, "corrected": 0, "symbols": []}

    end_ms = int((now if now is not None else time.time()) * 1000)
    start_ms = end_ms - 7 * 24 * 3600 * 1000
    income_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for sym in symbols:
        try:
            income_by_symbol[sym] = await client.get_realized_income(
                sym, start_ms=start_ms, end_ms=end_ms
            )
        except Exception as exc:
            logger.warning("income_backfill fetch %s: %s", sym, type(exc).__name__)
            income_by_symbol[sym] = []

    new_rows, n_upd = apply_backfill_to_rows(rows, income_by_symbol, now=now)
    new_rows, n_corr = correct_tiny_income_rows(new_rows, income_by_symbol, now=now)
    if n_upd or n_corr:
        save_trades(new_rows, path)
    return {
        "updated": n_upd,
        "corrected": n_corr,
        "nulls": len(null_exits),
        "symbols": symbols,
    }
