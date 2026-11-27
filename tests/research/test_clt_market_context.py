from __future__ import annotations

import pytest
from scripts.research.clt_market_context import (
    HOUR_MS,
    attach_closed_1h_context,
)


def _hour(index: int, *, close: float, high: float, low: float) -> dict:
    ts = index * HOUR_MS
    return {
        "ts": ts,
        "close_time": ts + HOUR_MS - 1,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
    }


def _quarter(ts: int) -> dict:
    return {"ts": ts, "open": 100, "high": 101, "low": 99, "close": 100}


def test_15m_bar_never_uses_still_open_1h_candle():
    hours = [
        _hour(0, close=100, high=101, low=99),
        _hour(1, close=101, high=102, low=100),
        _hour(2, close=105, high=106, low=101),
    ]
    rows = attach_closed_1h_context(
        [_quarter(2 * HOUR_MS + 30 * 60_000)],
        hours,
        lookback_hours=2,
    )
    assert rows[0]["htf_context_ready"] is False
    assert rows[0]["htf_context_ts"] is None


def test_same_timestamp_closed_1h_bar_is_available_without_lookahead():
    hours = [
        _hour(0, close=100, high=101, low=99),
        _hour(1, close=101, high=102, low=100),
        _hour(2, close=105, high=106, low=101),
    ]
    rows = attach_closed_1h_context(
        [_quarter(2 * HOUR_MS + 45 * 60_000)],
        hours,
        lookback_hours=2,
    )
    row = rows[0]
    assert row["htf_context_ready"] is True
    assert row["htf_context_ts"] == 2 * HOUR_MS
    assert row["htf_structure_break"] == 1
    assert row["htf_return_pct"] == pytest.approx(5.0)
    assert row["htf_context_age_ms"] == 1


def test_1h_gaps_are_rejected_instead_of_bridged():
    hours = [
        _hour(0, close=100, high=101, low=99),
        _hour(2, close=105, high=106, low=101),
    ]
    with pytest.raises(ValueError, match="gap"):
        attach_closed_1h_context([_quarter(3 * HOUR_MS)], hours)


def test_15m_timestamps_must_be_strictly_increasing():
    bar = _quarter(0)
    with pytest.raises(ValueError, match="strictly chronological"):
        attach_closed_1h_context([bar, bar], [])


def test_1h_ohlc_range_must_be_valid():
    hours = [_hour(0, close=105, high=101, low=99)]
    with pytest.raises(ValueError, match="invalid 1h OHLC"):
        attach_closed_1h_context([_quarter(0)], hours)
