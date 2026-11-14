#!/usr/bin/env python3
"""Build strict 15m/1h OKX USDT-perp research files from verified 1m archives."""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

MINUTE_MS = 60_000
TIMEFRAMES = {"15m": 15, "1h": 60}


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite {field}: {value!r}")
    return result


def iter_monthly_rows(source_dir: Path, months: list[str], inst_id: str) -> Iterator[dict[str, Any]]:
    previous_time = None
    for month in months:
        path = source_dir / month / f"{inst_id}_1m_{month}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                timestamp = int(row["open_time"])
                if previous_time is not None and timestamp != previous_time + MINUTE_MS:
                    raise ValueError(
                        f"{inst_id}: 1m continuity failure before {timestamp}; "
                        f"previous={previous_time}"
                    )
                if row.get("confirm") != "1":
                    raise ValueError(f"{inst_id}: unconfirmed 1m candle at {timestamp}")
                previous_time = timestamp
                yield {
                    "open_time": timestamp,
                    "open": _decimal(row["open"], "open"),
                    "high": _decimal(row["high"], "high"),
                    "low": _decimal(row["low"], "low"),
                    "close": _decimal(row["close"], "close"),
                    "volume_contracts": _decimal(row["volume_contracts"], "volume contracts"),
                    "volume": _decimal(row["volume_base"], "base volume"),
                    "quote_volume": _decimal(row["volume_quote"], "quote volume"),
                    "confirm": row["confirm"],
                    "source": row.get("source", "unknown"),
                }


def _aggregate_group(rows: list[dict[str, Any]], interval_minutes: int) -> dict[str, Any] | None:
    if not rows:
        return None
    interval_ms = interval_minutes * MINUTE_MS
    bucket_start = rows[0]["open_time"] // interval_ms * interval_ms
    expected_times = [bucket_start + index * MINUTE_MS for index in range(interval_minutes)]
    if len(rows) != interval_minutes or [row["open_time"] for row in rows] != expected_times:
        return None
    return {
        "open_time": bucket_start,
        "open": str(rows[0]["open"]),
        "high": str(max(row["high"] for row in rows)),
        "low": str(min(row["low"] for row in rows)),
        "close": str(rows[-1]["close"]),
        "volume": str(sum((row["volume"] for row in rows), Decimal("0"))),
        "quote_volume": str(sum((row["quote_volume"] for row in rows), Decimal("0"))),
        "volume_contracts": str(sum((row["volume_contracts"] for row in rows), Decimal("0"))),
        "confirm": "1",
    }


def aggregate_timeframes(
    rows: Iterator[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[int]]]:
    output = {timeframe: [] for timeframe in TIMEFRAMES}
    incomplete = {timeframe: [] for timeframe in TIMEFRAMES}
    groups: dict[str, list[dict[str, Any]]] = {timeframe: [] for timeframe in TIMEFRAMES}
    bucket_starts: dict[str, int | None] = {timeframe: None for timeframe in TIMEFRAMES}

    def flush(timeframe: str) -> None:
        group = groups[timeframe]
        if not group:
            return
        result = _aggregate_group(group, TIMEFRAMES[timeframe])
        if result is None:
            incomplete[timeframe].append(int(bucket_starts[timeframe]))
        else:
            output[timeframe].append(result)
        groups[timeframe] = []
        bucket_starts[timeframe] = None

    for row in rows:
        timestamp = int(row["open_time"])
        for timeframe, minutes in TIMEFRAMES.items():
            interval_ms = minutes * MINUTE_MS
            bucket = timestamp // interval_ms * interval_ms
            if bucket_starts[timeframe] is not None and bucket != bucket_starts[timeframe]:
                flush(timeframe)
            if bucket_starts[timeframe] is None:
                bucket_starts[timeframe] = bucket
            groups[timeframe].append(row)
    for timeframe in TIMEFRAMES:
        flush(timeframe)
    return output, incomplete


def read_funding(source_dir: Path, months: list[str], inst_id: str, symbol: str) -> tuple[list[dict], dict]:
    rates: dict[int, str] = {}
    for month in months:
        path = source_dir / month / f"{inst_id}_funding_archive_{month}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                timestamp = int(row["funding_time"])
                rate = str(_decimal(row["funding_rate"], "funding rate"))
                if timestamp in rates and rates[timestamp] != rate:
                    raise ValueError(f"{inst_id}: conflicting funding rate at {timestamp}")
                if timestamp in rates:
                    raise ValueError(f"{inst_id}: duplicate funding timestamp {timestamp}")
                rates[timestamp] = rate
    ordered = [
        {"funding_time": timestamp, "rate": rate, "symbol": symbol}
        for timestamp, rate in sorted(rates.items())
    ]
    if not ordered:
        raise ValueError(f"{inst_id}: empty settled funding history")
    times = [item["funding_time"] for item in ordered]
    max_gap_hours = max(
        (right - left) / 3_600_000
        for left, right in itertools.pairwise(times)
    ) if len(times) > 1 else 0.0
    if max_gap_hours > 8.0:
        raise ValueError(f"{inst_id}: settled funding gap exceeds 8h: {max_gap_hours}")
    return ordered, {
        "events": len(ordered),
        "first_funding_time": times[0],
        "last_funding_time": times[-1],
        "max_gap_hours": max_gap_hours,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> str:
    if not rows:
        raise ValueError(f"refusing to write empty research dataset: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_dataset(source_dir: Path, out_dir: Path) -> dict:
    source_manifest_path = source_dir / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    months = source_manifest["months"]
    begin_ms = int(source_manifest["common_window_start_ms"])
    end_ms = int(source_manifest["common_window_end_ms"])
    expected_minutes = (end_ms + 1 - begin_ms) // MINUTE_MS
    expected_rows = {
        "15m": expected_minutes // 15,
        "1h": expected_minutes // 60,
    }
    result = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "exchange": "OKX",
        "market": "USDT-margined perpetual swaps",
        "source_manifest_sha256": hashlib.sha256(source_manifest_path.read_bytes()).hexdigest(),
        "months": months,
        "window_start_ms": begin_ms,
        "window_end_ms": end_ms,
        "expected_1m_bars_per_symbol": expected_minutes,
        "symbols": {},
        "limitations": [
            "This is OKX cross-venue robustness data, not BingX execution or fill data.",
            "1m bars are aggregated only when every minute in the 15m/1h bucket is present and confirmed.",
            "Funding uses the settled monthly archive rate validated against REST realizedRate on the overlapping recent month.",
            "All simulated entries/exits remain subject to the strategy's fee, spread, and slippage assumptions.",
        ],
    }
    for inst_id in sorted(source_manifest["symbols"]):
        symbol = inst_id.removesuffix("-SWAP")
        stem = symbol.replace("-", "")
        rows = iter_monthly_rows(source_dir, months, inst_id)
        bars, incomplete = aggregate_timeframes(rows)
        symbol_summary = {}
        for timeframe, output_rows in bars.items():
            if incomplete[timeframe]:
                raise ValueError(
                    f"{inst_id}: incomplete {timeframe} buckets: "
                    f"{incomplete[timeframe][:10]}"
                )
            if len(output_rows) != expected_rows[timeframe]:
                raise ValueError(
                    f"{inst_id}: {timeframe} bars={len(output_rows)} "
                    f"expected={expected_rows[timeframe]}"
                )
            path = out_dir / f"{stem}_{timeframe}.csv"
            digest = _write_csv(path, output_rows)
            symbol_summary[timeframe] = {
                "rows": len(output_rows),
                "first_open_time": output_rows[0]["open_time"],
                "last_open_time": output_rows[-1]["open_time"],
                "sha256": digest,
                "path": str(path),
            }
        funding, funding_summary = read_funding(source_dir, months, inst_id, symbol)
        funding_path = out_dir / f"funding_{stem}.csv"
        funding_digest = _write_csv(funding_path, funding)
        funding_summary.update({"sha256": funding_digest, "path": str(funding_path)})
        symbol_summary["funding"] = funding_summary
        result["symbols"][symbol] = symbol_summary
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("reports/okx_public_history_probe"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/okx_usdm"))
    args = parser.parse_args()
    print(json.dumps(build_dataset(args.source_dir, args.out_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
