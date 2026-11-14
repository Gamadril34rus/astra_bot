from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from scripts.archive_bingx_1m_research import (
    MINUTE_MS,
    aggregate_rows,
    compare_native,
    merge_native_overlap,
    validate_daily_rows,
    write_rows,
)


def _synthetic_day(start_ms: int) -> list[dict]:
    rows = []
    for index in range(1_440):
        price = Decimal("100") + Decimal(index) / Decimal("1000")
        rows.append({
            "open_time": start_ms + index * MINUTE_MS,
            "open": price,
            "high": price + Decimal("1"),
            "low": price - Decimal("1"),
            "close": price + Decimal("0.1"),
            "volume": Decimal("2"),
        })
    return rows


def test_daily_validation_sorts_and_requires_every_minute() -> None:
    start = 1_763_020_800_000
    source = _synthetic_day(start)
    validated = validate_daily_rows(list(reversed(source)), start)
    assert len(validated) == 1_440
    assert validated[0]["open_time"] == start
    assert validated[-1]["open_time"] == start + 1_439 * MINUTE_MS

    with pytest.raises(RuntimeError, match="expected 1440"):
        validate_daily_rows(source[:-1], start)


def test_aggregation_produces_only_complete_15m_and_1h_bars() -> None:
    start = 1_763_020_800_000
    rows = _synthetic_day(start)
    fifteen = aggregate_rows(rows, 15)
    hourly = aggregate_rows(rows, 60)

    assert len(fifteen) == 96
    assert len(hourly) == 24
    assert fifteen[0]["open"] == rows[0]["open"]
    assert fifteen[0]["close"] == rows[14]["close"]
    assert fifteen[0]["high"] == max(row["high"] for row in rows[:15])
    assert fifteen[0]["volume"] == Decimal("30")
    assert hourly[0]["volume"] == Decimal("120")

    with pytest.raises(ValueError, match="only 15m and 1h"):
        aggregate_rows(rows, 5)


def test_native_overlap_requires_exact_ohlc_match(tmp_path: Path) -> None:
    start = 1_763_020_800_000
    aggregate_path = tmp_path / "aggregated.csv"
    native_path = tmp_path / "native.csv"
    aggregate = aggregate_rows(_synthetic_day(start), 15)[:2]
    write_rows(aggregate_path, aggregate)

    with aggregate_path.open("r", encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    with native_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    result = compare_native(aggregate_path, native_path, minimum_overlap=2)
    assert result["overlap_bars"] == 2
    assert result["exact_ohlc_match"] is True
    assert result["price_mismatch_count_sampled"] == 0

    rows[0]["close"] = str(Decimal(rows[0]["close"]) + Decimal("0.01"))
    with native_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    result = compare_native(aggregate_path, native_path, minimum_overlap=2)
    assert result["exact_ohlc_match"] is False
    assert result["price_mismatch_count"] == 1
    assert result["mismatch_samples"][0]["field"] == "close"



def test_native_overlap_splice_prefers_native_bars_and_preserves_full_series(tmp_path: Path) -> None:
    start = 1_763_020_800_000
    aggregate = aggregate_rows(_synthetic_day(start), 15)[:4]
    aggregate_path = tmp_path / "aggregate.csv"
    native_path = tmp_path / "native.csv"
    merged_path = tmp_path / "merged.csv"
    write_rows(aggregate_path, aggregate)

    native = [dict(row) for row in aggregate[1:3]]
    native[0]["close"] = native[0]["close"] + Decimal("0.01")
    write_rows(native_path, native)

    splice = merge_native_overlap(
        aggregate_path,
        native_path,
        merged_path,
        interval_ms=15 * MINUTE_MS,
        minimum_overlap=2,
    )
    with merged_path.open("r", encoding="utf-8", newline="") as handle:
        merged = list(csv.DictReader(handle))

    assert len(merged) == 4
    assert merged[0]["close"] == str(aggregate[0]["close"])
    assert merged[1]["close"] == str(native[0]["close"])
    assert merged[3]["close"] == str(aggregate[3]["close"])
    assert splice["native_overlap_rows"] == 2
    assert splice["aggregate_only_rows"] == 2
