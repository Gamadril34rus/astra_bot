from __future__ import annotations

from scripts.probe_bingx_public_history import (
    _kline_summary,
    _minute_window_summary,
    _trade_summary,
)


def test_trade_probe_summary_reports_id_and_time_span() -> None:
    rows = [
        {"id": "101", "time": 1_700_000_000_000},
        {"id": "102", "time": 1_700_000_060_000},
    ]
    summary = _trade_summary(rows, 100)
    assert summary["requested_from_id"] == 100
    assert summary["rows"] == 2
    assert summary["min_id"] == 101
    assert summary["max_id"] == 102
    assert summary["estimated_100_row_pages_from_id_1"] == 2


def test_kline_probe_summary_handles_array_rows() -> None:
    rows = [
        [1_700_000_000_000, "1", "2", "0.5", "1.5", "10"],
        [1_700_000_900_000, "1.5", "2", "1", "1.8", "12"],
    ]
    summary = _kline_summary(rows)
    assert summary["rows"] == 2
    assert summary["first_open_utc"] is not None
    assert summary["last_open_utc"] is not None


def test_kline_probe_summary_handles_mark_price_objects() -> None:
    rows = [
        {"openTime": 1_700_000_000_000},
        {"openTime": 1_700_000_900_000},
    ]
    summary = _kline_summary(rows, object_rows=True)
    assert summary["rows"] == 2
    assert summary["first_open_utc"] is not None
    assert summary["last_open_utc"] is not None



def test_kline_probe_summary_handles_bingx_object_rows() -> None:
    rows = [
        {"openTime": 1_700_000_000_000},
        {"openTime": 1_700_000_900_000},
    ]
    summary = _kline_summary(rows)
    assert summary["rows"] == 2
    assert summary["first_open_utc"] is not None
    assert summary["last_open_utc"] is not None


def test_minute_window_summary_requires_exact_contiguous_coverage() -> None:
    start = 1_700_000_000_000
    end = start + 2 * 60_000 + 59_999
    complete = [
        [start, "1", "2", "0.5", "1.5", "10"],
        [start + 60_000, "1.5", "2", "1", "1.8", "12"],
        [start + 120_000, "1.8", "2", "1", "1.9", "14"],
    ]
    summary = _minute_window_summary(complete, start, end)
    assert summary["expected_rows"] == 3
    assert summary["full_contiguous_window"] is True
    summary = _minute_window_summary([complete[0], complete[2]], start, end)
    assert summary["full_contiguous_window"] is False
    assert summary["missing_minutes_between_returned_rows"] == 1


def test_minute_window_rejects_non_minute_aligned_intervals() -> None:
    start = 1_700_000_000_000
    end = start + 2 * 60_000 + 59_999
    rows = [
        [start, "1", "2", "0.5", "1.5", "10"],
        [start + 30_000, "1.5", "2", "1", "1.8", "12"],
        [start + 120_000, "1.8", "2", "1", "1.9", "14"],
    ]
    summary = _minute_window_summary(rows, start, end)
    assert summary["missing_minutes_between_returned_rows"] == 0
    assert summary["non_minute_intervals"] == 2
    assert summary["full_contiguous_window"] is False
