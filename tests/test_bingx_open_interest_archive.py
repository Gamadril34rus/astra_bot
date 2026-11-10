from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.archive_bingx_open_interest import (
    _archive_path,
    _merge_rows,
    _read_rows,
    _write_rows,
    parse_open_interest,
)


def _row(symbol: str, source_time: int, oi: str) -> dict[str, str]:
    return parse_open_interest(
        symbol,
        {"data": {"symbol": symbol, "time": source_time, "openInterest": oi}},
        source_time + 1000,
    )


def test_open_interest_parser_preserves_source_time_and_unit() -> None:
    row = _row("BTC-USDT", 1_800_000, "123.456")

    assert row["symbol"] == "BTC-USDT"
    assert row["source_time"] == "1800000"
    assert row["source_time_utc"].endswith("+00:00")
    assert Decimal(row["open_interest_base"]) == Decimal("123.456")


@pytest.mark.parametrize(
    "data,observed_at",
    [
        ({"symbol": "BTC-USDT", "openInterest": "1"}, 2_000_000),
        ({"symbol": "BTC-USDT", "time": 1_800_000}, 2_000_000),
        ({"symbol": "ETH-USDT", "time": 1_800_000, "openInterest": "1"}, 2_000_000),
        ({"symbol": "BTC-USDT", "time": 1_800_000, "openInterest": "0"}, 2_000_000),
        ({"symbol": "BTC-USDT", "time": 2_100_000, "openInterest": "1"}, 2_000_000),
    ],
)
def test_open_interest_parser_rejects_incomplete_or_invalid_data(
    data: dict[str, str], observed_at: int
) -> None:
    with pytest.raises(ValueError):
        parse_open_interest("BTC-USDT", {"data": data}, observed_at)


def test_open_interest_merge_is_sorted_and_idempotent() -> None:
    first = _row("BTC-USDT", 1_800_000, "100")
    second = _row("BTC-USDT", 3_600_000, "101")
    merged = _merge_rows(
        {3_600_000: second},
        {1_800_000: first, 3_600_000: second},
    )

    assert list(merged) == [1_800_000, 3_600_000]
    assert _merge_rows(merged, {3_600_000: second}) == merged


def test_open_interest_merge_rejects_conflicting_same_timestamp() -> None:
    previous = _row("BTC-USDT", 1_800_000, "100")
    revised = _row("BTC-USDT", 1_800_000, "100.1")

    with pytest.raises(ValueError, match="immutable open-interest conflict"):
        _merge_rows({1_800_000: previous}, {1_800_000: revised})


def test_open_interest_csv_roundtrip(tmp_path: Path) -> None:
    path = _archive_path(tmp_path, "BTC-USDT")
    rows = {
        1_800_000: _row("BTC-USDT", 1_800_000, "100"),
        3_600_000: _row("BTC-USDT", 3_600_000, "101"),
    }
    _write_rows(path, rows)

    assert _read_rows(path) == rows
    with path.open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 2
