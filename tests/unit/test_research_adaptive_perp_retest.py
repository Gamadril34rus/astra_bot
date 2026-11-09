from __future__ import annotations

import pandas as pd

from scripts.research_adaptive_perp_retest import choose_retest_breakout_signal


def _frame() -> pd.DataFrame:
    rows = []
    for _ in range(30):
        rows.append({
            "open": 99.0,
            "high": 100.0,
            "low": 98.0,
            "close": 99.0,
            "volume": 100.0,
            "quote_volume": 100.0,
            "atr14": 1.0,
            "h1_adx14": 25.0,
            "h1_ema20": 105.0,
            "h1_ema50": 100.0,
            "h1_ema20_rising": True,
            "h1_ema20_falling": False,
        })
    return pd.DataFrame(rows)


def test_long_breakout_requires_a_held_retest_and_enters_next_bar() -> None:
    frame = _frame()
    frame.loc[25, ["open", "high", "low", "close", "quote_volume"]] = [
        100.0, 101.5, 99.8, 101.0, 200.0
    ]
    frame.loc[26, ["open", "high", "low", "close"]] = [
        100.2, 101.0, 99.9, 100.4
    ]

    signal = choose_retest_breakout_signal(frame, 26)

    assert signal is not None
    assert signal["family"] == "RETEST_BREAKOUT"
    assert signal["side"] == 1
    assert signal["stop"] == 99.75
    assert signal["target_rr"] == 2.0


def test_long_breakout_is_rejected_if_level_was_lost_before_retest() -> None:
    frame = _frame()
    frame.loc[24, ["open", "high", "low", "close", "quote_volume"]] = [
        100.0, 101.5, 99.8, 101.0, 200.0
    ]
    frame.loc[25, "close"] = 99.5
    frame.loc[26, ["open", "high", "low", "close"]] = [
        100.2, 101.0, 99.9, 100.4
    ]

    signal = choose_retest_breakout_signal(frame, 26)

    assert signal is None


def test_retest_breakout_requires_higher_timeframe_direction() -> None:
    frame = _frame()
    frame.loc[25, ["open", "high", "low", "close", "quote_volume"]] = [
        100.0, 101.5, 99.8, 101.0, 200.0
    ]
    frame.loc[26, ["open", "high", "low", "close"]] = [
        100.2, 101.0, 99.9, 100.4
    ]
    frame.loc[26, "h1_ema20"] = 99.0
    frame.loc[26, "h1_ema50"] = 100.0

    signal = choose_retest_breakout_signal(frame, 26)

    assert signal is None



def test_short_breakout_uses_symmetric_retest_rule() -> None:
    frame = _frame()
    frame.loc[:24, ["open", "high", "low", "close"]] = [
        101.0, 102.0, 100.0, 101.0
    ]
    frame.loc[25, ["open", "high", "low", "close", "quote_volume"]] = [
        100.0, 100.2, 98.5, 99.0, 200.0
    ]
    frame.loc[26, ["open", "high", "low", "close"]] = [
        99.8, 100.1, 98.9, 99.5
    ]
    frame.loc[26, "h1_ema20"] = 95.0
    frame.loc[26, "h1_ema50"] = 100.0
    frame.loc[26, "h1_ema20_rising"] = False
    frame.loc[26, "h1_ema20_falling"] = True

    signal = choose_retest_breakout_signal(frame, 26)

    assert signal is not None
    assert signal["side"] == -1
    assert signal["stop"] == 100.25
    assert signal["target_rr"] == 2.0
