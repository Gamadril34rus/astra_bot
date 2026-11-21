#!/usr/bin/env python3
"""Download public Hyperliquid 15m candles for the CLT source-price event study."""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.hyperliquid.xyz/info"
COINS = ("BTC", "ETH", "SOL", "HYPE", "INJ")
INTERVAL = "15m"
BAR_MS = 15 * 60 * 1000
FIELDS = ("coin", "timestamp_ms", "open", "high", "low", "close", "volume", "trades")


def post_info(payload: dict[str, Any], *, timeout: int = 30) -> list[dict[str, Any]]:
    request = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    for attempt in range(4):
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not isinstance(result, list):
                raise RuntimeError(f"Unexpected Hyperliquid candle response: {str(result)[:500]}")
            return result
        except HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Hyperliquid candle API HTTP {exc.code}: {detail[:500]}") from exc
            time.sleep((5, 15, 30)[attempt])
        except URLError as exc:
            if attempt == 3:
                raise RuntimeError(f"Hyperliquid candle API connection error: {exc}") from exc
            time.sleep((2, 5, 10)[attempt])
    raise RuntimeError("Hyperliquid candle API retry loop exhausted")


def fetch_candles(coin: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    rows = post_info({
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": INTERVAL,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    })
    cleaned = []
    seen = set()
    for row in rows:
        timestamp = int(row["t"])
        if timestamp < start_ms or timestamp >= end_ms or timestamp in seen:
            continue
        values = {
            "open": float(row["o"]),
            "high": float(row["h"]),
            "low": float(row["l"]),
            "close": float(row["c"]),
            "volume": float(row["v"]),
        }
        if not all(value > 0 for key, value in values.items() if key != "volume"):
            raise ValueError(f"Non-positive OHLC value for {coin} at {timestamp}")
        if values["volume"] < 0 or values["high"] < max(values["open"], values["close"], values["low"]):
            raise ValueError(f"Invalid candle OHLCV for {coin} at {timestamp}")
        if values["low"] > min(values["open"], values["close"]):
            raise ValueError(f"Invalid candle low for {coin} at {timestamp}")
        seen.add(timestamp)
        cleaned.append({
            "coin": coin,
            "timestamp_ms": timestamp,
            **values,
            "trades": int(row.get("n") or 0),
        })
    cleaned.sort(key=lambda row: row["timestamp_ms"])
    return cleaned


def download(start: datetime, end: datetime, out: Path) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("start/end must be timezone-aware and start < end")
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    all_rows = []
    coverage = {}
    for coin in COINS:
        rows = fetch_candles(coin, start_ms, end_ms)
        all_rows.extend(rows)
        expected = (end_ms - start_ms + BAR_MS - 1) // BAR_MS
        timestamps = {row["timestamp_ms"] for row in rows}
        coverage[coin] = {
            "rows": len(rows),
            "expected_rows": expected,
            "missing_bars": max(0, expected - len(timestamps)),
            "first_utc": datetime.fromtimestamp(min(timestamps) / 1000, UTC).isoformat() if timestamps else None,
            "last_utc": datetime.fromtimestamp(max(timestamps) / 1000, UTC).isoformat() if timestamps else None,
        }
        time.sleep(0.25)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "hyperliquid_15m.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(all_rows, key=lambda row: (row["coin"], row["timestamp_ms"])))
    report = {
        "venue": "Hyperliquid",
        "interval": INTERVAL,
        "start_utc": start.astimezone(UTC).isoformat(),
        "end_utc_exclusive": end.astimezone(UTC).isoformat(),
        "rows": len(all_rows),
        "coverage_by_coin": coverage,
    }
    (out / "hyperliquid_15m_coverage.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="ISO-8601 start, timezone required")
    parser.add_argument("--end", required=True, help="ISO-8601 exclusive end, timezone required")
    parser.add_argument("--out-dir", type=Path, default=Path("reports/clt_hyperliquid_candles"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    end = datetime.fromisoformat(args.end.replace("Z", "+00:00"))
    print(json.dumps(download(start, end, args.out_dir), indent=2))


if __name__ == "__main__":
    main()
