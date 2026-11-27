from __future__ import annotations

from datetime import UTC, datetime

import scripts.research.clt_hyperliquid_candles as candles


def test_fetch_candles_validates_and_sorts(monkeypatch):
    monkeypatch.setattr(candles, "post_info", lambda _payload: [
        {"t": 900000, "o": "10", "h": "12", "l": "9", "c": "11", "v": "2", "n": 4},
        {"t": 0, "o": "8", "h": "9", "l": "7", "c": "8.5", "v": "1", "n": 2},
        {"t": 900000, "o": "10", "h": "12", "l": "9", "c": "11", "v": "2", "n": 4},
    ])
    rows = candles.fetch_candles("BTC", 0, 1_000_000)
    assert [row["timestamp_ms"] for row in rows] == [0, 900000]
    assert rows[0]["coin"] == "BTC"


def test_fetch_candles_rejects_invalid_ohlc(monkeypatch):
    monkeypatch.setattr(candles, "post_info", lambda _payload: [
        {"t": 0, "o": "10", "h": "9", "l": "8", "c": "9", "v": "1", "n": 1},
    ])
    try:
        candles.fetch_candles("BTC", 0, 900000)
    except ValueError as exc:
        assert "Invalid candle" in str(exc)
    else:
        raise AssertionError("invalid OHLC must fail closed")


def test_download_writes_all_five_markets_and_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(candles, "fetch_candles", lambda coin, start, end: [
        {"coin": coin, "timestamp_ms": start, "open": 10.0, "high": 11.0,
         "low": 9.0, "close": 10.5, "volume": 1.0, "trades": 1}
    ])
    monkeypatch.setattr(candles.time, "sleep", lambda _seconds: None)
    report = candles.download(
        datetime(2026, 10, 3, tzinfo=UTC),
        datetime(2026, 10, 3, 0, 15, tzinfo=UTC),
        tmp_path,
    )
    assert report["rows"] == 5
    assert set(report["coverage_by_coin"]) == {"BTC", "ETH", "SOL", "HYPE", "INJ"}
    assert (tmp_path / "hyperliquid_15m.csv").exists()
    assert (tmp_path / "hyperliquid_15m_coverage.json").exists()
