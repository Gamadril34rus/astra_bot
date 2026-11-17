from __future__ import annotations

from datetime import UTC, datetime

from scripts.probe_bingx_1m_depth import summarize_window


def test_summarize_contiguous_1m_window_and_array_fields() -> None:
    start = int(datetime(2025, 11, 13, tzinfo=UTC).timestamp() * 1000)
    rows = [
        [start + i * 60_000, "100", "101", "99", "100", "2", start + (i + 1) * 60_000 - 1, "200", 25, "1.2", "120"]
        for i in range(3)
    ]
    result = summarize_window(rows, start, 60_000, 3)
    assert result["full_contiguous_window"] is True
    assert result["duplicate_timestamps"] == 0
    assert result["nonzero_share"]["quote_volume"] == 1.0
    assert result["nonzero_share"]["trade_count"] == 1.0
    assert result["nonzero_share"]["taker_buy_base"] == 1.0
    assert result["nonzero_share"]["taker_buy_quote"] == 1.0


def test_summarize_rejects_gap_and_duplicate() -> None:
    start = int(datetime(2025, 11, 13, tzinfo=UTC).timestamp() * 1000)
    rows = [
        {"time": start, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"},
        {"time": start + 120_000, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"},
        {"time": start + 120_000, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"},
    ]
    result = summarize_window(rows, start, 60_000, 3)
    assert result["full_contiguous_window"] is False
    assert result["duplicate_timestamps"] == 1
    assert result["missing_minutes_or_bars_between_rows"] == 1
