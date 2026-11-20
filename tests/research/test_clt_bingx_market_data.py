from __future__ import annotations

import pytest
from scripts.research.clt_bingx_market_data import (
    INTERVAL_MS,
    fetch_funding,
    fetch_klines,
)


def _raw_kline(ts: int, interval: str = "15m") -> list:
    return [
        ts, "100", "102", "99", "101", "10",
        ts + INTERVAL_MS[interval] - 1, "1000", "50", "5", "500",
    ]


def test_klines_are_downloaded_in_non_overlapping_bounded_chunks():
    calls = []

    def fake_fetch(path, params):
        calls.append((path, params))
        interval_ms = INTERVAL_MS[params["interval"]]
        return [
            _raw_kline(ts, params["interval"])
            for ts in range(params["startTime"], params["endTime"] + 1, interval_ms)
        ]

    rows = fetch_klines(
        fake_fetch,
        symbol="BTC-USDT",
        interval="15m",
        start_ms=0,
        end_ms=3 * INTERVAL_MS["15m"],
        limit=2,
    )
    assert len(calls) == 2
    assert [row["open_time"] for row in rows] == [
        0, INTERVAL_MS["15m"], 2 * INTERVAL_MS["15m"]
    ]
    assert calls[0][1]["endTime"] + 1 == calls[1][1]["startTime"]


def test_klines_reject_invalid_ohlc():
    def fake_fetch(path, params):
        row = _raw_kline(params["startTime"])
        row[2] = "98"
        return [row]

    with pytest.raises(RuntimeError, match="invalid BingX OHLC"):
        fetch_klines(
            fake_fetch,
            symbol="BTC-USDT",
            interval="15m",
            start_ms=0,
            end_ms=INTERVAL_MS["15m"],
            limit=10,
        )


def test_funding_history_is_sorted_and_range_filtered():
    def fake_fetch(path, params):
        return [
            {"fundingTime": 300, "fundingRate": "0.0003"},
            {"fundingTime": 100, "fundingRate": "0.0001"},
            {"fundingTime": 200, "fundingRate": "0.0002"},
        ]

    rows = fetch_funding(
        fake_fetch,
        symbol="BTC-USDT",
        start_ms=100,
        end_ms=300,
        pause_seconds=0,
    )
    assert [row["funding_time"] for row in rows] == [100, 200]
    assert rows[0]["funding_rate"] == pytest.approx(0.0001)


def test_unsupported_kline_interval_fails_closed():
    with pytest.raises(ValueError, match="unsupported interval"):
        fetch_klines(
            lambda *_: [],
            symbol="BTC-USDT",
            interval="3m",
            start_ms=0,
            end_ms=1,
        )
