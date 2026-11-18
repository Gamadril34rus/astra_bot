#!/usr/bin/env python3
"""Download and validate a frozen Binance USD-M 15m proxy dataset for SAR research.

This is explicitly a cross-exchange proxy, not BingX execution-price history.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://data.binance.vision/data/futures/um"
API = "https://fapi.binance.com/fapi/v1/fundingRate"
INTERVAL_MS = 15 * 60 * 1000
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def get_bytes(url: str, retries: int = 4) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ASTRA-SAR-research/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            if attempt == retries - 1:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed: {url}")


def verified_zip(url: str) -> bytes:
    archive = get_bytes(url)
    checksum_text = get_bytes(url + ".CHECKSUM").decode("utf-8").strip()
    expected = checksum_text.split()[0].lower()
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise RuntimeError(f"SHA256 mismatch for {url}: {actual} != {expected}")
    return archive


def month_iter(start: date, end: date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def daily_iter(start: date, end_exclusive: date):
    current = start
    while current < end_exclusive:
        yield current
        current += timedelta(days=1)


def read_zip_rows(blob: bytes):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected one CSV in archive, got {names}")
        with archive.open(names[0]) as stream:
            text = io.TextIOWrapper(stream, encoding="utf-8")
            for row in csv.reader(text):
                if not row:
                    continue
                try:
                    int(row[0])
                except (ValueError, IndexError):
                    continue  # CSV header
                if len(row) < 12:
                    raise RuntimeError(f"malformed kline row: {row}")
                yield row[:12]


def download_klines(symbol: str, start_ms: int, end_ms: int, out: Path) -> dict:
    start_dt = datetime.fromtimestamp(start_ms / 1000, timezone.utc).date()
    end_dt = datetime.fromtimestamp((end_ms - 1) / 1000, timezone.utc).date()
    rows: dict[int, list[str]] = {}
    duplicate_rows = 0
    sources = []
    # Completed historical months use monthly archives.
    for year, month in month_iter(start_dt, end_dt):
        month_start = date(year, month, 1)
        month_end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        if month_end <= end_dt + timedelta(days=1) and month_end <= date.today().replace(day=1):
            period = f"{year:04d}-{month:02d}"
            name = f"{symbol}-15m-{period}.zip"
            url = f"{BASE}/monthly/klines/{symbol}/15m/{name}"
            blob = verified_zip(url)
            for row in read_zip_rows(blob):
                ts = int(row[0])
                if start_ms <= ts < end_ms:
                    if ts in rows:
                        duplicate_rows += 1
                    rows[ts] = row
            sources.append({"url": url, "sha256": hashlib.sha256(blob).hexdigest()})
        else:
            day = max(start_dt, month_start)
            final_day = min(end_dt + timedelta(days=1), month_end)
            for current in daily_iter(day, final_day):
                name = f"{symbol}-15m-{current.isoformat()}.zip"
                url = f"{BASE}/daily/klines/{symbol}/15m/{name}"
                blob = verified_zip(url)
                for row in read_zip_rows(blob):
                    ts = int(row[0])
                    if start_ms <= ts < end_ms:
                        if ts in rows:
                            duplicate_rows += 1
                        rows[ts] = row
                sources.append({"url": url, "sha256": hashlib.sha256(blob).hexdigest()})
    ordered = [rows[key] for key in sorted(rows)]
    timestamps = sorted(rows)
    expected = list(range(start_ms, end_ms, INTERVAL_MS))
    missing = sorted(set(expected) - set(timestamps))
    unexpected = sorted(set(timestamps) - set(expected))
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{symbol}_15m_klines.csv.gz"
    with gzip.open(target, "wt", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        writer.writerows(ordered)
    return {
        "symbol": symbol,
        "rows": len(ordered),
        "expected_rows": len(expected),
        "duplicates_removed": duplicate_rows,
        "missing_bars": len(missing),
        "unexpected_bars": len(unexpected),
        "first_open_utc": datetime.fromtimestamp(timestamps[0] / 1000, timezone.utc).isoformat() if timestamps else None,
        "last_open_utc": datetime.fromtimestamp(timestamps[-1] / 1000, timezone.utc).isoformat() if timestamps else None,
        "file": target.name,
        "file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "sources": sources,
        "missing_open_times_sample": missing[:20],
    }


def download_funding(symbol: str, start_ms: int, end_ms: int, out: Path) -> dict:
    rows: dict[int, dict] = {}
    cursor = start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode({"symbol": symbol, "startTime": cursor, "endTime": end_ms - 1, "limit": 1000})
        payload = json.loads(get_bytes(f"{API}?{query}"))
        batch = payload.get("data", [])
        if not isinstance(batch, list):
            raise RuntimeError(f"invalid funding payload for {symbol}: {payload}")
        if not batch:
            break
        for item in batch:
            ts = int(item["fundingTime"])
            if start_ms <= ts < end_ms:
                rows[ts] = {
                    "symbol": symbol,
                    "funding_time": ts,
                    "funding_rate": item["fundingRate"],
                    "mark_price": item.get("markPrice", ""),
                    "rate_type": item.get("rateType", ""),
                }
        last = max(int(item["fundingTime"]) for item in batch)
        if last < cursor or last >= end_ms - 1:
            break
        cursor = last + 1
        time.sleep(0.15)
    ordered = [rows[key] for key in sorted(rows)]
    target = out / f"{symbol}_funding.csv.gz"
    with gzip.open(target, "wt", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["symbol", "funding_time", "funding_rate", "mark_price", "rate_type"])
        writer.writeheader()
        writer.writerows(ordered)
    return {
        "symbol": symbol,
        "funding_events": len(ordered),
        "first_funding_utc": datetime.fromtimestamp(ordered[0]["funding_time"] / 1000, timezone.utc).isoformat() if ordered else None,
        "last_funding_utc": datetime.fromtimestamp(ordered[-1]["funding_time"] / 1000, timezone.utc).isoformat() if ordered else None,
        "file": target.name,
        "file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-11-18T00:00:00Z")
    parser.add_argument("--end", default="2026-11-18T00:00:00Z")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    parser.add_argument("--out", type=Path, default=Path("artifacts/sar_binance_proxy"))
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(timezone.utc)
    end = datetime.fromisoformat(args.end.replace("Z", "+00:00")).astimezone(timezone.utc)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    if start_ms % INTERVAL_MS or end_ms % INTERVAL_MS or start_ms >= end_ms:
        raise SystemExit("start/end must be increasing UTC 15m boundaries")
    results = {"source": "Binance USD-M perpetual futures; cross-exchange proxy, not BingX", "interval": "15m", "start_utc": start.isoformat(), "end_utc_exclusive": end.isoformat(), "symbols": {}}
    for symbol in args.symbols:
        klines = download_klines(symbol, start_ms, end_ms, args.out)
        funding = download_funding(symbol, start_ms, end_ms, args.out)
        results["symbols"][symbol] = {"klines": klines, "funding": funding}
        print(f"{symbol}: candles={klines['rows']}/{klines['expected_rows']} missing={klines['missing_bars']} funding={funding['funding_events']}")
    manifest = args.out / "manifest.json"
    manifest.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
