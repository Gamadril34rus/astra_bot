from __future__ import annotations

import pandas as pd

from scripts.research_15m_pullback_continuation import (
    add_15m_features,
    choose_15m_pullback_signal,
)


def _bars(count: int = 200, interval_ms: int = 15 * 60_000) -> pd.DataFrame:
    rows = []
    for index in range(count):
        close = 100.0 + index * 0.05
        open_time = index * interval_ms
        rows.append({
            "open_time": open_time,
            "decision_time": open_time + interval_ms,
            "open": close - 0.1,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": 100.0,
            "quote_volume": 0.0,
        })
    return pd.DataFrame(rows)


def _with_long_regime(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["atr14"] = 1.0
    frame["ema20"] = 100.0
    frame["ema50"] = 99.0
    frame["volume_ratio"] = 1.5
    frame["h1_ema20"] = 105.0
    frame["h1_ema50"] = 100.0
    frame["h1_ema20_rising"] = True
    frame["h1_ema20_falling"] = False
    frame["h1_adx14"] = 25.0
    frame.loc[150:153, "low"] = 99.5
    frame.loc[154, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.8, 100.5]
    frame.loc[153, "high"] = 100.2
    return frame


def test_long_pullback_requires_closed_1h_regime_and_15m_reclaim() -> None:
    frame = _with_long_regime(_bars())
    signal = choose_15m_pullback_signal(frame, 154)
    assert signal is not None
    assert signal["family"] == "15M_PULLBACK_CONTINUATION"
    assert signal["side"] == 1
    assert signal["target_rr"] == 1.5


def test_short_pullback_is_directionally_symmetric() -> None:
    frame = _bars()
    frame["atr14"] = 1.0
    frame["ema20"] = 100.0
    frame["ema50"] = 101.0
    frame["volume_ratio"] = 1.5
    frame["h1_ema20"] = 95.0
    frame["h1_ema50"] = 100.0
    frame["h1_ema20_rising"] = False
    frame["h1_ema20_falling"] = True
    frame["h1_adx14"] = 25.0
    frame.loc[150:153, "high"] = 100.5
    frame.loc[150:153, "close"] = 100.5
    frame.loc[154, ["open", "high", "low", "close"]] = [100.0, 100.2, 99.0, 99.5]
    frame.loc[153, "low"] = 99.8
    signal = choose_15m_pullback_signal(frame, 154)
    assert signal is not None
    assert signal["side"] == -1


def test_no_signal_when_hourly_regime_is_weak() -> None:
    frame = _with_long_regime(_bars())
    frame.loc[154, "h1_adx14"] = 15.0
    assert choose_15m_pullback_signal(frame, 154) is None


def test_no_signal_when_hourly_direction_does_not_confirm() -> None:
    frame = _with_long_regime(_bars())
    frame.loc[154, "h1_ema20_rising"] = False
    assert choose_15m_pullback_signal(frame, 154) is None


def test_future_bars_do_not_change_a_closed_15m_signal() -> None:
    frame = _with_long_regime(_bars())
    before = choose_15m_pullback_signal(frame, 154)
    frame.loc[155:, ["open", "high", "low", "close", "volume"]] = [
        [500.0, 900.0, 1.0, 800.0, 1_000_000.0]
        for _ in range(len(frame) - 155)
    ]
    after = choose_15m_pullback_signal(frame, 154)
    assert before == after


def test_1h_context_is_not_visible_before_its_close() -> None:
    signal_bars = _bars(8)
    hourly = _bars(3, interval_ms=60 * 60_000)
    enriched = add_15m_features(signal_bars, hourly)
    before_first_hour_close = enriched.loc[enriched["decision_time"] == 45 * 60_000].iloc[0]
    at_first_hour_close = enriched.loc[enriched["decision_time"] == 60 * 60_000].iloc[0]
    assert pd.isna(before_first_hour_close["h1_ema20"])
    assert pd.notna(at_first_hour_close["h1_ema20"])


def test_relative_volume_baseline_excludes_current_signal_bar() -> None:
    signal_bars = _bars(60)
    hourly = _bars(20, interval_ms=60 * 60_000)
    signal_bars.loc[40, "volume"] = 500.0
    enriched = add_15m_features(signal_bars, hourly)
    assert enriched.loc[40, "volume_ratio"] == 5.0
