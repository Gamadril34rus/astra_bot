from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research.quarter_hour_boundary import (
    INTERVAL_MS,
    ROUND_TRIP_COST_BPS,
    STRESS_ROUND_TRIP_COST_BPS,
    build_events,
    event_outcomes,
    validate_config,
    _split_for_interval,
)


def _bars(count: int = 3_200) -> pd.DataFrame:
    start = pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000
    rows = []
    price = 100.0
    for index in range(count):
        open_price = price
        close_price = open_price * 1.0001
        volume = 10.0
        if index == 3_000:
            close_price = open_price * 0.995
            volume = 30.0
        rows.append(
            {
                "open_time": start + index * INTERVAL_MS,
                "open": open_price,
                "high": max(open_price, close_price) * 1.0002,
                "low": min(open_price, close_price) * 0.9998,
                "close": close_price,
                "volume": volume,
                "symbol": "BTC-USDT",
            }
        )
        price = close_price
    return pd.DataFrame(rows)


def test_boundary_event_is_selected_and_faded_against_bullish_trend() -> None:
    frame = _bars()
    events = build_events(frame)
    selected = events.loc[events["open_time"] == frame.iloc[3_000]["open_time"]]
    assert len(selected) == 1
    event = selected.iloc[0]
    assert event["variant"] == "REVERSAL"
    assert event["side"] == 1
    assert event["impulse"] == -1


def test_event_study_enters_next_bar_and_charges_round_trip_cost() -> None:
    frame = _bars(80)
    frame["symbol"] = "BTC-USDT"
    event_time = int(frame.iloc[2]["open_time"])
    events = pd.DataFrame(
        [
            {
                "open_time": event_time,
                "decision_time": pd.to_datetime(event_time + INTERVAL_MS, unit="ms", utc=True),
                "symbol": "BTC-USDT",
                "variant": "CONTINUATION",
                "side": 1,
            }
        ]
    )
    entry_index = 3
    exit_index = entry_index + 6 - 1
    frame.loc[entry_index, "open"] = 100.0
    frame.loc[exit_index, "close"] = 101.0
    funding = pd.DataFrame(
        {
            "funding_time_ms": [event_time, int(frame.iloc[-1]["open_time"]) + INTERVAL_MS],
            "funding_rate": [0.0, 0.0],
        }
    )

    outcomes = event_outcomes(frame, events, funding)
    row = outcomes.loc[outcomes["horizon"] == "30m"].iloc[0]
    assert np.isclose(row["gross_bps"], 100.0)
    assert np.isclose(
        row["cost_only_net_bps"], 100.0 - ROUND_TRIP_COST_BPS
    )
    assert np.isclose(row["stress_net_bps"], 100.0 - STRESS_ROUND_TRIP_COST_BPS)
    assert bool(row["funding_complete"])


def test_missing_funding_coverage_is_unknown_not_zero() -> None:
    frame = _bars(80)
    event_time = int(frame.iloc[2]["open_time"])
    events = pd.DataFrame(
        [
            {
                "open_time": event_time,
                "decision_time": pd.to_datetime(event_time + INTERVAL_MS, unit="ms", utc=True),
                "symbol": "BTC-USDT",
                "variant": "REVERSAL",
                "side": -1,
            }
        ]
    )
    funding = pd.DataFrame(columns=["funding_time_ms", "funding_rate"])
    outcomes = event_outcomes(frame, events, funding)
    assert outcomes["funded_net_bps"].isna().all()
    assert not outcomes["funding_complete"].any()


def test_checked_in_config_matches_frozen_runner() -> None:
    config = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "research"
        / "quarter_hour_boundary.json"
    )
    validate_config(config)


def test_outcomes_crossing_split_boundary_are_purged() -> None:
    decision = pd.Timestamp("2025-12-31T23:50:00Z")
    exit_inside_next_split = pd.Timestamp("2026-01-01T00:20:00Z")
    assert _split_for_interval(decision, exit_inside_next_split) is None
    assert _split_for_interval(
        pd.Timestamp("2025-12-31T18:00:00Z"),
        pd.Timestamp("2025-12-31T22:00:00Z"),
    ) == "IS"
