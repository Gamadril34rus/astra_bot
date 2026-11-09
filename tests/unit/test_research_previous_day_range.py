from __future__ import annotations

import numpy as np
import pandas as pd
from scripts.research_previous_day_range import (
    add_previous_day_levels,
    choose_previous_day_breakout_signal,
)


def _signal_frame() -> pd.DataFrame:
    rows = []
    for index in range(80):
        rows.append({
            "open_time": index * 30 * 60_000,
            "open": 99.0,
            "high": 100.0,
            "low": 98.0,
            "close": 99.0,
            "volume": 100.0,
            "quote_volume": 0.0,
            "atr14": 1.0,
            "h1_adx14": 25.0,
            "h1_ema20": 105.0,
            "h1_ema50": 100.0,
            "h1_ema20_rising": True,
            "h1_ema20_falling": False,
            "previous_day_high": 100.0,
            "previous_day_low": 90.0,
            "_utc_day_bar_index": index % 48,
        })
    return pd.DataFrame(rows)


def test_prior_day_levels_are_shifted_and_never_include_current_day() -> None:
    interval = 30 * 60_000
    rows = []
    for day in range(3):
        for bar in range(48):
            rows.append({
                "open_time": (day * 48 + bar) * interval,
                "open": 100.0,
                "high": 101.0 + day,
                "low": 99.0 - day,
                "close": 100.0,
                "volume": 10.0,
            })
    result = add_previous_day_levels(pd.DataFrame(rows))

    assert result.loc[48, "previous_day_high"] == 101.0
    assert result.loc[48, "previous_day_low"] == 99.0
    assert result.loc[96, "previous_day_high"] == 102.0
    assert result.loc[96, "previous_day_low"] == 98.0
    assert np.isnan(result.loc[0, "previous_day_high"])


def test_long_first_break_uses_volume_fallback_and_structural_stop() -> None:
    frame = _signal_frame()
    frame.loc[49, "close"] = 99.0
    frame.loc[50, ["open", "high", "low", "close", "volume"]] = [
        100.2, 102.0, 98.8, 101.5, 200.0
    ]
    frame.loc[46:49, "low"] = [98.8, 98.6, 98.5, 98.7]

    signal = choose_previous_day_breakout_signal(frame, 50)

    assert signal is not None
    assert signal["side"] == 1
    assert signal["family"] == "PREVIOUS_DAY_RANGE_BREAKOUT"
    assert signal["stop"] == 98.35
    assert signal["target_rr"] == 2.5


def test_short_first_break_is_symmetric() -> None:
    frame = _signal_frame()
    frame.loc[50, ["open", "high", "low", "close", "volume"]] = [
        89.8, 91.2, 88.0, 88.5, 200.0
    ]
    frame.loc[46:49, "high"] = [101.0, 101.2, 101.5, 101.3]
    frame.loc[50, "h1_ema20"] = 95.0
    frame.loc[50, "h1_ema50"] = 100.0
    frame.loc[50, "h1_ema20_rising"] = False
    frame.loc[50, "h1_ema20_falling"] = True

    signal = choose_previous_day_breakout_signal(frame, 50)

    assert signal is not None
    assert signal["side"] == -1
    assert signal["stop"] == 101.65
    assert signal["target_rr"] == 2.5


def test_only_first_breakout_of_day_is_eligible() -> None:
    frame = _signal_frame()
    frame.loc[48, "close"] = 101.0
    frame.loc[49, "close"] = 99.0
    frame.loc[50, ["open", "high", "low", "close", "volume"]] = [
        100.2, 102.0, 98.8, 101.5, 200.0
    ]

    assert choose_previous_day_breakout_signal(frame, 50) is None


def test_no_breakout_if_previous_close_is_already_outside_level() -> None:
    frame = _signal_frame()
    frame.loc[49, "close"] = 100.5
    frame.loc[50, ["open", "high", "low", "close", "volume"]] = [
        100.2, 102.0, 98.8, 101.5, 200.0
    ]

    assert choose_previous_day_breakout_signal(frame, 50) is None


def test_no_signal_when_hourly_regime_disagrees() -> None:
    frame = _signal_frame()
    frame.loc[50, ["open", "high", "low", "close", "volume"]] = [
        100.2, 102.0, 98.8, 101.5, 200.0
    ]
    frame.loc[50, "h1_ema20"] = 99.0
    frame.loc[50, "h1_ema50"] = 100.0

    assert choose_previous_day_breakout_signal(frame, 50) is None


def test_future_candles_do_not_change_prior_day_levels_or_signal() -> None:
    frame = _signal_frame()
    frame.loc[50, ["open", "high", "low", "close", "volume"]] = [
        100.2, 102.0, 98.8, 101.5, 200.0
    ]
    frame.loc[50, "_utc_day_bar_index"] = 2
    before = choose_previous_day_breakout_signal(frame, 50)
    frame.loc[51:, ["open", "high", "low", "close", "volume"]] = [
        [500.0, 999.0, 1.0, 900.0, 1_000_000.0]
        for _ in range(len(frame) - 51)
    ]
    after = choose_previous_day_breakout_signal(frame, 50)
    assert before == after
