#!/usr/bin/env python3
"""Archive one year of BingX USDT-M 1m OHLCV for 15m/1h research aggregation.

The 1m series is a source granularity only. Strategy decisions are made on
aggregated 15m bars and the last fully closed 1h context; no 1m/5m signals.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://open-api.bingx.com"
KLINE_PATH = "/openApi/swap/v3/quote/klines"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
MINUTE_MS = 60_000
DAY_MS = 1_440 * MINUTE_MS
PAGE_LIMIT = 1_440
FIELDS = ("open_time", "open_time_utc", "open", "high", "low", "close", "volume")
PRICE_FIELDS = ("open", "high", "low", "close")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite {field}: {value!r}")
    return result


def _parse_row(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        timestamp = row.get("time", row.get("openTime", row.get("open_time")))
        values = {name: row.get(name) for name in PRICE_FIELDS}
        values["volume"] = row.get("volume", row.get("vol"))
    elif isinstance(row, (list, tuple)) and len(row) >= 6:
        timestamp = row[0]
        values = dict(zip((*PRICE_FIELDS, "volume"), row[1:6], strict=True))
    else:
        raise ValueError(f"unsupported BingX kline row: {row!r}")
    if timestamp is None:
        raise ValueError(f"BingX kline row has no open time: {row!r}")
    open_time = int(timestamp)
    parsed = {name: _decimal(values[name], name) for name in (*PRICE_FIELDS, "volume")}
    if any(parsed[name] <= 0 for name in PRICE_FIELDS):
        raise ValueError(f"non-positive OHLC value at {open_time}: {parsed}")
    if parsed["volume"] < 0:
        raise ValueError(f"negative volume at {open_time}: {parsed['volume']}")
    if parsed["high"] < max(parsed["open"], parsed["close"], parsed["low"]):
        raise ValueError(f"invalid high at {open_time}")
    if parsed["low"] > min(parsed["open"], parsed["close"], parsed["high"]):
        raise ValueError(f"invalid low at {open_time}")
    return {"open_time": open_time, **parsed}


def validate_daily_rows(raw_rows: list[Any], day_start_ms: int) -> list[dict[str, Any]]:
    rows = sorted((_parse_row(row) for row in raw_rows), key=lambda row: row["open_time"])
    expected = list(range(day_start_ms, day_start_ms + DAY_MS, MINUTE_MS))
    observed = [row["open_time"] for row in rows]
    if len(rows) != PAGE_LIMIT:
        raise RuntimeError(
            f"expected {PAGE_LIMIT} 1m bars at {day_start_ms}, received {len(rows)}"
        )
    if len(set(observed)) != len(observed):
        raise RuntimeError(f"duplicate 1m timestamps at {day_start_ms}")
    if observed != expected:
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        raise RuntimeError(
            f"1m day is not contiguous at {day_start_ms}: "
            f"missing={missing[:5]} extra={extra[:5]}"
        )
    return rows


def aggregate_rows(rows: list[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
    if minutes not in (15, 60):
        raise ValueError("only 15m and 1h aggregation is permitted")
    if len(rows) % minutes:
        raise ValueError(f"{len(rows)} 1m rows cannot form complete {minutes}m bars")
    interval_ms = minutes * MINUTE_MS
    output = []
    for offset in range(0, len(rows), minutes):
        group = rows[offset:offset + minutes]
        first = group[0]["open_time"]
        expected = list(range(first, first + interval_ms, MINUTE_MS))
        if [row["open_time"] for row in group] != expected or first % interval_ms:
            raise RuntimeError(f"incomplete or misaligned {minutes}m bucket at {first}")
        output.append({
            "open_time": first,
            "open": group[0]["open"],
            "high": max(row["high"] for row in group),
            "low": min(row["low"] for row in group),
            "close": group[-1]["close"],
            "volume": sum((row["volume"] for row in group), Decimal("0")),
        })
    return output


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            timestamp = int(row["open_time"])
            writer.writerow({
                "open_time": timestamp,
                "open_time_utc": datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(),
                **{key: str(row[key]) for key in ("open", "high", "low", "close", "volume")},
            })


class BingXPublicKlines:
    def __init__(self, qps: float) -> None:
        if qps <= 0:
            raise ValueError("qps must be positive")
        self.delay = 1.0 / qps
        self.last_request = 0.0

    def _pace(self) -> None:
        wait = self.delay - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)

    def daily(self, symbol: str, day_start_ms: int) -> list[Any]:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": day_start_ms,
            "endTime": day_start_ms + DAY_MS - 1,
            "limit": PAGE_LIMIT,
        }
        backoffs = (5, 30, 60)
        for attempt in range(4):
            self._pace()
            request = Request(
                f"{BASE_URL}{KLINE_PATH}?{urlencode(params)}",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "ASTRA-BOT-BingX-research-archive/1.0",
                },
            )
            self.last_request = time.monotonic()
            try:
                with urlopen(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                rate_limited = exc.code == 429 or "100410" in raw
                if not rate_limited or attempt == 3:
                    raise RuntimeError(
                        f"BingX HTTP {exc.code} for {symbol} {day_start_ms}: {raw[:300]}"
                    ) from exc
                retry_after = exc.headers.get("Retry-After")
                time.sleep(max(float(retry_after), backoffs[attempt]) if retry_after else backoffs[attempt])
                continue
            if not isinstance(payload, dict):
                raise RuntimeError("BingX returned a non-object kline response")
            code = str(payload.get("code", "0"))
            if code == "100410":
                if attempt == 3:
                    raise RuntimeError(f"BingX 100410 retries exhausted for {symbol} {day_start_ms}")
                time.sleep(backoffs[attempt])
                continue
            if code != "0":
                raise RuntimeError(
                    f"BingX kline error {code} for {symbol} {day_start_ms}: {payload.get('msg')}"
                )
            data = payload.get("data") or []
            if isinstance(data, dict):
                data = data.get("klines", data.get("data", [])) or []
            if not isinstance(data, list):
                raise RuntimeError(f"unsupported BingX kline data shape for {symbol} {day_start_ms}")
            return data
        raise RuntimeError("unreachable retry state")


def compare_native(
    aggregate_path: Path,
    native_path: Path,
    *,
    minimum_overlap: int = 100,
) -> dict[str, Any]:
    aggregate: dict[int, dict[str, str]] = {}
    with aggregate_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            aggregate[int(row["open_time"])] = row
    overlap = 0
    mismatches = []
    with native_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            timestamp = int(row["open_time"])
            other = aggregate.get(timestamp)
            if other is None:
                continue
            overlap += 1
            for field in PRICE_FIELDS:
                if _decimal(row[field], field) != _decimal(other[field], field):
                    mismatches.append({
                        "open_time": timestamp,
                        "field": field,
                        "native": row[field],
                        "aggregated": other[field],
                    })
                    if len(mismatches) >= 10:
                        break
            if len(mismatches) >= 10:
                break
    if overlap < minimum_overlap:
        raise RuntimeError(
            f"insufficient native overlap for {aggregate_path.name}: {overlap} < {minimum_overlap}"
        )
    return {
        "overlap_bars": overlap,
        "price_mismatch_count_sampled": len(mismatches),
        "mismatch_samples": mismatches,
        "exact_ohlc_match": not mismatches,
    }


def archive(
    start: date,
    end_exclusive: date,
    out_dir: Path,
    native_dir: Path | None,
    qps: float,
    symbols: tuple[str, ...],
) -> dict[str, Any]:
    if end_exclusive <= start:
        raise ValueError("end date must be later than start date")
    days = [start + timedelta(days=i) for i in range((end_exclusive - start).days)]
    if len(days) < 365:
        raise ValueError("the research gate requires at least 365 complete UTC days")
    out_dir.mkdir(parents=True, exist_ok=True)
    client = BingXPublicKlines(qps)
    reports: dict[str, Any] = {}

    for symbol in symbols:
        print(f"Downloading {symbol}: {days[0]} through {days[-1]}", flush=True)
        one_minute_path = out_dir / f"{symbol.replace('-', '')}_1m.csv"
        fifteen_path = out_dir / f"{symbol.replace('-', '')}_15m.csv"
        hourly_path = out_dir / f"{symbol.replace('-', '')}_1h.csv"
        symbol_rows = 0
        first_open = None
        last_open = None
        previous_last = None
        aggregate_counts = {"15m": 0, "1h": 0}

        with (
            one_minute_path.open("w", encoding="utf-8", newline="") as one_handle,
            fifteen_path.open("w", encoding="utf-8", newline="") as fifteen_handle,
            hourly_path.open("w", encoding="utf-8", newline="") as hourly_handle,
        ):
            one_writer = csv.DictWriter(one_handle, fieldnames=FIELDS)
            fifteen_writer = csv.DictWriter(fifteen_handle, fieldnames=FIELDS)
            hourly_writer = csv.DictWriter(hourly_handle, fieldnames=FIELDS)
            one_writer.writeheader()
            fifteen_writer.writeheader()
            hourly_writer.writeheader()

            for day in days:
                day_start = int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
                raw = client.daily(symbol, day_start)
                rows = validate_daily_rows(raw, day_start)
                if previous_last is not None and rows[0]["open_time"] != previous_last + MINUTE_MS:
                    raise RuntimeError(f"cross-day 1m gap for {symbol} at {rows[0]['open_time']}")
                previous_last = rows[-1]["open_time"]
                first_open = rows[0]["open_time"] if first_open is None else first_open
                last_open = rows[-1]["open_time"]
                symbol_rows += len(rows)

                for row in rows:
                    timestamp = row["open_time"]
                    one_writer.writerow({
                        "open_time": timestamp,
                        "open_time_utc": datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(),
                        **{key: str(row[key]) for key in ("open", "high", "low", "close", "volume")},
                    })
                for row in aggregate_rows(rows, 15):
                    timestamp = row["open_time"]
                    fifteen_writer.writerow({
                        "open_time": timestamp,
                        "open_time_utc": datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(),
                        **{key: str(row[key]) for key in ("open", "high", "low", "close", "volume")},
                    })
                    aggregate_counts["15m"] += 1
                for row in aggregate_rows(rows, 60):
                    timestamp = row["open_time"]
                    hourly_writer.writerow({
                        "open_time": timestamp,
                        "open_time_utc": datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(),
                        **{key: str(row[key]) for key in ("open", "high", "low", "close", "volume")},
                    })
                    aggregate_counts["1h"] += 1

        expected_minutes = len(days) * PAGE_LIMIT
        if symbol_rows != expected_minutes:
            raise RuntimeError(f"{symbol}: expected {expected_minutes} 1m bars, got {symbol_rows}")
        if aggregate_counts != {"15m": len(days) * 96, "1h": len(days) * 24}:
            raise RuntimeError(f"{symbol}: incomplete 15m/1h aggregation: {aggregate_counts}")

        native_checks = {}
        if native_dir is not None:
            for timeframe, path in (("15m", fifteen_path), ("1h", hourly_path)):
                native_path = native_dir / f"{symbol.replace('-', '')}_{timeframe}.csv"
                if not native_path.is_file():
                    raise FileNotFoundError(f"native BingX overlap file is missing: {native_path}")
                native_checks[timeframe] = compare_native(path, native_path)

        reports[symbol] = {
            "one_minute_rows": symbol_rows,
            "first_open_time_utc": datetime.fromtimestamp(first_open / 1000, UTC).isoformat(),
            "last_open_time_utc": datetime.fromtimestamp(last_open / 1000, UTC).isoformat(),
            "missing_minute_bars": 0,
            "duplicate_minute_bars": 0,
            "aggregated_15m_rows": aggregate_counts["15m"],
            "aggregated_1h_rows": aggregate_counts["1h"],
            "one_minute_sha256": _sha256(one_minute_path),
            "aggregated_15m_sha256": _sha256(fifteen_path),
            "aggregated_1h_sha256": _sha256(hourly_path),
            "native_overlap_validation": native_checks,
        }

    manifest = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "source_endpoint": KLINE_PATH,
        "source_interval": "1m",
        "page_limit": PAGE_LIMIT,
        "request_qps": qps,
        "requested_start_utc": datetime.combine(start, datetime.min.time(), tzinfo=UTC).isoformat(),
        "exclusive_end_utc": datetime.combine(end_exclusive, datetime.min.time(), tzinfo=UTC).isoformat(),
        "complete_utc_days": len(days),
        "symbols": reports,
        "data_fields": ["open", "high", "low", "close", "volume"],
        "unavailable_fields": [
            "quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote",
            "historical_order_book", "historical_open_interest", "wallet_identity",
        ],
        "aggregation": {
            "15m": "15 consecutive complete BingX 1m bars; OHLC first/max/min/last; volume sum",
            "1h": "60 consecutive complete BingX 1m bars; OHLC first/max/min/last; volume sum",
            "signal_policy": "15m decisions only; 1h context uses the latest fully closed 1h candle; no 1m/5m signals",
            "missing_or_duplicate_minutes": "fatal; no filling or imputation",
        },
        "limitations": [
            "This archive contains BingX swap OHLCV only. It does not contain trade prints, taker-buy volume, quote volume, historical order-book depth or historical open interest.",
            "Volume is preserved as returned by the BingX 1m endpoint and is used only within the same symbol; no cross-symbol volume comparison is implied.",
            "Native 15m/1h overlap checks validate aggregated OHLC against the separate BingX candle archive, not tick-level execution.",
        ],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if native_dir is not None:
        failed = {
            symbol: {
                timeframe: check
                for timeframe, check in report["native_overlap_validation"].items()
                if not check["exact_ohlc_match"]
            }
            for symbol, report in reports.items()
        }
        failed = {symbol: rows for symbol, rows in failed.items() if rows}
        if failed:
            raise RuntimeError(f"aggregated BingX OHLC does not match native candles: {failed}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2025-11-13")
    parser.add_argument("--end-date", default="2026-11-13", help="exclusive UTC date boundary")
    parser.add_argument("--out-dir", type=Path, default=Path("data/bingx_research_1m"))
    parser.add_argument("--native-dir", type=Path, default=None)
    parser.add_argument("--qps", type=float, default=0.5)
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    args = parser.parse_args()
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    report = archive(
        date.fromisoformat(args.start_date),
        date.fromisoformat(args.end_date),
        args.out_dir,
        args.native_dir,
        args.qps,
        symbols,
    )
    print(json.dumps({
        "complete_utc_days": report["complete_utc_days"],
        "symbols": {
            symbol: {
                "one_minute_rows": item["one_minute_rows"],
                "aggregated_15m_rows": item["aggregated_15m_rows"],
                "aggregated_1h_rows": item["aggregated_1h_rows"],
                "native_overlap_validation": item["native_overlap_validation"],
            }
            for symbol, item in report["symbols"].items()
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
