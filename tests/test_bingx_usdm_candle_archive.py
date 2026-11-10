from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.archive_bingx_usdm_candles import (
    DEFAULT_TIMEFRAMES,
    TIMEFRAME_MS,
    _coverage,
    _merge_rows,
    _read_rows,
    _resolve_timeframes,
    _write_rows,
)


def _row(open_time: int, close: str = "100") -> dict[str, str]:
    return {
        "open_time": str(open_time),
        "open_time_utc": "2026-01-01T00:00:00+00:00",
        "open": "99",
        "high": "101",
        "low": "98",
        "close": close,
        "volume": "10",
        "quote_volume": "1000",
        "trades_count": "3",
        "taker_buy_volume": "",
        "taker_buy_quote_volume": "",
    }

def test_merge_is_sorted_and_idempotent() -> None:
    existing = {0: _row(0), 60_000: _row(60_000)}
    incoming = {60_000: _row(60_000), 120_000: _row(120_000)}
    merged = _merge_rows(existing, incoming)
    assert list(merged) == [0, 60_000, 120_000]
    assert _merge_rows(merged, incoming) == merged

def test_merge_rejects_revised_historical_bar() -> None:
    existing = {60_000: _row(60_000, "100")}
    incoming = {60_000: _row(60_000, "100.01")}
    with pytest.raises(ValueError, match="immutable candle conflict"):
        _merge_rows(existing, incoming)

def test_csv_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "candles.csv"
    rows = {0: _row(0), 60_000: _row(60_000)}
    _write_rows(path, rows)
    assert _read_rows(path) == rows

def test_coverage_counts_missing_intervals() -> None:
    rows = {0: _row(0), 15 * 60_000: _row(15 * 60_000), 45 * 60_000: _row(45 * 60_000)}
    coverage = _coverage(rows, TIMEFRAME_MS["15m"])
    assert coverage["bar_count"] == 3
    assert coverage["gap_count"] == 1
    assert coverage["gap_samples"][0]["missing_bars"] == 1

def test_merge_tolerates_optional_trade_count_shape_difference() -> None:
    existing_row = _row(60_000)
    incoming_row = _row(60_000)
    existing_row["trades_count"] = "0"
    incoming_row["trades_count"] = "42"
    assert _merge_rows({60_000: existing_row}, {60_000: incoming_row}) == {
        60_000: existing_row
    }

def test_daily_timeframes_follow_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"timeframes": ["1m", "5m"]}), encoding="utf-8")
    assert _resolve_timeframes(None, "daily", manifest) == ("1m", "5m")

def test_initial_timeframes_default_to_active_zeus_set(tmp_path: Path) -> None:
    assert _resolve_timeframes(None, "initial", tmp_path / "missing.json") == DEFAULT_TIMEFRAMES


def test_parse_bingx_kline_keeps_taker_buy_volumes() -> None:
    from decimal import Decimal

    from astra_bot.adapters.bingx.client import BingXClient

    client = object.__new__(BingXClient)
    candle = client._parse_candle(
        [
            1_800_000, "100", "102", "99", "101", "10",
            3_599_999, "1000", 25, "6.5", "650",
        ],
        "BTC-USDT",
        "30m",
    )

    assert candle is not None
    assert candle.trades_count == 25
    assert candle.taker_buy_volume == Decimal("6.5")
    assert candle.taker_buy_quote_volume == Decimal("650")


def test_parse_bingx_kline_marks_missing_taker_volumes_unknown() -> None:
    from astra_bot.adapters.bingx.client import BingXClient

    client = object.__new__(BingXClient)
    candle = client._parse_candle(
        [1_800_000, "100", "102", "99", "101", "10", 3_599_999, "1000"],
        "BTC-USDT",
        "30m",
    )

    assert candle is not None
    assert candle.taker_buy_volume is None
    assert candle.taker_buy_quote_volume is None


def test_legacy_archive_can_be_enriched_with_taker_buy_fields() -> None:
    previous = _row(60_000)
    incoming = _row(60_000)
    incoming["taker_buy_volume"] = "6.5"
    incoming["taker_buy_quote_volume"] = "650"

    merged = _merge_rows({60_000: previous}, {60_000: incoming})

    assert merged[60_000]["taker_buy_volume"] == "6.5"
    assert merged[60_000]["taker_buy_quote_volume"] == "650"


def test_taker_buy_volume_conflict_is_not_silently_overwritten() -> None:
    previous = _row(60_000)
    previous["taker_buy_volume"] = "6.5"
    previous["taker_buy_quote_volume"] = "650"
    incoming = _row(60_000)
    incoming["taker_buy_volume"] = "6.6"
    incoming["taker_buy_quote_volume"] = "660"

    with pytest.raises(ValueError, match="immutable candle conflict"):
        _merge_rows({60_000: previous}, {60_000: incoming})
