"""Tests for the research-only 15m residual screen."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research.screen_residual_reversion_15m import (
    add_closed_h1_context,
    funding_cost_pct,
    simulate_period,
)

import pytest


def _ohlc(open_times: list[int], interval_ms: int) -> pd.DataFrame:
    close = np.linspace(100.0, 101.0, len(open_times))
    return pd.DataFrame({
        "open_time": open_times,
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 10.0,
        "decision_time": np.asarray(open_times, dtype=np.int64) + interval_ms,
    })


def test_15m_join_uses_only_last_fully_closed_1h_bar() -> None:
    m15 = _ohlc([0, 900_000, 1_800_000, 2_700_000, 3_600_000, 4_500_000], 900_000)
    h1 = _ohlc([0, 3_600_000], 3_600_000)
    joined = add_closed_h1_context(m15, h1)

    # Decisions before 01:00 UTC cannot see the 00:00–01:00 hourly candle.
    assert joined.loc[:2, "h1_closed_at"].isna().all()
    assert joined.loc[3, "h1_closed_at"] == 3_600_000
    assert joined.loc[4, "h1_closed_at"] == 3_600_000
    valid = joined.h1_closed_at.notna()
    assert (joined.loc[valid, "h1_closed_at"] <= joined.loc[valid, "decision_time"]).all()


def test_funding_sign_is_side_correct() -> None:
    funding = pd.DataFrame({
        "funding_time": [100, 200],
        "rate": [0.0001, -0.0002],
    })
    assert funding_cost_pct(funding, 0, 300, 1) == pytest.approx(-0.01)
    assert funding_cost_pct(funding, 0, 300, -1) == pytest.approx(0.01)


def test_simulator_does_not_open_overlapping_positions_per_symbol() -> None:
    n = 160
    frame = _ohlc([i * 900_000 for i in range(n)], 900_000)
    frame["resid_sum6"] = 0.0
    frame["clv"] = 0.5
    frame["atr14"] = 1.0
    frame["atr_ratio"] = 1.0
    frame["ret1"] = 0.0
    frame["h1_adx14"] = 10.0
    frame["h1_ema20"] = 101.0
    frame["h1_ema50"] = 100.0
    frame["h1_ema20_rising"] = True
    frame["h1_ema20_falling"] = False
    frame.loc[[120, 121], "resid_sum6"] = -0.04
    frame.loc[[120, 121], "clv"] = 0.60
    funding = pd.DataFrame({"funding_time": pd.Series(dtype="int64"),
                            "rate": pd.Series(dtype="float64")})

    trades, _ = simulate_period(
        "HYPEUSDT", frame, funding,
        start_ms=121 * 900_000,
        end_ms=159 * 900_000,
        mode="adx_range",
    )
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "no_progress_30m"
    assert trades[0]["hold_min"] == 30.0
