#!/usr/bin/env python3
"""Download public BingX USDT-M 15m/1h candles and funding for CLT research."""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://open-api.bingx.com"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
INTERVAL_MS = {"15m": 15 * 60_000, "1h": 60 * 60_000}
PAGE_LIMIT = 1_000


def public_get(path: str, params: dict[str, Any], *, timeout: int = 20) -> Any:
    query = urlencode(params)
    request = Request(
        f"{BASE_URL}{path}?{query}",
        headers={"Accept": "application/json", "User-Agent": "ASTRA-CLT-research/0.1"},
    )
    for attempt in range(4):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("code") != 0:
                raise RuntimeError(f"BingX public API returned an invalid response: {payload!r}")
            return payload.get("data")
        except HTTPError as exc:
            if (exc.code != 429 and exc.code < 500) or attempt == 3:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"BingX HTTP {exc.code}: {detail[:500]}") from exc
            time.sleep((1, 2, 4)[attempt])
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == 3:
                raise RuntimeError(f"BingX public request failed: {exc}") from exc
            time.sleep((1, 2, 4)[attempt])
    raise RuntimeError("BingX retry loop exhausted")


def fetch_klines(
    fetch: Callable[[str, dict[str, Any]], Any],
    *,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = PAGE_LIMIT,
    pause_seconds: float = 0.0,
) -> list[dict[str, float | int]]:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    if start_ms < 0 or end_ms <= start_ms or not 1 <= limit <= 1_440:
        raise ValueError("invalid kline range or limit")
    interval_ms = INTERVAL_MS[interval]
    rows: dict[int, dict[str, float | int]] = {}
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(end_ms, cursor + limit * interval_ms)
        data = fetch("/openApi/swap/v3/quote/klines", {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": chunk_end - 1,
            "limit": limit,
        })
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected kline response for {symbol} {interval}")
        for raw in data:
            if not isinstance(raw, list) or len(raw) < 11:
                raise RuntimeError(f"malformed kline row for {symbol} {interval}")
            ts = int(raw[0])
            if not start_ms <= ts < end_ms:
                continue
            values = [float(raw[index]) for index in (1, 2, 3, 4, 5, 7, 8, 9, 10)]
            if any(not math.isfinite(value) for value in values):
                raise RuntimeError("non-finite BingX kline value")
            open_price, high, low, close, volume, quote_volume, trade_count, taker_base, taker_quote = values
            if (
                min(open_price, high, low, close) <= 0
                or high < low
                or not low <= open_price <= high
                or not low <= close <= high
            ):
                raise RuntimeError("invalid BingX OHLC")
            rows[ts] = {
                "open_time": ts,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "close_time": int(raw[6]),
                "quote_volume": quote_volume,
                "trade_count": trade_count,
                "taker_buy_base": taker_base,
                "taker_buy_quote": taker_quote,
            }
        cursor = chunk_end
        if cursor < end_ms:
            time.sleep(pause_seconds)
    ordered = [rows[key] for key in sorted(rows)]
    previous = None
    for row in ordered:
        ts = int(row["open_time"])
        if previous is not None and ts <= previous:
            raise RuntimeError("duplicate or unordered BingX candles")
        previous = ts
    return ordered


def fetch_funding(
    fetch: Callable[[str, dict[str, Any]], Any],
    *,
    symbol: str,
    start_ms: int,
    end_ms: int,
    pause_seconds: float = 1.05,
) -> list[dict[str, float | int | str]]:
    data = fetch("/openApi/swap/v2/quote/fundingRate", {
        "symbol": symbol,
        "startTime": start_ms,
        "endTime": end_ms - 1,
        "limit": 1_000,
    })
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected funding response for {symbol}")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            raise RuntimeError("malformed funding row")
        timestamp = int(item.get("fundingTime") or item.get("time") or 0)
        rate = float(item["fundingRate"])
        if not math.isfinite(rate):
            raise RuntimeError("non-finite funding rate")
        if start_ms <= timestamp < end_ms:
            rows.append({
                "symbol": symbol,
                "funding_time": timestamp,
                "funding_rate": rate,
            })
    rows.sort(key=lambda row: int(row["funding_time"]))
    if pause_seconds:
        time.sleep(pause_seconds)
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class RateLimitedFetcher:
    """Serialize public market-data requests to at most one per second."""

    def __init__(self, minimum_interval_seconds: float = 1.05):
        self.minimum_interval_seconds = minimum_interval_seconds
        self.last_call_monotonic: float | None = None

    def __call__(self, path: str, params: dict[str, Any]) -> Any:
        if self.last_call_monotonic is not None:
            elapsed = time.monotonic() - self.last_call_monotonic
            delay = self.minimum_interval_seconds - elapsed
            if delay > 0:
                time.sleep(delay)
        result = public_get(path, params)
        self.last_call_monotonic = time.monotonic()
        return result


def archive(
    out_dir: Path,
    *,
    start: datetime,
    end: datetime,
    symbols: tuple[str, ...] = SYMBOLS,
) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("start/end must be timezone-aware and start before end")
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "venue": "BingX USDT-M Perpetual",
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "symbols": {},
        "auth": "public read-only endpoints; no API key",
    }
    fetcher = RateLimitedFetcher()
    for symbol in symbols:
        symbol_report = {}
        for interval in ("15m", "1h"):
            rows = fetch_klines(
                fetcher,
                symbol=symbol,
                interval=interval,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            path = out_dir / f"{symbol.replace('-', '_')}_{interval}.csv"
            _write_csv(path, rows)
            interval_ms = INTERVAL_MS[interval]
            gaps = [
                {"after": int(left["open_time"]), "before": int(right["open_time"])}
                for left, right in zip(rows, rows[1:])
                if int(right["open_time"]) - int(left["open_time"]) != interval_ms
            ]
            symbol_report[interval] = {
                "rows": len(rows),
                "gaps": len(gaps),
                "first_open_time": rows[0]["open_time"] if rows else None,
                "last_open_time": rows[-1]["open_time"] if rows else None,
                "file": path.name,
                "gap_examples": gaps[:10],
            }
        funding = fetch_funding(
            fetcher,
            symbol=symbol,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        funding_path = out_dir / f"{symbol.replace('-', '_')}_funding.csv"
        _write_csv(funding_path, funding)
        symbol_report["funding"] = {
            "rows": len(funding),
            "file": funding_path.name,
        }
        report["symbols"][symbol] = symbol_report
    (out_dir / "coverage.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/clt_bingx_market_data"))
    parser.add_argument("--start", default="2026-10-03T00:00:00+00:00")
    parser.add_argument("--end", default="2026-11-09T00:00:00+00:00")
    args = parser.parse_args()
    archive(
        args.out_dir,
        start=datetime.fromisoformat(args.start).astimezone(UTC),
        end=datetime.fromisoformat(args.end).astimezone(UTC),
    )


if __name__ == "__main__":
    main()
