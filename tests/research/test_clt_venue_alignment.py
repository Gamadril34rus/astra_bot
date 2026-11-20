from __future__ import annotations

import csv

import pytest

from scripts.research.clt_venue_alignment import (
    compare_candles,
    load_candles,
)


def _bars(*, final_close: float = 98.0) -> list[dict]:
    rows = []
    for index in range(10):
        close = final_close if index == 9 else 100.0
        rows.append({
            "open_time": index,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": close,
        })
    return rows


def test_identical_venues_have_full_direction_and_break_alignment():
    rows = _bars()
    result = compare_candles(rows, rows, lookback_bars=8)
    assert result["matched_bars"] == 10
    assert result["candle_direction_agreement_pct"] == 100
    assert result["same_direction_swing_breaks"] == 1
    assert result["opposite_direction_swing_breaks"] == 0
    assert result["same_direction_overlap_pct"] == 100


def test_opposite_structure_breaks_are_counted_not_hidden():
    bingx = _bars(final_close=98.0)
    hyperliquid = _bars(final_close=102.0)
    result = compare_candles(bingx, hyperliquid, lookback_bars=8)
    assert result["opposite_direction_swing_breaks"] == 1
    assert result["same_direction_swing_breaks"] == 0
    assert result["same_direction_overlap_pct"] == 0


def test_venue_comparison_requires_exact_timestamp_alignment():
    bingx = _bars()
    hyperliquid = _bars()
    hyperliquid[-1]["open_time"] = 99
    with pytest.raises(ValueError, match="timestamps do not align"):
        compare_candles(bingx, hyperliquid)


def test_candle_loader_rejects_duplicate_timestamps(tmp_path):
    path = tmp_path / "candles.csv"
    fields = ["open_time", "open", "high", "low", "close"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        row = {"open_time": 1, "open": 100, "high": 101, "low": 99, "close": 100}
        writer.writerow(row)
        writer.writerow(row)
    with pytest.raises(ValueError, match="duplicate or unordered"):
        load_candles(path)


def test_candle_loader_rejects_invalid_ohlc(tmp_path):
    path = tmp_path / "candles.csv"
    fields = ["open_time", "open", "high", "low", "close"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "open_time": 1, "open": 100, "high": 98, "low": 99, "close": 100
        })
    with pytest.raises(ValueError, match="invalid OHLC range"):
        load_candles(path)
