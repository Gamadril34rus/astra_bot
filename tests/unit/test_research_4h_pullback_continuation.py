from __future__ import annotations

import pandas as pd

from scripts.research_4h_pullback_continuation import (
    add_4h_regime_features,
    build_completed_4h_bars,
    choose_4h_pullback_signal,
)


def _bars(count: int = 160) -> pd.DataFrame:
    rows = []
    for i in range(count):
        close = 100.0 + i * 0.05
        rows.append({
            "open_time": i * 30 * 60_000,
            "open": close - 0.1,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": 100.0,
            "quote_volume": 0.0,
        })
    return pd.DataFrame(rows)


def test_4h_aggregation_uses_only_complete_groups_of_eight() -> None:
    frame = _bars(81)
    result = build_completed_4h_bars(frame)
    assert len(result) == 10
    assert result.iloc[-1]["decision_time"] == 22 * 60 * 60_000


def test_4h_context_is_not_available_before_its_close() -> None:
    frame = _bars(160)
    hourly = frame.iloc[::2].copy()
    hourly["decision_time"] = hourly["open_time"] + 60 * 60_000
    merged = add_4h_regime_features(frame, hourly)
    first_4h_close = 4 * 60 * 60_000
    before = merged.loc[merged["decision_time"] == first_4h_close - 30 * 60_000].iloc[0]
    at_close = merged.loc[merged["decision_time"] == first_4h_close].iloc[0]
    assert pd.isna(before["h4_ema20"])
    assert not pd.isna(at_close["h4_ema20"])


def test_long_signal_requires_4h_regime_and_closed_bar_reclaim() -> None:
    frame = _bars()
    frame["atr14"] = 1.0
    frame["ema20"] = 100.0
    frame["ema50"] = 99.0
    frame["volume_ratio"] = 1.5
    frame["h4_ema20"] = 105.0
    frame["h4_ema50"] = 100.0
    frame["h4_ema20_rising"] = True
    frame["h4_ema20_falling"] = False
    frame["h4_adx14"] = 25.0
    frame.loc[148:153, "low"] = 99.5
    frame.loc[154, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.8, 100.5]
    frame.loc[153, "high"] = 100.2

    signal = choose_4h_pullback_signal(frame, 154)

    assert signal is not None
    assert signal["side"] == 1
    assert signal["target_rr"] == 2.0


def test_no_signal_when_4h_regime_is_not_directional() -> None:
    frame = _bars()
    frame["atr14"] = 1.0
    frame["ema20"] = 100.0
    frame["ema50"] = 99.0
    frame["volume_ratio"] = 1.5
    frame["h4_ema20"] = 105.0
    frame["h4_ema50"] = 100.0
    frame["h4_ema20_rising"] = True
    frame["h4_ema20_falling"] = False
    frame["h4_adx14"] = 15.0
    frame.loc[148:153, "low"] = 99.5
    frame.loc[154, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.8, 100.5]
    assert choose_4h_pullback_signal(frame, 154) is None


def test_future_bars_do_not_change_signal_at_closed_index() -> None:
    frame = _bars()
    frame["atr14"] = 1.0
    frame["ema20"] = 100.0
    frame["ema50"] = 99.0
    frame["volume_ratio"] = 1.5
    frame["h4_ema20"] = 105.0
    frame["h4_ema50"] = 100.0
    frame["h4_ema20_rising"] = True
    frame["h4_ema20_falling"] = False
    frame["h4_adx14"] = 25.0
    frame.loc[148:153, "low"] = 99.5
    frame.loc[154, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.8, 100.5]
    before = choose_4h_pullback_signal(frame, 154)
    frame.loc[155:, ["open", "high", "low", "close", "volume"]] = [
        [500.0, 900.0, 1.0, 800.0, 1_000_000.0]
        for _ in range(len(frame) - 155)
    ]
    after = choose_4h_pullback_signal(frame, 154)
    assert before == after



def test_relative_volume_baseline_excludes_current_bar() -> None:
    frame = _bars(60)
    hourly = frame.iloc[::2].copy()
    hourly["decision_time"] = hourly["open_time"] + 60 * 60_000
    frame.loc[40, "volume"] = 500.0
    enriched = add_4h_regime_features(frame, hourly)
    expected = 500.0 / 100.0
    assert enriched.loc[40, "volume_ratio"] == expected
