from __future__ import annotations

import pytest

from scripts.research.clt_event_model import detect_clt_events, inventory_deltas


def _bars(*, crowd_direction: str) -> list[dict]:
    rows = []
    for index in range(10):
        price = 100.0 - 0.05 * index if crowd_direction == "long" else 100.0 + 0.05 * index
        rows.append({
            "ts": index,
            "open": price,
            "high": price + 0.4,
            "low": price - 0.4,
            "close": price,
            "crowd_long_delta_usd": 0.0,
            "crowd_short_delta_usd": 0.0,
            "large_long_delta_usd": 0.0,
            "large_short_delta_usd": 0.0,
        })

    if crowd_direction == "long":
        rows[1]["crowd_long_delta_usd"] = 120_000
        rows[2]["large_short_delta_usd"] = 90_000
        rows[3]["crowd_long_delta_usd"] = 60_000
        rows[8]["close"] = 98.0
        rows[8]["low"] = 97.8
        rows[9]["open"] = 98.1
    else:
        rows[1]["crowd_short_delta_usd"] = 120_000
        rows[2]["large_long_delta_usd"] = 90_000
        rows[3]["crowd_short_delta_usd"] = 60_000
        rows[8]["close"] = 102.0
        rows[8]["high"] = 102.2
        rows[9]["open"] = 101.9
    return rows


def test_inventory_deltas_distinguish_open_from_close():
    long_delta, short_delta = inventory_deltas({
        "open_long_usd": 150,
        "close_long_usd": 40,
        "open_short_usd": 90,
        "close_short_usd": 20,
    })
    assert long_delta == 110
    assert short_delta == 70


def test_inventory_deltas_refuse_missing_direction_data():
    with pytest.raises(ValueError, match="explicit direction"):
        inventory_deltas({"open_long_usd": 100})


def test_short_event_requires_long_crowd_and_short_large_add_then_break():
    events = detect_clt_events(
        _bars(crowd_direction="long"),
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_pct=0.1,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert len(events) == 1
    assert events[0]["setup"] == "crowd_long_trap"
    assert events[0]["direction"] == -1
    assert events[0]["entry_index"] == 9
    assert events[0]["entry_price"] == 98.1


def test_long_event_is_mirror_of_short_event():
    events = detect_clt_events(
        _bars(crowd_direction="short"),
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_pct=0.1,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert len(events) == 1
    assert events[0]["setup"] == "crowd_short_trap"
    assert events[0]["direction"] == 1
    assert events[0]["entry_index"] == 9


def test_no_entry_without_next_bar():
    rows = _bars(crowd_direction="long")[:-1]
    events = detect_clt_events(
        rows,
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_pct=0.1,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert events == []
