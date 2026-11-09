#!/usr/bin/env python3
"""Download timestamped public BingX USDT-M perpetual funding rates for research."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from astra_bot.adapters.bingx.client import (
    BINGX_API_BASE,
    BINGX_ENDPOINTS,
    BingXClient,
)

LOGGER = logging.getLogger("bingx_funding_archive")
OUT_DIR = Path("data/bingx_usdm")
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
CHUNK_DAYS = 90
FIELDS = ("funding_time", "rate")


def _rows_from_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or []
    if isinstance(data, dict):
        data = data.get("fundingRates", data.get("data", [])) or []
    if not isinstance(data, list):
        raise ValueError("BingX funding endpoint returned an unsupported data shape")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ts = item.get("fundingTime", item.get("time"))
        rate = item.get("fundingRate")
        if ts is None or rate is None:
            raise ValueError(f"funding record missing time/rate: {item!r}")
        rows.append({"funding_time": int(ts), "rate": str(rate)})
    return rows


async def download_symbol(
    client: BingXClient, symbol: str, start_ms: int, end_ms: int
) -> dict[str, Any]:
    rows: dict[int, str] = {}
    cursor = start_ms
    while cursor <= end_ms:
        chunk_end = min(
            end_ms,
            cursor + int(timedelta(days=CHUNK_DAYS).total_seconds() * 1000) - 1,
        )
        response = await client._request(
            "GET",
            BINGX_ENDPOINTS["swap"]["funding_rate"],
            params={
                "symbol": symbol,
                "startTime": cursor,
                "endTime": chunk_end,
                "limit": 1000,
            },
        )
        for row in _rows_from_response(response):
            ts = row["funding_time"]
            if cursor <= ts <= chunk_end:
                previous = rows.get(ts)
                if previous is not None and previous != row["rate"]:
                    raise ValueError(
                        f"conflicting funding rate for {symbol} at {ts}: "
                        f"{previous} != {row['rate']}"
                    )
                rows[ts] = row["rate"]
        cursor = chunk_end + 1

    ordered = sorted(rows.items())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"funding_{symbol.replace('-', '')}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(
            {"funding_time": ts, "rate": rate} for ts, rate in ordered
        )

    gaps = [
        (right_ts - left_ts) / 3_600_000
        for (left_ts, _), (right_ts, _) in pairwise(ordered)
    ]
    return {
        "symbol": symbol,
        "file": str(path),
        "events": len(ordered),
        "first_funding_time": ordered[0][0] if ordered else None,
        "last_funding_time": ordered[-1][0] if ordered else None,
        "max_gap_hours": max(gaps) if gaps else None,
        "source": "BingX /openApi/swap/v2/quote/fundingRate",
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    start = datetime.combine(date.fromisoformat(args.start_date), time.min, tzinfo=UTC)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    client = BingXClient(
        {
            "base_url": BINGX_API_BASE,
            "rate_limit_qps": 0.8,
            "contract_type": "linear",
        }
    )
    await client.initialize()
    try:
        reports = []
        for symbol in args.symbols:
            LOGGER.info("downloading funding history for %s", symbol)
            reports.append(await download_symbol(client, symbol, start_ms, end_ms))
    finally:
        await client.close()
    manifest = {
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "requested_start_utc": start.isoformat(),
        "downloaded_through_utc": datetime.fromtimestamp(
            end_ms / 1000, tz=UTC
        ).isoformat(),
        "reports": reports,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "funding_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2021-01-01")
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    args = parser.parse_args()
    args.symbols = tuple(
        item.strip().upper() for item in args.symbols.split(",") if item.strip()
    )
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
