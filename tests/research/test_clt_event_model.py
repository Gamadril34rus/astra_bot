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
            "small_long_add_wallets": 0,
            "small_short_add_wallets": 0,
            "large_long_add_wallets": 0,
            "large_short_add_wallets": 0,
            "prior_volatility_pct": 0.2,
            "htf_context_ready": True,
            "htf_context_ts": index,
            "htf_context_age_ms": 1,
            "htf_return_pct": 0.25,
            "htf_range_pct": 0.8,
            "htf_structure_break": 0,
            "htf_close_location": 0.5,
        })
        for cohort in ("small", "large"):
            for direction in ("long", "short"):
                for window in range(1, 13)
                    rows[-1][f"{cohort}_{direction}_add_wallets_prev_{window}"] = 0

    if crowd_direction == "long":
        rows[1]["crowd_long_delta_usd"] = 120_000
        rows[1]["small_long_add_wallets"] = 3
        rows[2]["large_short_delta_usd"] = 90_000
        rows[2]["large_short_add_wallets"] = 1
        rows[2]["small_long_add_wallets_prev_1"] = 3
        rows[3]["crowd_long_delta_usd"] = 60_000
        rows[8]["small_long_add_wallets_prev_5"] = 3
        rows[3]["small_long_add_wallets"] = 3
        rows[8]["close"] = 98.0
        rows[8]["low"] = 97.8
        rows[9]["open"] = 98.1
    else:
        rows[1]["crowd_short_delta_usd"] = 120_000
        rows[1]["small_short_add_wallets"] = 3
        rows[2]["large_long_delta_usd"] = 90_000
        rows[2]["large_long_add_wallets"] = 1
        rows[2]["small_short_add_wallets_prev_1"] = 3
        rows[3]["crowd_short_delta_usd"] = 60_000
        rows[8]["small_short_add_wallets_prev_5"] = 3
        rows[3]["small_short_add_wallets"] = 3
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
        max_crowd_progress_z=0.8,
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
    assert events[0]["crowd_start_ts"] == 1
    assert events[0]["large_add_start_ts"] == 2
    assert events[0]["crowd_to_large_bars"] == 1
    assert events[0]["large_to_break_bars"] == 6
    assert events[0]["continued_bars"] == 5


def test_long_event_is_mirror_of_short_event():
    events = detect_clt_events(
        _bars(crowd_direction="short"),
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
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
        max_crowd_progress_z=0.8,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert events == []


def test_crowd_must_continue_after_whale_add_not_in_same_bar():
    rows = _bars(crowd_direction="long")
    rows[3]["crowd_long_delta_usd"] = 0.0
    events = detect_clt_events(
        rows,
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert events == []


def test_whale_add_must_follow_crowd_within_max_lag():
    rows = _bars(crowd_direction="long")
    rows[2]["large_short_delta_usd"] = 0.0
    rows[3]["crowd_long_delta_usd"] = 0.0
    rows[3]["small_long_add_wallets"] = 0
    rows[4]["large_short_delta_usd"] = 90_000
    rows[4]["large_short_add_wallets"] = 1
    rows[5]["crowd_long_delta_usd"] = 60_000
    rows[5]["small_long_add_wallets"] = 3
    events = detect_clt_events(
        rows,
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
        crowd_window_bars=8,
        max_lag_bars=2,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert events == []



def test_crowd_build_accumulates_across_bars_before_large_opposite_add():
    rows = _bars(crowd_direction="long")
    rows[1]["crowd_long_delta_usd"] = 60_000
    rows[1]["small_long_add_wallets"] = 3
    rows[2]["crowd_long_delta_usd"] = 60_000
    rows[2]["small_long_add_wallets"] = 3
    rows[2]["large_short_delta_usd"] = 0.0
    rows[2]["large_short_add_wallets"] = 0
    rows[3]["small_long_add_wallets_prev_2"] = 3
    rows[8]["small_long_add_wallets_prev_4"] = 3
    rows[3]["large_short_delta_usd"] = 90_000
    rows[3]["large_short_add_wallets"] = 1
    rows[4]["crowd_long_delta_usd"] = 60_000
    rows[4]["small_long_add_wallets"] = 3
    events = detect_clt_events(
        rows,
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert len(events) == 1
    assert events[0]["crowd_build_usd"] == 120_000



def test_one_small_wallet_is_not_a_crowd():
    rows = _bars(crowd_direction="long")
    rows[1]["small_long_add_wallets"] = 1
    rows[2]["small_long_add_wallets_prev_1"] = 1
    rows[3]["small_long_add_wallets"] = 1
    rows[8]["small_long_add_wallets_prev_5"] = 1
    events = detect_clt_events(
        rows,
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert events == []



def test_clt_event_carries_closed_1h_context_for_regime_analysis():
    events = detect_clt_events(
        _bars(crowd_direction="long"),
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert len(events) == 1
    assert events[0]["htf_context_ts"] == 8
    assert events[0]["htf_return_pct"] == 0.25
    assert events[0]["htf_structure_break"] == 0


def test_clt_event_is_not_emitted_when_closed_1h_context_is_unavailable():
    rows = _bars(crowd_direction="long")
    rows[8]["htf_context_ready"] = False
    rows[8]["htf_context_ts"] = None
    rows[8]["htf_context_age_ms"] = None
    rows[8]["htf_return_pct"] = None
    rows[8]["htf_range_pct"] = None
    rows[8]["htf_structure_break"] = None
    rows[8]["htf_close_location"] = None
    events = detect_clt_events(
        rows,
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
        crowd_window_bars=8,
        max_lag_bars=8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert events == []



def test_structure_break_must_follow_large_add_within_confirmation_window():
    events = detect_clt_events(
        _bars(crowd_direction="long"),
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
        crowd_window_bars=8,
        max_lag_bars=8,
        max_confirmation_bars=4,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert events == []



def test_default_clt_windows_cover_three_hour_stages():
    events = detect_clt_events(
        _bars(crowd_direction="long"),
        min_crowd_add_usd=100_000,
        min_large_add_usd=80_000,
        min_continued_crowd_add_usd=50_000,
        max_crowd_progress_z=0.8,
        swing_lookback_bars=8,
        cooldown_bars=36,
    )
    assert len(events) == 1
    assert events[0]["crowd_to_large_bars"] == 1
    assert events[0]["large_to_break_bars"] == 6
