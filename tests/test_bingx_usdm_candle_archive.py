from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import archive_bingx_usdm_candles as candle_archive
from scripts.archive_bingx_usdm_candles import (
    DEFAULT_TIMEFRAMES,
    TIMEFRAME_MS,
    _call_with_cooldown,
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
    }

@pytest.mark.asyncio
async def test_candle_archive_cooldown_uses_bounded_backoff(monkeypatch) -> None:
    sleeps: list[float] = []
    calls = 0

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def flaky_operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 4:
            raise RuntimeError("simulated BingX 100410 cooldown")
        return "ok"

    monkeypatch.setattr(candle_archive.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        candle_archive, "is_exchange_endpoint_cooldown_error", lambda exc: True
    )

    assert await _call_with_cooldown(flaky_operation, label="test candle page") == "ok"
    assert calls == 4
    assert sleeps == [5.0, 30.0, 60.0]


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


def test_30m_interval_is_supported_for_archive_probe() -> None:
    assert TIMEFRAME_MS["30m"] == 30 * 60_000
    rows = {0: _row(0), 30 * 60_000: _row(30 * 60_000)}
    coverage = _coverage(rows, TIMEFRAME_MS["30m"])
    assert coverage["bar_count"] == 2
    assert coverage["gap_count"] == 0

def test_daily_timeframes_follow_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"timeframes": ["1m", "5m"]}), encoding="utf-8")
    assert _resolve_timeframes(None, "daily", manifest) == ("1m", "5m")

def test_initial_timeframes_default_to_active_zeus_set(tmp_path: Path) -> None:
    assert _resolve_timeframes(None, "initial", tmp_path / "missing.json") == DEFAULT_TIMEFRAMES
