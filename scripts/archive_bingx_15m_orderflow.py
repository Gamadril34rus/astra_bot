#!/usr/bin/env python3
"""Download native BingX USDT-M 15m/1h klines with quote volume and taker-buy fields.

This archive is for research only. Signals are evaluated on closed 15m candles
with the latest fully closed 1h context. No 1m/5m candles or signals are used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://open-api.bingx.com"
KLINE_PATH = "/openApi/swap/v3/quote/klines"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
INTERVALS = {"15m": 15 * 60_000, "1h": 60 * 60_000}
PAGE_LIMIT = 1_440
PRICE_FIELDS = ("open", "high", "low", "close")
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
    except Exception as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite {field}: {value!r}")
    return result


def parse_row(row: Any, interval_ms: int) -> dict[str, Any]:
    if isinstance(row, dict):
        open_time = row.get("time", row.get("openTime", row.get("open_time")))
        close_time = row.get("closeTime", row.get("close_time"))
        raw = {
            "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"),
            "volume": row.get("volume", row.get("vol")),
            "quote_volume": row.get("quoteVolume", row.get("quote_volume")),
            "trade_count": row.get("count", row.get("tradeCount", row.get("trade_count"))),
            "taker_buy_base_volume": row.get("takerBuyVolume", row.get("taker_buy_base_volume")),
            "taker_buy_quote_volume": row.get("takerBuyQuoteVolume", row.get("taker_buy_quote_volume")),
        }
    elif isinstance(row, (list, tuple)) and len(row) >= 11:
        open_time, open_, high, low, close, volume, close_time, quote_volume, count, taker_base, taker_quote = row[:11]
        raw = {
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "quote_volume": quote_volume, "trade_count": count,
            "taker_buy_base_volume": taker_base, "taker_buy_quote_volume": taker_quote,
        }
    else:
        raise ValueError(
            "BingX kline row must contain all 11 documented fields "
            "(open time, OHLCV, close time, quote volume, trade count, taker-buy base/quote)"
        )
    if open_time is None:
        raise ValueError(f"BingX kline row has no open time: {row!r}")
    open_ms = int(open_time)
    close_ms = int(close_time) if close_time is not None else open_ms + interval_ms - 1
    if not open_ms <= close_ms < open_ms + interval_ms:
        raise ValueError(f"invalid close time for {open_ms}: {close_ms}")
    parsed = {name: dec(raw[name], name) for name in (*PRICE_FIELDS, "volume", "quote_volume",
               "taker_buy_base_volume", "taker_buy_quote_volume")}
    count_value = dec(raw["trade_count"], "trade_count")
    if count_value != count_value.to_integral_value():
        raise ValueError(f"non-integral trade_count at {open_ms}: {count_value}")
    count = int(count_value)
    if count < 0:
        raise ValueError(f"negative trade_count at {open_ms}")
    if any(parsed[name] <= 0 for name in PRICE_FIELDS):
        raise ValueError(f"non-positive OHLC value at {open_ms}: {parsed}")
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
        raise ValueError(f"taker-buy quote volume exceeds quote volume at {open_ms}")
    if parsed["volume"] > 0 and (parsed["quote_volume"] <= 0 or count <= 0):
        raise ValueError(f"missing quote volume/trade count for non-empty candle at {open_ms}")
    return {
        "open_time": open_ms, "close_time": close_ms, "trade_count": count, **parsed,
    }


def validate_page(raw_rows: list[Any], page_start: int, expected_count: int, interval_ms: int) -> list[dict[str, Any]]:
    rows = sorted((parse_row(row, interval_ms) for row in raw_rows), key=lambda item: item["open_time"])
    expected = list(range(page_start, page_start + expected_count * interval_ms, interval_ms))
    observed = [row["open_time"] for row in rows]
    if len(rows) != expected_count:
        raise RuntimeError(f"expected {expected_count} bars at {page_start}, received {len(rows)}")
    if len(set(observed)) != len(observed):
        raise RuntimeError(f"duplicate timestamps at {page_start}")
    if observed != expected:
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        raise RuntimeError(f"non-contiguous page at {page_start}: missing={missing[:5]} extra={extra[:5]}")
    return rows


class BingXPublicKlines:
    def __init__(self, qps: float) -> None:
        if qps <= 0:
            raise ValueError("qps must be positive")
        self.delay = 1.0 / qps
        self.last_request = 0.0

    def page(self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int) -> list[Any]:
        params = {
            "symbol": symbol, "interval": interval, "startTime": start_ms,
            "endTime": end_ms, "limit": limit,
        }
        backoffs = (5, 30, 60)
        for attempt in range(4):
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            request = Request(
                f"{BASE_URL}{KLINE_PATH}?{urlencode(params)}",
                headers={"Accept": "application/json", "User-Agent": "ASTRA-BOT-BingX-15m-orderflow-research/1.0"},
            )
            self.last_request = time.monotonic()
            try:
                with urlopen(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                limited = exc.code == 429 or "100410" in body
                if not limited or attempt == 3:
                    raise RuntimeError(f"BingX HTTP {exc.code} {symbol} {interval} {start_ms}: {body[:300]}") from exc
                retry_after = exc.headers.get("Retry-After")
                time.sleep(max(float(retry_after), backoffs[attempt]) if retry_after else backoffs[attempt])
                continue
            if not isinstance(payload, dict):
                raise RuntimeError("BingX returned a non-object kline response")
            code = str(payload.get("code", "0"))
            if code == "100410":
                if attempt == 3:
                    raise RuntimeError(f"BingX 100410 retries exhausted for {symbol} {interval} {start_ms}")
                time.sleep(backoffs[attempt])
                continue
            if code != "0":
                raise RuntimeError(f"BingX kline error {code} for {symbol} {interval}: {payload.get('msg')}")
            data = payload.get("data") or []
            if isinstance(data, dict):
                data = data.get("klines", data.get("data", [])) or []
            if not isinstance(data, list):
                raise RuntimeError(f"unsupported BingX data shape for {symbol} {interval}")
            return data
        raise RuntimeError("unreachable retry state")


def write_interval(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            open_ms = int(row["open_time"])
            close_ms = int(row["close_time"])
            writer.writerow({
                "open_time": open_ms,
                "open_time_utc": datetime.fromtimestamp(open_ms / 1000, UTC).isoformat(),
                "close_time": close_ms,
                "close_time_utc": datetime.fromtimestamp(close_ms / 1000, UTC).isoformat(),
                **{key: str(row[key]) for key in ("open", "high", "low", "close", "volume", "quote_volume",
                   "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume")},
            })


def archive_interval(
    client: BingXPublicKlines, symbol: str, interval: str, start_ms: int, end_ms: int, out_path: Path
) -> dict[str, Any]:
    interval_ms = INTERVALS[interval]
    if start_ms % interval_ms or end_ms % interval_ms or end_ms <= start_ms:
        raise ValueError(f"unaligned or invalid {interval} window")
    expected_total = (end_ms - start_ms) // interval_ms
    all_rows: list[dict[str, Any]] = []
    cursor = start_ms
    while cursor < end_ms:
        count = min(PAGE_LIMIT, (end_ms - cursor) // interval_ms)
        page_end = cursor + count * interval_ms - 1
        raw = client.page(symbol, interval, cursor, page_end, count)
        rows = validate_page(raw, cursor, count, interval_ms)
        all_rows.extend(rows)
        cursor += count * interval_ms
        print(f"{symbol} {interval}: {len(all_rows)}/{expected_total}", flush=True)
    if len(all_rows) != expected_total:
        raise RuntimeError(f"{symbol} {interval}: expected {expected_total} rows, got {len(all_rows)}")
    timestamps = [int(row["open_time"]) for row in all_rows]
    if len(set(timestamps)) != len(timestamps) or any(b - a != interval_ms for a, b in pairwise(timestamps)):
        raise RuntimeError(f"{symbol} {interval}: duplicate or missing bars after pagination")
    if timestamps[0] != start_ms or timestamps[-1] + interval_ms != end_ms:
        raise RuntimeError(f"{symbol} {interval}: archive edges do not match requested window")
    write_interval(out_path, all_rows)
    total_quote = sum((row["quote_volume"] for row in all_rows), Decimal("0"))
    total_base = sum((row["volume"] for row in all_rows), Decimal("0"))
    total_taker_quote = sum((row["taker_buy_quote_volume"] for row in all_rows), Decimal("0"))
    total_taker_base = sum((row["taker_buy_base_volume"] for row in all_rows), Decimal("0"))
    ratios = [
        float(row["taker_buy_quote_volume"] / row["quote_volume"])
        for row in all_rows if row["quote_volume"] > 0
    ]
    return {
        "rows": len(all_rows),
        "first_open_time_utc": datetime.fromtimestamp(timestamps[0] / 1000, UTC).isoformat(),
        "last_open_time_utc": datetime.fromtimestamp(timestamps[-1] / 1000, UTC).isoformat(),
        "missing_bars": 0, "duplicate_bars": 0,
        "trade_count_sum": sum(int(row["trade_count"]) for row in all_rows),
        "base_volume_sum": str(total_base),
        "quote_volume_sum": str(total_quote),
        "taker_buy_base_volume_sum": str(total_taker_base),
        "taker_buy_quote_volume_sum": str(total_taker_quote),
        "taker_buy_quote_share": round(float(total_taker_quote / total_quote), 8) if total_quote else None,
        "bar_taker_buy_quote_share_min": round(min(ratios), 8) if ratios else None,
        "bar_taker_buy_quote_share_max": round(max(ratios), 8) if ratios else None,
        "sha256": sha256(out_path),
        "file": out_path.name,
    }


def compare_legacy(new_path: Path, legacy_path: Path, interval: str) -> dict[str, Any]:
    if not legacy_path.is_file():
        raise FileNotFoundError(f"legacy OHLCV comparison file missing: {legacy_path}")
    with new_path.open("r", encoding="utf-8", newline="") as handle:
        new = {int(row["open_time"]): row for row in csv.DictReader(handle)}
    with legacy_path.open("r", encoding="utf-8", newline="") as handle:
        old = {int(row["open_time"]): row for row in csv.DictReader(handle)}
    common = sorted(set(new).intersection(old))
    if len(common) < 1000:
        raise RuntimeError(f"insufficient {interval} legacy overlap: {len(common)}")
    mismatches = []
    volume_abs = 0.0
    volume_rel = 0.0
    volume_mismatch_count = 0
    for timestamp in common:
        for field in PRICE_FIELDS:
            if dec(new[timestamp][field], field) != dec(old[timestamp][field], field):
                mismatches.append({"open_time": timestamp, "field": field,
                                   "new": new[timestamp][field], "legacy": old[timestamp][field]})
                if len(mismatches) >= 10:
                    break
        left = float(dec(new[timestamp]["volume"], "volume"))
        right = float(dec(old[timestamp]["volume"], "legacy volume"))
        absolute = abs(left - right)
        relative = absolute / max(abs(right), 1e-12)
        volume_abs = max(volume_abs, absolute)
        volume_rel = max(volume_rel, relative)
        if relative > 1e-3:
            volume_mismatch_count += 1
        if len(mismatches) >= 10:
            break
    if mismatches or volume_mismatch_count:
        raise RuntimeError(
            f"{interval} OHLCV differs from prior BingX archive: "
            f"ohlc={mismatches[:10]} volume_mismatch_count_above_1e-3={volume_mismatch_count} "
            f"max_volume_rel_diff={volume_rel}"
        )
    return {
        "overlap_bars": len(common), "ohlc_exact_match": True,
        "volume_mismatch_count_above_1e-3": volume_mismatch_count,
        "max_volume_abs_diff": volume_abs, "max_volume_relative_diff": volume_rel,
        "mismatch_samples": [],
    }


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
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        times = sorted(int(row["funding_time"]) for row in rows if start_ms <= int(row["funding_time"]) < end_ms)
        if len(times) < 1000 or len(set(times)) != len(times):
            raise RuntimeError(f"funding events missing/duplicated for {symbol}")
        max_gap_h = max((b - a) / 3_600_000 for a, b in pairwise(times))
        if max_gap_h > (4 if symbol == "HYPE-USDT" else 8):
            raise RuntimeError(f"funding gap {max_gap_h}h for {symbol}")
        reports[symbol] = {
            "events": len(times), "max_gap_hours": max_gap_h,
            "sha256": sha256(path),
        }
    return {
        "source_manifest": "funding_manifest.json",
        "manifest_sha256": sha256(manifest_path),
        "symbols": reports,
    }


def archive(
    start: date, end_exclusive: date, out_dir: Path, qps: float,
    symbols: tuple[str, ...], legacy_dir: Path | None, funding_dir: Path | None,
    legacy_run_id: str | None, funding_run_id: str | None,
) -> dict[str, Any]:
    if end_exclusive <= start or (end_exclusive - start).days < 365:
        raise ValueError("the research gate requires at least 365 complete UTC days")
    start_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(end_exclusive, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    client = BingXPublicKlines(qps)
    reports: dict[str, Any] = {}
    for symbol in symbols:
        print(f"Downloading native BingX USDT-M 15m/1h data for {symbol}", flush=True)
        reports[symbol] = {}
        for interval in ("15m", "1h"):
            path = out_dir / f"{symbol.replace('-', '')}_{interval}.csv"
            reports[symbol][interval] = archive_interval(client, symbol, interval, start_ms, end_ms, path)
            if legacy_dir is not None:
                legacy = legacy_dir / f"{symbol.replace('-', '')}_{interval}.csv"
                reports[symbol][interval]["legacy_ohlcv_validation"] = compare_legacy(path, legacy, interval)
    funding_report = validate_funding(funding_dir, start_ms, end_ms) if funding_dir is not None else None
    manifest = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "source_endpoint": KLINE_PATH,
        "source_intervals": list(INTERVALS),
        "page_limit": PAGE_LIMIT,
        "request_qps": qps,
        "requested_start_utc": datetime.combine(start, datetime.min.time(), tzinfo=UTC).isoformat(),
        "exclusive_end_utc": datetime.combine(end_exclusive, datetime.min.time(), tzinfo=UTC).isoformat(),
        "complete_utc_days": (end_exclusive - start).days,
        "symbols": reports,
        "funding_validation": funding_report,
        "source_artifacts": {
            "legacy_1m_archive_run_id": legacy_run_id,
            "historical_funding_run_id": funding_run_id,
        },
        "data_fields": [
            "open", "high", "low", "close", "volume", "quote_volume", "trade_count",
            "taker_buy_base_volume", "taker_buy_quote_volume",
        ],
        "derived_fields": [
            "taker_sell_base_volume = volume - taker_buy_base_volume",
            "taker_sell_quote_volume = quote_volume - taker_buy_quote_volume",
            "taker_buy_quote_share = taker_buy_quote_volume / quote_volume",
            "signed_taker_quote_imbalance = 2 * taker_buy_quote_volume - quote_volume",
        ],
        "unavailable_fields": ["historical_order_book_depth", "historical_open_interest_series", "wallet_identity"],
        "signal_policy": "15m closed-candle decisions only; 1h context uses the latest fully closed 1h candle; no 1m/5m signals",
        "limitations": [
            "Kline taker-buy fields are candle-aggregated aggressor proxies, not a complete historical order-book or individual trade-print archive.",
            "Historical open-interest series and historical order-book depth are not included.",
            "The legacy archive comparison validates OHLCV only; the new native BingX candles are the source of quote volume, trade count and taker-buy fields.",
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2025-11-13")
    parser.add_argument("--end-date", default="2026-11-13", help="exclusive UTC date boundary")
    parser.add_argument("--out-dir", type=Path, default=Path("data/bingx_usdm_orderflow"))
    parser.add_argument("--legacy-dir", type=Path, default=None)
    parser.add_argument("--funding-dir", type=Path, default=None)
    parser.add_argument("--legacy-run-id", default=None)
    parser.add_argument("--funding-run-id", default=None)
    parser.add_argument("--qps", type=float, default=0.8)
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    args = parser.parse_args()
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    if set(symbols) != set(SYMBOLS):
        raise ValueError(f"research core must include exactly {SYMBOLS}")
    report = archive(
        date.fromisoformat(args.start_date), date.fromisoformat(args.end_date),
        args.out_dir, args.qps, symbols, args.legacy_dir, args.funding_dir,
        args.legacy_run_id, args.funding_run_id,
    )
    print(json.dumps({
        "complete_utc_days": report["complete_utc_days"],
        "symbols": {symbol: {
            tf: {"rows": item[tf]["rows"], "trade_count_sum": item[tf]["trade_count_sum"],
                 "taker_buy_quote_share": item[tf]["taker_buy_quote_share"],
                 "sha256": item[tf]["sha256"]}
            for tf in ("15m", "1h")
        } for symbol, item in report["symbols"].items()},
        "funding_validation": report["funding_validation"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
