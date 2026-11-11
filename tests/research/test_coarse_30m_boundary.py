from __future__ import annotations

import numpy as np
import pandas as pd
from scripts.research.run_coarse_30m_boundary import (
    BAR_MS,
    build_events,
    event_outcomes,
)
from scripts.research.quarter_hour_boundary import (
    ROUND_TRIP_COST_BPS,
    STRESS_ROUND_TRIP_COST_BPS,
)


def _bars(count: int = 1_000) -> pd.DataFrame:
    start = pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000
    rows = []
    price = 100.0
    for index in range(count):
        open_price = price
        close_price = open_price * 1.0001
        volume = 10.0
        if index == 800:
            close_price = open_price * 0.995
            volume = 30.0
        rows.append(
            {
                "open_time": start + index * BAR_MS,
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


def test_coarse_event_uses_only_completed_4h_trend() -> None:
    frame = _bars()
    events = build_events(frame)
    selected = events.loc[events["open_time"] == frame.iloc[800]["open_time"]]
    assert len(selected) == 1
    event = selected.iloc[0]
    assert event["variant"] == "REVERSAL"
    assert event["side"] == 1


def test_coarse_event_enters_next_30m_open_and_charges_cost() -> None:
    frame = _bars(80)
    event_time = int(frame.iloc[2]["open_time"])
    events = pd.DataFrame(
        [
            {
                "open_time": event_time,
                "decision_time": pd.to_datetime(event_time + BAR_MS, unit="ms", utc=True),
                "symbol": "BTC-USDT",
                "variant": "CONTINUATION",
                "side": 1,
            }
        ]
    )
    frame.loc[3, "open"] = 100.0
    frame.loc[3, "close"] = 101.0
    funding = pd.DataFrame(
        {
            "funding_time_ms": [
                event_time,
                int(frame.iloc[-1]["open_time"]) + BAR_MS,
            ],
            "funding_rate": [0.0, 0.0],
        }
    )
    outcomes = event_outcomes(frame, events, funding)
    row = outcomes.loc[outcomes["horizon"] == "30m"].iloc[0]
    assert np.isclose(row["gross_bps"], 100.0)
    assert np.isclose(
        row["cost_only_net_bps"], 100.0 - ROUND_TRIP_COST_BPS["BTC-USDT"]
    )
    assert bool(row["funding_complete"])


def test_coarse_altcoin_cost_proxy_is_higher() -> None:
    assert ROUND_TRIP_COST_BPS["BTC-USDT"] == 12.0
    assert ROUND_TRIP_COST_BPS["SOL-USDT"] == 18.0
    assert STRESS_ROUND_TRIP_COST_BPS["SOL-USDT"] == 27.0
