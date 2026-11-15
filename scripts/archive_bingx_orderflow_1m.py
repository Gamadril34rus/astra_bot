#!/usr/bin/env python3
"""Archive native BingX 1m USDT-M klines with taker-flow fields, then aggregate 15m/1h.

The 1m source is used only for exact OHLCV/flow aggregation. Strategy decisions
remain on closed 15m bars with the latest fully closed 1h context.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from datetime import UTC, date, datetime, time as dt_time
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://open-api.bingx.com"
KLINE_PATH = "/openApi/swap/v3/quote/klines"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
ONE_MINUTE_MS = 60_000
PAGE_LIMIT = 1_440
PRICE_FIELDS = ("open", "high", "low", "close")
OHLCV_FIELDS = (*PRICE_FIELDS, "volume")
CSV_FIELDS = (
    "open_time", "open_time_utc", "close_time", "close_time_utc",
    "open", "high", "low", "close", "volume", "quote_volume",
    "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dec(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite {field}: {value!r}")
    return result


def parse_row(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        open_time = row.get("time", row.get("openTime", row.get("open_time")))
        close_time = row.get("closeTime", row.get("close_time"))
        raw = {
            "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"),
            "volume": row.get("volume", row.get("vol")),
            "quote_volume": row.get("quoteVolume", row.get("quote_volume")),
            "trade_count": row.get("count", row.get("tradeCount", row.get("trade_count"))),
            "taker_buy_base_volume": row.get(
                "takerBuyVolume", row.get("takerBuyBaseVolume", row.get("taker_buy_base_volume"))
            ),
            "taker_buy_quote_volume": row.get("takerBuyQuoteVolume", row.get("taker_buy_quote_volume")),
        }
    elif isinstance(row, (list, tuple)) and len(row) >= 11:
        open_time, open_, high, low, close, volume, close_time, quote, count, taker_base, taker_quote = row[:11]
        raw = {
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "quote_volume": quote, "trade_count": count,
            "taker_buy_base_volume": taker_base,
            "taker_buy_quote_volume": taker_quote,
        }
    else:
        raise ValueError("BingX kline row must contain all 11 documented fields")
    if open_time is None:
        raise ValueError(f"BingX kline row has no open time: {row!r}")
    open_ms = int(open_time)
    close_ms = int(close_time) if close_time is not None else open_ms + ONE_MINUTE_MS - 1
    if not open_ms <= close_ms < open_ms + ONE_MINUTE_MS:
        raise ValueError(f"invalid 1m close time for {open_ms}: {close_ms}")
    parsed = {
        name: dec(raw[name], name)
        for name in (*OHLCV_FIELDS, "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume")
    }
    count_value = dec(raw["trade_count"], "trade_count")
    if count_value != count_value.to_integral_value() or count_value < 0:
        raise ValueError(f"invalid trade_count at {open_ms}: {count_value}")
    if any(parsed[name] <= 0 for name in PRICE_FIELDS):
        raise ValueError(f"non-positive OHLC at {open_ms}")
    if parsed["high"] < max(parsed["open"], parsed["close"], parsed["low"]):
        raise ValueError(f"invalid high at {open_ms}")
    if parsed["low"] > min(parsed["open"], parsed["close"], parsed["high"]):
        raise ValueError(f"invalid low at {open_ms}")
    for name in ("volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume"):
        if parsed[name] < 0:
            raise ValueError(f"negative {name} at {open_ms}")
    eps = Decimal("1e-12")
    if parsed["taker_buy_base_volume"] > parsed["volume"] + eps:
        raise ValueError(f"taker-buy base volume exceeds total volume at {open_ms}")
    if parsed["taker_buy_quote_volume"] > parsed["quote_volume"] + eps:
        raise ValueError(f"taker-buy quote volume exceeds total quote volume at {open_ms}")
    if parsed["volume"] > 0 and parsed["quote_volume"] <= 0:
        raise ValueError(f"missing quote volume at {open_ms}")
    if parsed["quote_volume"] > 0 and count_value <= 0:
        raise ValueError(f"missing trade count at {open_ms}")
    return {
        "open_time": open_ms, "close_time": close_ms,
        "trade_count": int(count_value), **parsed,
    }


def validate_page(raw_rows: list[Any], page_start: int, expected_count: int) -> list[dict[str, Any]]:
    rows = sorted((parse_row(row) for row in raw_rows), key=lambda item: item["open_time"])
    expected = list(range(page_start, page_start + expected_count * ONE_MINUTE_MS, ONE_MINUTE_MS))
    observed = [row["open_time"] for row in rows]
    if len(rows) != expected_count:
        raise RuntimeError(f"expected {expected_count} 1m bars at {page_start}, received {len(rows)}")
    if len(set(observed)) != len(observed):
        raise RuntimeError(f"duplicate 1m timestamps at {page_start}")
    if observed != expected:
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        raise RuntimeError(f"non-contiguous 1m page: missing={missing[:5]} extra={extra[:5]}")
    return rows


def aggregate_group(rows: list[dict[str, Any]], minutes: int) -> dict[str, Any]:
    if len(rows) != minutes:
        raise RuntimeError(f"incomplete {minutes}m aggregate: expected {minutes} source bars, got {len(rows)}")
    interval_ms = minutes * ONE_MINUTE_MS
    start = int(rows[0]["open_time"])
    if start % interval_ms:
        raise RuntimeError(f"unaligned {minutes}m aggregate at {start}")
    expected_times = list(range(start, start + interval_ms, ONE_MINUTE_MS))
    if [int(row["open_time"]) for row in rows] != expected_times:
        raise RuntimeError(f"missing/duplicate 1m source bars in {minutes}m aggregate at {start}")
    result: dict[str, Any] = {
        "open_time": start,
        "close_time": int(rows[-1]["close_time"]),
        "open": rows[0]["open"],
        "high": max(row["high"] for row in rows),
        "low": min(row["low"] for row in rows),
        "close": rows[-1]["close"],
    }
    for name in ("volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume"):
        result[name] = sum((row[name] for row in rows), Decimal("0"))
    result["trade_count"] = sum(int(row["trade_count"]) for row in rows)
    if result["taker_buy_base_volume"] > result["volume"] + Decimal("1e-9"):
        raise RuntimeError(f"aggregated taker-buy base exceeds total at {start}")
    if result["taker_buy_quote_volume"] > result["quote_volume"] + Decimal("1e-6"):
        raise RuntimeError(f"aggregated taker-buy quote exceeds total at {start}")
    return result


def iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def write_row(writer: csv.DictWriter, row: dict[str, Any]) -> None:
    start, end = int(row["open_time"]), int(row["close_time"])
    writer.writerow({
        "open_time": start, "open_time_utc": iso_ms(start),
        "close_time": end, "close_time_utc": iso_ms(end),
        **{key: str(row[key]) for key in (
            "open", "high", "low", "close", "volume", "quote_volume",
            "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume",
        )},
    })


class BingXPublicKlines:
    def __init__(self, qps: float) -> None:
        if qps <= 0 or qps > 1.0:
            raise ValueError("qps must be >0 and <=1.0 per BingX's documented 1 request/s limit")
        self.delay = 1.0 / qps
        self.last_request = 0.0

    def page(self, symbol: str, start_ms: int, end_ms: int, limit: int) -> list[Any]:
        params = {
            "symbol": symbol, "interval": "1m", "startTime": start_ms,
            "endTime": end_ms, "limit": limit,
        }
        backoffs = (5, 30, 60)
        for attempt in range(4):
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            request = Request(
                f"{BASE_URL}{KLINE_PATH}?{urlencode(params)}",
                headers={"Accept": "application/json", "User-Agent": "ASTRA-BOT-BingX-native-taker-flow-research/1.0"},
            )
            self.last_request = time.monotonic()
            try:
                with urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                limited = exc.code == 429 or "100410" in body
                if not limited or attempt == 3:
                    raise RuntimeError(f"BingX HTTP {exc.code} {symbol} 1m {start_ms}: {body[:300]}") from exc
                retry_after = exc.headers.get("Retry-After")
                time.sleep(max(float(retry_after), backoffs[attempt]) if retry_after else backoffs[attempt])
                continue
            if not isinstance(payload, dict):
                raise RuntimeError("BingX returned a non-object kline response")
            code = str(payload.get("code", "0"))
            if code == "100410":
                if attempt == 3:
                    raise RuntimeError(f"BingX 100410 retries exhausted for {symbol} 1m {start_ms}")
                time.sleep(backoffs[attempt])
                continue
            if code != "0":
                raise RuntimeError(f"BingX kline error {code} for {symbol} 1m: {payload.get('msg')}")
            data = payload.get("data") or []
            if isinstance(data, dict):
                data = data.get("klines", data.get("data", [])) or []
            if not isinstance(data, list):
                raise RuntimeError(f"unsupported BingX data shape for {symbol} 1m")
            return data
        raise RuntimeError("unreachable retry state")


def compare_legacy(new_path: Path, legacy_path: Path) -> dict[str, Any]:
    if not legacy_path.is_file():
        raise FileNotFoundError(f"legacy OHLCV file missing: {legacy_path}")
    with new_path.open("r", encoding="utf-8", newline="") as handle:
        new_rows = {int(row["open_time"]): row for row in csv.DictReader(handle)}
    with legacy_path.open("r", encoding="utf-8", newline="") as handle:
        old_rows = {int(row["open_time"]): row for row in csv.DictReader(handle)}
    if set(new_rows) != set(old_rows):
        raise RuntimeError(
            f"legacy timestamp mismatch for {new_path.name}: new={len(new_rows)} old={len(old_rows)}"
        )
    for timestamp, row in new_rows.items():
        old = old_rows[timestamp]
        for field in OHLCV_FIELDS:
            if dec(row[field], field) != dec(old[field], f"legacy {field}"):
                raise RuntimeError(f"legacy {field} mismatch at {timestamp}: {row[field]} != {old[field]}")
    return {"overlap_rows": len(new_rows), "ohlcv_exact_match": True}


def validate_funding(funding_dir: Path, start_ms: int, end_ms: int) -> dict[str, Any]:
    manifest_path = funding_dir / "funding_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"funding manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    window = manifest.get("core_common_window", {})
    if not window.get("covers_required_days") or not window.get("all_symbols_cadence_and_edges_ok"):
        raise RuntimeError("historical funding manifest does not pass the 365-day common-window gate")
    reports = {}
    for symbol in SYMBOLS:
        report = next((item for item in manifest.get("reports", []) if item.get("symbol") == symbol), None)
        if not report or not report.get("cadence_ok") or float(report.get("coverage_days_in_requested_window", 0)) < 365:
            raise RuntimeError(f"funding coverage/cadence failed for {symbol}")
        path = funding_dir / f"funding_{symbol.replace('-', '')}.csv"
        if not path.is_file():
            raise FileNotFoundError(f"funding CSV missing for {symbol}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            times = sorted(int(row["funding_time"]) for row in csv.DictReader(handle)
                           if start_ms <= int(row["funding_time"]) < end_ms)
        if len(times) < 1000 or len(set(times)) != len(times):
            raise RuntimeError(f"funding events missing/duplicated for {symbol}")
        max_gap_h = max((b - a) / 3_600_000 for a, b in pairwise(times))
        if max_gap_h > (4 if symbol == "HYPE-USDT" else 8):
            raise RuntimeError(f"funding gap {max_gap_h}h for {symbol}")
        reports[symbol] = {"events": len(times), "max_gap_hours": max_gap_h, "sha256": sha256(path)}
    return {"source_manifest_sha256": sha256(manifest_path), "symbols": reports}


def archive_symbol(
    client: BingXPublicKlines, symbol: str, start_ms: int, end_ms: int,
    out_dir: Path, legacy_dir: Path,
) -> dict[str, Any]:
    expected_total = (end_ms - start_ms) // ONE_MINUTE_MS
    if expected_total <= 0 or (end_ms - start_ms) % ONE_MINUTE_MS:
        raise ValueError("1m archive window must be positive and minute-aligned")
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = symbol.replace("-", "")
    paths = {
        "1m": out_dir / f"{prefix}_1m.csv",
        "15m": out_dir / f"{prefix}_15m.csv",
        "1h": out_dir / f"{prefix}_1h.csv",
    }
    handles = {key: path.open("w", encoding="utf-8", newline="") for key, path in paths.items()}
    writers = {}
    try:
        for key, handle in handles.items():
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writers[key] = writer
        cursor = start_ms
        total = 0
        trade_count_sum = 0
        quote_sum = Decimal("0")
        taker_quote_sum = Decimal("0")
        taker_base_sum = Decimal("0")
        base_sum = Decimal("0")
        while cursor < end_ms:
            count = min(PAGE_LIMIT, (end_ms - cursor) // ONE_MINUTE_MS)
            page_end = cursor + count * ONE_MINUTE_MS - 1
            raw = client.page(symbol, cursor, page_end, count)
            rows = validate_page(raw, cursor, count)
            for row in rows:
                write_row(writers["1m"], row)
            for minutes, key in ((15, "15m"), (60, "1h")):
                if len(rows) % minutes:
                    raise RuntimeError(f"page is not divisible into {minutes}m aggregates")
                for offset in range(0, len(rows), minutes):
                    write_row(writers[key], aggregate_group(rows[offset:offset + minutes], minutes))
            total += len(rows)
            trade_count_sum += sum(int(row["trade_count"]) for row in rows)
            quote_sum += sum((row["quote_volume"] for row in rows), Decimal("0"))
            taker_quote_sum += sum((row["taker_buy_quote_volume"] for row in rows), Decimal("0"))
            taker_base_sum += sum((row["taker_buy_base_volume"] for row in rows), Decimal("0"))
            base_sum += sum((row["volume"] for row in rows), Decimal("0"))
            cursor += count * ONE_MINUTE_MS
            print(f"{symbol} 1m: {total}/{expected_total}", flush=True)
    finally:
        for handle in handles.values():
            handle.close()
    if total != expected_total:
        raise RuntimeError(f"{symbol}: expected {expected_total} 1m rows, got {total}")
    legacy_checks = {
        "1m": compare_legacy(paths["1m"], legacy_dir / f"{prefix}_1m.csv"),
        "15m": compare_legacy(paths["15m"], legacy_dir / f"{prefix}_15m_aggregated.csv"),
        "1h": compare_legacy(paths["1h"], legacy_dir / f"{prefix}_1h_aggregated.csv"),
    }
    return {
        "1m": {"rows": total, "missing_bars": 0, "duplicate_bars": 0,
               "first_open_time_utc": iso_ms(start_ms), "last_open_time_utc": iso_ms(end_ms - ONE_MINUTE_MS),
               "sha256": sha256(paths["1m"]), "legacy_ohlcv_validation": legacy_checks["1m"]},
        "15m": {"rows": total // 15, "missing_bars": 0, "duplicate_bars": 0,
                "sha256": sha256(paths["15m"]), "legacy_ohlcv_validation": legacy_checks["15m"]},
        "1h": {"rows": total // 60, "missing_bars": 0, "duplicate_bars": 0,
               "sha256": sha256(paths["1h"]), "legacy_ohlcv_validation": legacy_checks["1h"]},
        "flow_totals": {
            "trade_count_sum": trade_count_sum,
            "base_volume_sum": str(base_sum), "quote_volume_sum": str(quote_sum),
            "taker_buy_base_volume_sum": str(taker_base_sum),
            "taker_buy_quote_volume_sum": str(taker_quote_sum),
            "taker_buy_quote_share": round(float(taker_quote_sum / quote_sum), 8) if quote_sum else None,
        },
    }


def archive(
    start: date, end_exclusive: date, out_dir: Path, legacy_dir: Path,
    funding_dir: Path, qps: float, legacy_run_id: str, funding_run_id: str,
) -> dict[str, Any]:
    if end_exclusive <= start or (end_exclusive - start).days < 365:
        raise ValueError("the research gate requires at least 365 complete UTC days")
    start_ms = int(datetime.combine(start, dt_time.min, tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(end_exclusive, dt_time.min, tzinfo=UTC).timestamp() * 1000)
    funding = validate_funding(funding_dir, start_ms, end_ms)
    client = BingXPublicKlines(qps)
    reports = {}
    for symbol in SYMBOLS:
        print(f"Downloading native BingX USDT-M 1m klines with taker flow for {symbol}", flush=True)
        reports[symbol] = archive_symbol(client, symbol, start_ms, end_ms, out_dir, legacy_dir)
    manifest = {
        "schema_version": 1, "exchange": "BingX", "market": "USDT-M perpetual swaps",
        "source_endpoint": KLINE_PATH, "source_interval": "1m",
        "requested_start_utc": datetime.combine(start, dt_time.min, tzinfo=UTC).isoformat(),
        "exclusive_end_utc": datetime.combine(end_exclusive, dt_time.min, tzinfo=UTC).isoformat(),
        "complete_utc_days": (end_exclusive - start).days, "page_limit": PAGE_LIMIT,
        "request_qps": qps, "symbols": reports, "funding_validation": funding,
        "source_artifacts": {"legacy_ohlcv_run_id": legacy_run_id, "funding_run_id": funding_run_id},
        "data_fields": ["open", "high", "low", "close", "volume", "quote_volume", "trade_count",
                        "taker_buy_base_volume", "taker_buy_quote_volume"],
        "derived_fields": ["taker_sell_base_volume=volume-taker_buy_base_volume",
                           "taker_sell_quote_volume=quote_volume-taker_buy_quote_volume",
                           "signed_taker_quote_imbalance=2*taker_buy_quote_volume-quote_volume"],
        "aggregation": {"15m": "15 consecutive complete 1m BingX bars; OHLC first/max/min/last; sum all volume and flow fields",
                        "1h": "60 consecutive complete 1m BingX bars; OHLC first/max/min/last; sum all volume and flow fields"},
        "signal_policy": "15m closed-candle decisions only; 1h context uses latest fully closed 1h candle; 1m is archive/aggregation source only",
        "limitations": ["Taker-buy fields are candle-aggregated aggressor proxies, not order-book depth or individual order IDs.",
                        "Historical order-book depth, historical OI series and wallet identity are unavailable in this archive.",
                        "Legacy OHLCV is cross-checked exactly; new flow fields are sourced directly from BingX 1m klines."],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2025-11-13")
    parser.add_argument("--end-date", default="2026-11-13", help="exclusive UTC date boundary")
    parser.add_argument("--out-dir", type=Path, default=Path("data/bingx_usdm_orderflow_1m"))
    parser.add_argument("--legacy-dir", type=Path, required=True)
    parser.add_argument("--funding-dir", type=Path, required=True)
    parser.add_argument("--legacy-run-id", required=True)
    parser.add_argument("--funding-run-id", required=True)
    parser.add_argument("--qps", type=float, default=0.8)
    args = parser.parse_args()
    manifest = archive(date.fromisoformat(args.start_date), date.fromisoformat(args.end_date),
                       args.out_dir, args.legacy_dir, args.funding_dir, args.qps,
                       args.legacy_run_id, args.funding_run_id)
    print(json.dumps({"complete_utc_days": manifest["complete_utc_days"],
                      "symbols": {symbol: {tf: report[tf] for tf in ("1m", "15m", "1h")}
                                  for symbol, report in manifest["symbols"].items()}}, indent=2))


if __name__ == "__main__":
    main()
