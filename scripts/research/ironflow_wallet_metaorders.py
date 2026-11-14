#!/usr/bin/env python3
"""Order-ID-level taker metaorder reconstruction from Ironflow's SQL API.

Requires no third-party Python packages. Without IRONFLOW_API_KEY, the API
allows only the latest 24h and at most 1h per query. Ironflow's current pricing
page lists 14 days for a free key, while its SQL endpoint reference says 30 days;
the script uses a conservative 14-day guard unless explicitly overridden. It groups fill rows by oid before it
reconstructs same-side, same-direction parent sequences.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.ironflow.sh/v1/query"
ZERO_HASHES = {"0x" + "0" * 64, "0x" + "0" * 40}


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def sql_ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def post_query(sql: str, api_key: str | None, timeout: int = 25) -> dict:
    body = json.dumps({"sql": sql, "max_rows": 1000}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = Request(API_URL, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("code") not in (None, 0):
                raise RuntimeError(f"Ironflow query error {data.get('code')}: {data.get('msg', data)}")
            return data
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ironflow HTTP {exc.code}: {detail[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Ironflow connection error: {exc}") from exc


def query_orders(address: str, market: str, start: datetime, end: datetime,
                 api_key: str | None, delay: float, min_chunk_seconds: int = 60) -> list[dict]:
    max_chunk = timedelta(hours=6 if api_key else 1)
    collected: list[dict] = []

    def fetch_window(a: datetime, b: datetime) -> None:
        sql = f"""
        SELECT oid, min(ts) AS ts, any(side) AS side, any(direction) AS direction,
               sum(notional) AS q_notional, sum(size) AS size,
               sum(price*size)/nullIf(sum(size),0) AS vwap,
               count() AS fill_count, sum(fee) AS fees,
               sum(realized_pnl) AS realized_pnl, any(hash) AS tx_hash
        FROM hl.wallet_fills
        WHERE address = '{address}' AND market = '{market}'
          AND ts >= toDateTime64('{sql_ts(a)}',6,'UTC')
          AND ts < toDateTime64('{sql_ts(b)}',6,'UTC')
          AND is_taker = true
        GROUP BY oid
        ORDER BY min(ts) ASC
        LIMIT 1000
        """
        if delay:
            time.sleep(delay)
        data = post_query(sql, api_key)
        actual_history = data.get("limits", {}).get("history_days")
        if actual_history is not None and a < datetime.now(UTC) - timedelta(days=int(actual_history)):
            raise RuntimeError(
                f"API reports only {actual_history} days of history; refusing a potentially truncated result."
            )
        rows = data.get("rows", [])
        truncated = bool(data.get("truncated")) or len(rows) >= 1000
        if truncated:
            span = (b - a).total_seconds()
            if span <= min_chunk_seconds:
                raise RuntimeError(
                    f"More than 1000 order IDs in a {span:.0f}s window; narrow the time range."
                )
            mid = a + (b - a) / 2
            fetch_window(a, mid)
            fetch_window(mid, b)
            return
        cols = [c["name"] for c in data.get("columns", [])]
        for row in rows:
            collected.append(dict(zip(cols, row, strict=True)))
        notice = data.get("notice")
        if notice:
            print(f"API notice: {notice}")

    cursor = start
    while cursor < end:
        nxt = min(cursor + max_chunk, end)
        fetch_window(cursor, nxt)
        cursor = nxt
    # An order can straddle adjacent query windows. Merge by oid before counting
    # child orders so boundary fills cannot create duplicate child orders.
    merged: dict[str, dict] = {}
    for row in collected:
        oid = str(row.get("oid"))
        if oid not in merged:
            merged[oid] = dict(row)
            continue
        old = merged[oid]
        old_size = float(old.get("size") or 0)
        new_size = float(row.get("size") or 0)
        total_size = old_size + new_size
        if total_size > 0:
            old["vwap"] = (
                float(old.get("vwap") or 0) * old_size
                + float(row.get("vwap") or 0) * new_size
            ) / total_size
        old["size"] = total_size
        old["q_notional"] = float(old.get("q_notional") or 0) + float(row.get("q_notional") or 0)
        old["fill_count"] = int(old.get("fill_count") or 0) + int(row.get("fill_count") or 0)
        old["fees"] = float(old.get("fees") or 0) + float(row.get("fees") or 0)
        old["realized_pnl"] = float(old.get("realized_pnl") or 0) + float(row.get("realized_pnl") or 0)
        if parse_dt(str(row["ts"])) < parse_dt(str(old["ts"])):
            old["ts"] = row["ts"]
        if not old.get("tx_hash") and row.get("tx_hash"):
            old["tx_hash"] = row["tx_hash"]
    return list(merged.values())


def build_metaorders(rows: list[dict], gap_minutes: int, min_orders: int,
                     max_duration_hours: float) -> tuple[list[dict], int]:
    orders = []
    twap_candidates = 0
    for row in rows:
        tx_hash = str(row.get("tx_hash") or "").lower()
        if tx_hash in ZERO_HASHES:
            twap_candidates += 1
            continue
        ts = parse_dt(str(row["ts"]))
        orders.append({
            "oid": int(row["oid"]), "ts": ts,
            "side": str(row.get("side") or "unknown"),
            "direction": str(row.get("direction") or "unknown"),
            "q": float(row.get("q_notional") or 0),
            "size": float(row.get("size") or 0),
            "vwap": float(row.get("vwap") or 0),
            "fills": int(row.get("fill_count") or 0),
            "fees": float(row.get("fees") or 0),
            "realized_pnl": float(row.get("realized_pnl") or 0),
        })
    orders.sort(key=lambda x: (x["side"], x["direction"], x["ts"], x["oid"]))
    partitions: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for order in orders:
        partitions[(order["side"], order["direction"])].append(order)

    parents = []
    max_gap = timedelta(minutes=gap_minutes)
    max_duration = timedelta(hours=max_duration_hours)
    for (side, direction), items in partitions.items():
        group: list[dict] = []
        for order in items:
            if group and order["ts"] - group[-1]["ts"] > max_gap:
                parents.extend(finalize(group, side, direction, min_orders, max_duration))
                group = []
            group.append(order)
        if group:
            parents.extend(finalize(group, side, direction, min_orders, max_duration))
    parents.sort(key=lambda x: x["q_notional_usd"], reverse=True)
    return parents, twap_candidates


def finalize(group: list[dict], side: str, direction: str, min_orders: int,
             max_duration: timedelta) -> list[dict]:
    start, end = group[0]["ts"], group[-1]["ts"]
    duration = end - start
    if len(group) < min_orders or duration > max_duration:
        return []
    q = sum(x["q"] for x in group)
    size = sum(x["size"] for x in group)
    if q <= 0 or size <= 0:
        return []
    fees = sum(x["fees"] for x in group)
    realized = sum(x["realized_pnl"] for x in group)
    span = duration.total_seconds()

    def share(predicate) -> float | None:
        if span <= 0:
            return None
        return sum(x["q"] for x in group if predicate(x["ts"].timestamp())) / q

    start_s = start.timestamp()
    return [{
        "side": side, "direction": direction,
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "duration_minutes": round(span / 60, 3),
        "child_orders": len(group), "fill_rows": sum(x["fills"] for x in group),
        "q_notional_usd": round(q, 4),
        "vwap": round(sum(x["vwap"] * x["size"] for x in group) / size, 8),
        "fees_usd": round(fees, 6), "realized_pnl_usd": round(realized, 6),
        "realized_less_fees_usd": round(realized - fees, 6),
        "first20_share": share(lambda t: t <= start_s + 0.2 * span),
        "middle60_share": share(lambda t: start_s + 0.2 * span < t < start_s + 0.8 * span),
        "last20_share": share(lambda t: t >= start_s + 0.8 * span),
        "first_oid": group[0]["oid"], "last_oid": group[-1]["oid"],
    }]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--address", required=True)
    ap.add_argument("--market", required=True)
    ap.add_argument("--start", required=True, help="ISO-8601 UTC, e.g. 2026-11-11T00:00:00Z")
    ap.add_argument("--end", required=True, help="ISO-8601 UTC")
    ap.add_argument("--gap-minutes", type=int, default=30)
    ap.add_argument("--min-orders", type=int, default=10)
    ap.add_argument("--max-duration-hours", type=float, default=24)
    ap.add_argument("--out", default="results/wallet_metaorders.json")
    ap.add_argument("--request-delay", type=float, default=None)
    ap.add_argument("--history-days", type=int, default=None, help="override the API tier history guard after verifying the account's actual limit")
    args = ap.parse_args()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", args.address):
        ap.error("address must be a 20-byte 0x-prefixed address")
    if not re.fullmatch(r"[A-Za-z0-9:_-]+", args.market):
        ap.error("invalid market symbol")
    if args.gap_minutes < 1 or args.min_orders < 2 or args.max_duration_hours <= 0:
        ap.error("gap, minimum orders and duration must be positive")
    start, end = parse_dt(args.start), parse_dt(args.end)
    if end <= start:
        ap.error("--end must be after --start")
    api_key = os.getenv("IRONFLOW_API_KEY")
    now = datetime.now(UTC)
    history_days = args.history_days if args.history_days is not None else (14 if api_key else 1)
    if history_days < 1:
        ap.error("--history-days must be at least 1")
    history = timedelta(days=history_days)
    if start < now - history:
        ap.error(f"requested start is outside the configured history guard ({history_days}d); keyless=1d, current public pricing lists free key=14d")
    if end > now + timedelta(minutes=5):
        ap.error("--end cannot be in the future")
    delay = args.request_delay if args.request_delay is not None else (1.1 if api_key else 2.1)
    rows = query_orders(args.address, args.market, start, end, api_key, delay)
    parents, twap_candidates = build_metaorders(rows, args.gap_minutes, args.min_orders, args.max_duration_hours)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "source": "Ironflow hl.wallet_fills",
        "address": args.address, "market": args.market,
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "gap_minutes": args.gap_minutes, "min_orders": args.min_orders,
        "max_duration_hours": args.max_duration_hours,
        "api_tier": "free-key" if api_key else "keyless-24h",
        "history_guard_days": history_days,
        "order_ids_retrieved": len(rows),
        "zero_hash_twap_candidates_excluded": twap_candidates,
        "metaorders": parents,
        "limitations": [
            "Wallet fills are queried for one address and one market at a time.",
            "Zero-hash fills are counted as TWAP candidates but cannot be grouped by twapId from this view.",
            "Funding and unrealized PnL are not included in per-parent realized_less_fees_usd.",
        ],
    }
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Retrieved {len(rows)} order IDs; {len(parents)} statistical metaorders; {twap_candidates} zero-hash TWAP candidates.")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
