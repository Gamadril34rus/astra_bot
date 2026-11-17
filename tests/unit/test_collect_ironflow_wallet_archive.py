from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from scripts import collect_ironflow_wallet_archive as collector


def test_days_inclusive_returns_exact_37_day_archive_window() -> None:
    days = collector.days_inclusive(date(2026, 10, 3), date(2026, 11, 8))
    assert len(days) == 37
    assert days[0] == date(2026, 10, 3)
    assert days[-1] == date(2026, 11, 8)


def test_days_inclusive_rejects_reversed_window() -> None:
    try:
        collector.days_inclusive(date(2026, 11, 8), date(2026, 10, 3))
    except ValueError as exc:
        assert "start date" in str(exc)
    else:
        raise AssertionError("reversed date range should fail")


def test_native_perp_dex_filter_keeps_only_empty_dex() -> None:
    pa = pytest.importorskip("pyarrow")
    values = pa.array(["", None, "xyz", "flx"])
    assert collector._blank_dex_mask(values).to_pylist() == [True, True, False, False]


def test_wallet_fill_table_marks_side_and_taker_aggression() -> None:
    pa = pytest.importorskip("pyarrow")
    table = pa.table({
        "timestamp": [datetime(2026, 10, 3, tzinfo=timezone.utc)],
        "market": ["BTC-PERP"],
        "buyer": ["0xbuyer"],
        "seller": ["0xseller"],
        "taker_side": ["buy"],
        "notional": [100.0],
        "liquidation": [False],
    })

    buyer = collector._make_fill_table(table, "buy", "2026-10-03")
    seller = collector._make_fill_table(table, "sell", "2026-10-03")

    assert buyer["wallet"].to_pylist() == ["0xbuyer"]
    assert buyer["side"].to_pylist() == ["buy"]
    assert buyer["crossed"].to_pylist() == [True]
    assert seller["wallet"].to_pylist() == ["0xseller"]
    assert seller["side"].to_pylist() == ["sell"]
    assert seller["crossed"].to_pylist() == [False]
