#!/usr/bin/env python3
"""Audit large-wallet open/close direction using Ironflow's free keyless API.

Only aggregate rows are written. No wallet addresses or raw fills are exported.
The keyless API exposes the trailing 24h, 1h maximum per request, 1,000 rows,
and 60 query-seconds per hour.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.ironflow.sh/v1/query"
MARKETS = ("BTC-PERP", "ETH-PERP", "SOL-PERP", "HYPE-PERP", "INJ-PERP")


def post_query(sql: str, timeout: int = 8) -> dict:
    body = json.dumps({"sql": sql, "max_rows": 1000}).encode()
    req = Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ironflow HTTP {exc.code}: {detail[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Ironflow connection error: {exc}") from exc
    if data.get("code") not in (None, 0):
        raise RuntimeError(
            f"Ironflow query error {data.get('code')}: {data.get('msg', data)}"
        )
    if data.get("truncated"):
        raise RuntimeError("Ironflow returned a truncated aggregate result; refusing partial audit")
    return data


def parse_manifest(path: Path) -> list[str]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    addresses = sorted({
        str(row["address"]).lower()
        for row in manifest.get("wallet_summaries", [])
        if row.get("address")
    })
    if not addresses:
        raise ValueError("wallet_archive_manifest.json contains no cohort addresses")
    if any(len(address) != 42 or not address.startswith("0x") for address in addresses):
        raise ValueError("wallet manifest contains an invalid wallet address")
    return addresses


def audit(manifest_path: Path, out_dir: Path, lookback_hours: int = 24) -> dict:
    if not 1 <= lookback_hours <= 24:
        raise ValueError("lookback_hours must be between 1 and 24")
    addresses = parse_manifest(manifest_path)
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(hours=lookback_hours)
    address_sql = ", ".join(f"'{address}'" for address in addresses)
    all_rows: list[dict] = []
    query_seconds = 0.0
    requests = 0

    for market in MARKETS:
        cursor = start
        while cursor < end:
            nxt = min(cursor + timedelta(hours=6), end)
            sql = f"""
                SELECT
                    market,
                    toStartOfInterval(ts, INTERVAL 1 HOUR) AS hour_utc,
                    side,
                    direction,
                    is_taker,
                    count() AS fill_rows,
                    uniqExact(oid) AS order_ids,
                    uniqExact(address) AS wallets,
                    sum(notional) AS notional_usd,
                    sum(fee) AS fees_usd,
                    sum(realized_pnl) AS realized_pnl_usd,
                    countIf(is_liquidation) AS liquidation_fills,
                    sumIf(notional, is_liquidation) AS liquidation_notional_usd
                FROM hl.wallet_fills
                WHERE address IN ({address_sql})
                  AND market = '{market}'
                  AND ts >= toDateTime64('{cursor.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
                  AND ts < toDateTime64('{nxt.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
                GROUP BY market, hour_utc, side, direction, is_taker
                ORDER BY hour_utc, direction, side, is_taker
                LIMIT 1000
            """
            data = post_query(sql)
            requests += 1
            query_seconds += float(data.get("query_seconds") or 0.0)
            if query_seconds > 55:
                raise RuntimeError(
                    f"Stopped before keyless hourly query budget: {query_seconds:.2f}s"
                )
            columns = [col["name"] for col in data.get("columns", [])]
            for values in data.get("rows", []):
                row = dict(zip(columns, values, strict=True))
                row["market"] = market
                row["hour_utc"] = str(row["hour_utc"])
                all_rows.append(row)
            cursor = nxt
            time.sleep(1.05)

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ironflow_wallet_direction_hourly.csv"
    fields = [
        "market", "hour_utc", "side", "direction", "is_taker",
        "fill_rows", "order_ids", "wallets", "notional_usd", "fees_usd",
        "realized_pnl_usd", "liquidation_fills", "liquidation_notional_usd",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    aggregates: dict[str, dict] = {}
    for market in MARKETS:
        rows = [row for row in all_rows if row["market"] == market]
        by_direction: dict[str, dict] = {}
        for row in rows:
            key = f"{row['side']}|{row['direction']}|{'taker' if row['is_taker'] else 'maker'}"
            agg = by_direction.setdefault(key, {
                "fill_rows": 0, "order_ids": 0, "wallets_max_per_hour": 0,
                "notional_usd": 0.0, "fees_usd": 0.0,
                "realized_pnl_usd": 0.0, "liquidation_fills": 0,
                "liquidation_notional_usd": 0.0,
            })
            agg["fill_rows"] += int(row["fill_rows"])
            agg["order_ids"] += int(row["order_ids"])
            agg["wallets_max_per_hour"] = max(
                agg["wallets_max_per_hour"], int(row["wallets"])
            )
            for field in ("notional_usd", "fees_usd", "realized_pnl_usd",
                          "liquidation_notional_usd"):
                agg[field] += float(row[field] or 0.0)
            agg["liquidation_fills"] += int(row["liquidation_fills"])
        aggregates[market] = by_direction

    report = {
        "source": "Ironflow hl.wallet_fills via public keyless SQL API",
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "history_tier": "keyless, trailing 24h",
        "cohort_wallets": len(addresses),
        "markets": list(MARKETS),
        "requests": requests,
        "query_seconds_reported": round(query_seconds, 3),
        "aggregate_rows": len(all_rows),
        "directional_aggregates": aggregates,
        "interpretation": {
            "sell_open_short": "sell + Open Short",
            "sell_close_long": "sell + Close Long",
            "buy_open_long": "buy + Open Long",
            "buy_close_short": "buy + Close Short",
            "liquidations": "is_liquidation flag on wallet fills; report separately",
        },
        "limitations": [
            "This is a current 24h audit, not a historical strategy backtest.",
            "The wallet cohort was selected from the earlier archive snapshot; selection bias remains.",
            "Realized PnL is per-fill and does not include funding or unrealized PnL.",
            "No wallet addresses or raw fill rows are exported.",
            "A participant's side and direction describe its own position action, not a copy-trading instruction.",
        ],
        "output_csv": str(csv_path),
    }
    (out_dir / "ironflow_wallet_direction_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/ironflow_direction_audit"))
    parser.add_argument("--lookback-hours", type=int, default=24)
    args = parser.parse_args()
    report = audit(args.manifest, args.out_dir, args.lookback_hours)
    print(json.dumps({
        "cohort_wallets": report["cohort_wallets"],
        "requests": report["requests"],
        "query_seconds_reported": report["query_seconds_reported"],
        "aggregate_rows": report["aggregate_rows"],
    }))


if __name__ == "__main__":
    main()
