from __future__ import annotations

import pytest
from scripts.research.clt_bingx_market_data import (
    INTERVAL_MS,
    fetch_funding,
    fetch_hyperliquid_klines,
    fetch_klines,
    parse_hyperliquid_kline,
    parse_kline,
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



def test_object_form_klines_are_supported_without_inventing_missing_fields():
    row = parse_kline({
        "time": 900_000,
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "12.5",
    }, interval_ms=INTERVAL_MS["15m"])
    assert row["open_time"] == 900_000
    assert row["close_time"] == 2 * INTERVAL_MS["15m"] - 1
    assert row["quote_volume"] is None
    assert row["trade_count"] is None
    assert row["taker_buy_quote"] is None


def test_seven_field_array_klines_are_supported():
    raw = [0, "100", "102", "99", "101", "10", INTERVAL_MS["15m"] - 1]
    row = parse_kline(raw, interval_ms=INTERVAL_MS["15m"])
    assert row["close_time"] == INTERVAL_MS["15m"] - 1
    assert row["quote_volume"] is None


def test_object_kline_missing_timestamp_is_rejected():
    with pytest.raises(RuntimeError, match="timestamp"):
        parse_kline({
            "open": "100", "high": "102", "low": "99",
            "close": "101", "volume": "10",
        }, interval_ms=INTERVAL_MS["15m"])



def _raw_hyperliquid_kline(ts: int, interval: str = "15m") -> dict:
    return {
        "t": ts,
        "T": ts + INTERVAL_MS[interval] - 1,
        "o": "100",
        "h": "102",
        "l": "99",
        "c": "101",
        "v": "10",
        "n": 50,
    }


def test_hyperliquid_candles_use_documented_snapshot_schema():
    calls = []

    def fake_fetch(payload):
        calls.append(payload)
        return [
            _raw_hyperliquid_kline(INTERVAL_MS["15m"]),
            _raw_hyperliquid_kline(0),
        ]

    rows = fetch_hyperliquid_klines(
        fake_fetch,
        coin="BTC",
        interval="15m",
        start_ms=0,
        end_ms=2 * INTERVAL_MS["15m"],
    )
    assert [row["open_time"] for row in rows] == [0, INTERVAL_MS["15m"]]
    assert rows[0]["trade_count"] == 50
    assert calls[0]["type"] == "candleSnapshot"
    assert calls[0]["req"]["coin"] == "BTC"
    assert calls[0]["req"]["endTime"] == 2 * INTERVAL_MS["15m"] - 1


def test_hyperliquid_candles_reject_invalid_ohlc():
    raw = _raw_hyperliquid_kline(0)
    raw["h"] = "98"
    with pytest.raises(RuntimeError, match="invalid Hyperliquid OHLC"):
        parse_hyperliquid_kline(raw, interval_ms=INTERVAL_MS["15m"])


def test_hyperliquid_candles_reject_more_than_5000_bars():
    with pytest.raises(ValueError, match="5,000-bar"):
        fetch_hyperliquid_klines(
            lambda *_: [],
            coin="BTC",
            interval="15m",
            start_ms=0,
            end_ms=5_001 * INTERVAL_MS["15m"],
        )


def test_hyperliquid_candles_reject_duplicate_timestamps():
    def fake_fetch(payload):
        return [_raw_hyperliquid_kline(0), _raw_hyperliquid_kline(0)]

    with pytest.raises(RuntimeError, match="duplicate"):
        fetch_hyperliquid_klines(
            fake_fetch,
            coin="BTC",
            interval="15m",
            start_ms=0,
            end_ms=INTERVAL_MS["15m"],
        )
