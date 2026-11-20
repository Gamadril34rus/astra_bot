#!/usr/bin/env python3
"""Audit current large-wallet position actions through Hyperliquid's public Info API.

Uses a bounded subset of the frozen wallet cohort, writes aggregate-only output,
and never stores or exports wallet addresses or raw fills.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.hyperliquid.xyz/info"
CORE_COINS = {"BTC", "ETH", "SOL", "HYPE", "INJ"}
PAGE_SIZE = 500
MAX_FILLS_PER_ADDRESS = 10_000


def post_info(payload: dict, timeout: int = 12) -> list[dict]:
    request = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Hyperliquid Info API HTTP {exc.code}: {detail[:1000]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Hyperliquid Info API connection error: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected userFillsByTime response: {str(data)[:1000]}")
    return data


def select_wallets(
    manifest_path: Path,
    *,
    max_wallets: int,
    max_fills_per_day: float,
) -> list[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    eligible = []
    for row in manifest.get("wallet_summaries", []):
        address = str(row.get("address", "")).lower()
        if len(address) != 42 or not address.startswith("0x"):
            continue
        native_fills = int(row.get("native_perp_fills") or 0)
        fills_per_day = float(row.get("fills_per_active_day") or 0)
        markets = {
            item.get("market")
            for item in row.get("top_markets_by_notional", [])
            if isinstance(item, dict)
        }
        if (
            native_fills >= 1000
            and 0 < fills_per_day <= max_fills_per_day
            and markets.intersection({f"{coin}-PERP" for coin in CORE_COINS})
        ):
            eligible.append((float(row.get("month_volume") or 0), address))
    eligible.sort(reverse=True)
    return [address for _, address in eligible[:max_wallets]]


def _fill_key(fill: dict) -> tuple:
    if fill.get("tid") is not None:
        return ("tid", fill["tid"])
    return (
        fill.get("time"), fill.get("oid"), fill.get("coin"),
        fill.get("side"), fill.get("px"), fill.get("sz"), fill.get("hash"),
    )


def fetch_wallet_fills(
    address: str,
    *,
    start_ms: int,
    end_ms: int,
    max_fills: int = MAX_FILLS_PER_ADDRESS,
) -> tuple[list[dict], dict]:
    cursor = start_ms
    seen: set[tuple] = set()
    fills: list[dict] = []
    pages = 0
    capped = False
    pagination_stalled = False
    while pages < math.ceil(max_fills / PAGE_SIZE):
        page = post_info({
            "type": "userFillsByTime",
            "user": address,
            "startTime": cursor,
            "endTime": end_ms,
            "aggregateByTime": False,
        })
        pages += 1
        new_rows = 0
        for fill in page:
            key = _fill_key(fill)
            if key not in seen:
                seen.add(key)
                fills.append(fill)
                new_rows += 1
        # Hyperliquid's per-IP limit is 1,200 weight/min. userFillsByTime
        # costs 20 plus one extra weight per 20 returned fills.
        weight = 20 + math.ceil(len(page) / 20)
        time.sleep(weight / 20.0 + 0.05)
        if len(page) < PAGE_SIZE:
            break
        last_time = max(int(fill["time"]) for fill in page)
        if last_time < cursor or (last_time == cursor and new_rows == 0):
            pagination_stalled = True
            break
        cursor = last_time + 1
        if len(fills) >= max_fills:
            capped = True
            break
    if len(fills) >= max_fills:
        capped = True
    return fills, {
        "pages": pages,
        "fills_returned": len(fills),
        "history_cap_reached": capped,
        "pagination_stalled": pagination_stalled,
    }


def audit(
    manifest_path: Path,
    out_dir: Path,
    *,
    lookback_hours: int = 24,
    max_wallets: int = 20,
    max_fills_per_day: float = 5000.0,
) -> dict:
    if not 1 <= lookback_hours <= 24:
        raise ValueError("lookback_hours must be between 1 and 24")
    if not 1 <= max_wallets <= 40:
        raise ValueError("max_wallets must be between 1 and 40")

    addresses = select_wallets(
        manifest_path,
        max_wallets=max_wallets,
        max_fills_per_day=max_fills_per_day,
    )
    if not addresses:
        raise ValueError("no eligible core-market wallets in the archive manifest")

    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(hours=lookback_hours)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    grouped: dict[tuple, dict] = {}
    wallet_stats = []
    total_pages = 0
    total_fills = 0

    for address in addresses:
        fills, stats = fetch_wallet_fills(
            address, start_ms=start_ms, end_ms=end_ms
        )
        total_pages += stats["pages"]
        total_fills += stats["fills_returned"]
        wallet_stats.append(stats)
        for fill in fills:
            coin = str(fill.get("coin", ""))
            if coin not in CORE_COINS:
                continue
            side_code = str(fill.get("side", ""))
            side = "buy" if side_code == "B" else "sell" if side_code == "A" else "unknown"
            direction = str(fill.get("dir") or "unknown")
            crossed = bool(fill.get("crossed", False))
            key = (f"{coin}-PERP", side, direction, crossed)
            row = grouped.setdefault(key, {
                "market": f"{coin}-PERP",
                "side": side,
                "direction": direction,
                "role": "taker" if crossed else "maker",
                "fill_rows": 0,
                "order_ids": set(),
                "wallets": set(),
                "notional_usd": 0.0,
                "fees_usd": 0.0,
                "realized_pnl_usd": 0.0,
                "liquidation_fills": 0,
                "liquidation_notional_usd": 0.0,
            })
            px = float(fill.get("px") or 0.0)
            size = float(fill.get("sz") or 0.0)
            notional = abs(px * size)
            row["fill_rows"] += 1
            if fill.get("oid") is not None:
                row["order_ids"].add(str(fill["oid"]))
            row["wallets"].add(address)
            row["notional_usd"] += notional
            row["fees_usd"] += float(fill.get("fee") or 0.0)
            row["realized_pnl_usd"] += float(fill.get("closedPnl") or 0.0)
            liquidation = fill.get("liquidation")
            if liquidation:
                row["liquidation_fills"] += 1
                row["liquidation_notional_usd"] += notional

    out_dir.mkdir(parents=True, exist_ok=True)
    output_rows = []
    for row in grouped.values():
        output_rows.append({
            **{key: value for key, value in row.items()
               if key not in {"order_ids", "wallets"}},
            "order_ids": len(row["order_ids"]),
            "wallets": len(row["wallets"]),
        })
    output_rows.sort(key=lambda row: (row["market"], row["direction"], row["side"], row["role"]))
    csv_path = out_dir / "hyperliquid_wallet_direction_audit.csv"
    fields = [
        "market", "side", "direction", "role", "fill_rows", "order_ids",
        "wallets", "notional_usd", "fees_usd", "realized_pnl_usd",
        "liquidation_fills", "liquidation_notional_usd",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    market_summary = {}
    for coin in sorted(CORE_COINS):
        market = f"{coin}-PERP"
        rows = [row for row in output_rows if row["market"] == market]
        def total(side: str, direction: str, rows=rows) -> float:
            return sum(
                row["notional_usd"] for row in rows
                if row["side"] == side and row["direction"] == direction
            )
        sell_open_short = total("sell", "Open Short")
        sell_close_long = total("sell", "Close Long")
        buy_open_long = total("buy", "Open Long")
        buy_close_short = total("buy", "Close Short")
        sell_total = sell_open_short + sell_close_long
        buy_total = buy_open_long + buy_close_short
        market_summary[market] = {
            "sell_open_short_notional_usd": sell_open_short,
            "sell_close_long_notional_usd": sell_close_long,
            "sell_open_short_share_pct": (
                sell_open_short / sell_total * 100.0 if sell_total else None
            ),
            "buy_open_long_notional_usd": buy_open_long,
            "buy_close_short_notional_usd": buy_close_short,
            "buy_open_long_share_pct": (
                buy_open_long / buy_total * 100.0 if buy_total else None
            ),
            "total_fill_rows": sum(row["fill_rows"] for row in rows),
            "total_notional_usd": sum(row["notional_usd"] for row in rows),
            "liquidation_fills": sum(row["liquidation_fills"] for row in rows),
            "liquidation_notional_usd": sum(
                row["liquidation_notional_usd"] for row in rows
            ),
        }

    report = {
        "source": "Hyperliquid public Info API userFillsByTime",
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "history_window_hours": lookback_hours,
        "cohort_selection": {
            "source": "frozen 37-day wallet archive manifest",
            "eligible_wallets": len(select_wallets(
                manifest_path,
                max_wallets=10_000,
                max_fills_per_day=max_fills_per_day,
            )),
            "selected_wallets": len(addresses),
            "rules": [
                "native_perp_fills >= 1,000 in archive snapshot",
                f"fills_per_active_day <= {max_fills_per_day}",
                "one of BTC/ETH/SOL/HYPE/INJ appears in top markets",
                "rank by archive month_volume, take highest selected count",
            ],
        },
        "counts": {
            "wallets_queried": len(addresses),
            "wallets_with_fills": sum(1 for stat in wallet_stats if stat["fills_returned"]),
            "pages": total_pages,
            "fill_rows_returned_all_markets": total_fills,
            "aggregate_rows_core_markets": len(output_rows),
            "wallets_at_10000_fill_cap": sum(
                1 for stat in wallet_stats if stat["history_cap_reached"]
            ),
            "wallets_with_pagination_stall": sum(
                1 for stat in wallet_stats if stat["pagination_stalled"]
            ),
        },
        "per_market": market_summary,
        "limitations": [
            "This is a current-window interpretation audit, not historical validation of the 37-day signal.",
            "Only the selected 20-wallet subset is queried; it is not the full 100-wallet cohort.",
            "Hyperliquid Info API exposes at most 10,000 recent fills per address.",
            "userFillsByTime excludes TWAP slice fills; TWAP coverage is incomplete.",
            "Liquidation is counted only when the optional liquidation field is present in the response.",
            "No wallet addresses or raw fills are exported.",
        ],
        "output_csv": str(csv_path),
    }
    (out_dir / "hyperliquid_wallet_direction_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/hyperliquid_direction_audit"))
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--max-wallets", type=int, default=20)
    parser.add_argument("--max-fills-per-day", type=float, default=5000.0)
    args = parser.parse_args()
    report = audit(
        args.manifest,
        args.out_dir,
        lookback_hours=args.lookback_hours,
        max_wallets=args.max_wallets,
        max_fills_per_day=args.max_fills_per_day,
    )
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
