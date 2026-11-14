from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scripts import research_adaptive_perp_portfolio as core
from scripts.research_bingx_session_vwap import (
    _assert_same_population,
    _oos_gate,
    _period_signal,
    _screen_passes,
    add_session_features,
    choose_session_vwap_signal,
)


def _bars(times: list[int], interval_ms: int) -> pd.DataFrame:
    values = np.arange(len(times), dtype=float)
    frame = pd.DataFrame({
        "open_time": times,
        "open": 100.0 + values * 0.01,
        "high": 102.0 + values * 0.01,
        "low": 99.0 + values * 0.01,
        "close": 101.0 + values * 0.01,
        "volume": 10.0 + values,
        "quote_volume": np.zeros(len(times)),
    })
    frame["decision_time"] = frame["open_time"] + interval_ms
    return frame


def _signal_frame() -> pd.DataFrame:
    count = 140
    frame = pd.DataFrame({
        "open_time": np.arange(count, dtype=np.int64) * 900_000,
        "decision_time": (np.arange(count, dtype=np.int64) + 1) * 900_000,
        "open": np.full(count, 100.0),
        "high": np.full(count, 101.0),
        "low": np.full(count, 99.0),
        "close": np.full(count, 100.0),
        "atr14": np.full(count, 1.0),
        "h1_adx14": np.full(count, 15.0),
        "session_vwap": np.full(count, 100.0),
        "vwap_deviation_atr": np.zeros(count),
        "volume_ratio_prior": np.full(count, 1.2),
        "session_id": ["2026-01-01"] * count,
    })
    return frame


def test_session_vwap_and_volume_baseline_are_causal() -> None:
    interval = 900_000
    m15 = _bars([i * interval for i in range(30)], interval)
    h1 = _bars([i * 3_600_000 for i in range(30)], 3_600_000)
    full = add_session_features(m15, h1)
    truncated = add_session_features(m15.iloc[:22].copy(), h1)
    pd.testing.assert_series_equal(
        full.loc[:20, "session_vwap"].reset_index(drop=True),
        truncated.loc[:20, "session_vwap"].reset_index(drop=True),
    )
    prior_mean = m15["volume"].iloc[:20].mean()
    assert full.loc[20, "volume_ratio_prior"] == pytest.approx(
        m15.loc[20, "volume"] / prior_mean
    )
    expected_vwap = (
        ((m15["high"].iloc[:21] + m15["low"].iloc[:21] + m15["close"].iloc[:21]) / 3)
        * m15["volume"].iloc[:21]
    ).sum() / m15["volume"].iloc[:21].sum()
    assert full.loc[20, "session_vwap"] == pytest.approx(expected_vwap)


def test_15m_bar_uses_only_last_fully_closed_1h_context() -> None:
    m15 = _bars([i * 900_000 for i in range(48)], 900_000)
    h1 = _bars([i * 3_600_000 for i in range(30)], 3_600_000)
    merged = core.add_features(m15, h1)
    target = merged.loc[merged["decision_time"] == 10 * 3_600_000 + 45 * 60_000].iloc[0]
    hourly = h1.copy()
    hourly["ema20"] = hourly["close"].ewm(span=20, adjust=False).mean()
    expected = hourly.loc[hourly["decision_time"] <= 10 * 3_600_000, "ema20"].iloc[-1]
    assert target["h1_ema20"] == pytest.approx(expected)


def test_session_vwap_long_reclaim_signal() -> None:
    frame = _signal_frame()
    i = 120
    frame.loc[i - 1, "vwap_deviation_atr"] = -1.5
    frame.loc[i, "vwap_deviation_atr"] = -0.5
    frame.loc[i, "open"] = 99.0
    frame.loc[i, "close"] = 99.5
    frame.loc[i - 2 : i, "low"] = [98.8, 98.9, 99.0]
    signal = choose_session_vwap_signal(frame, i)
    assert signal is not None
    assert signal["family"] == "SESSION_VWAP_RECLAIM"
    assert signal["side"] == 1
    assert signal["stop"] == pytest.approx(98.65)
    assert signal["target_price"] == 100.0


def test_session_vwap_short_reclaim_signal() -> None:
    frame = _signal_frame()
    i = 120
    frame.loc[i - 1, "vwap_deviation_atr"] = 1.5
    frame.loc[i, "vwap_deviation_atr"] = 0.5
    frame.loc[i, "open"] = 101.0
    frame.loc[i, "close"] = 100.5
    frame.loc[i - 2 : i, "high"] = [101.0, 101.1, 101.2]
    signal = choose_session_vwap_signal(frame, i)
    assert signal is not None
    assert signal["family"] == "SESSION_VWAP_RECLAIM"
    assert signal["side"] == -1
    assert signal["stop"] == pytest.approx(101.35)
    assert signal["target_price"] == 100.0


@pytest.mark.parametrize("boundary", ["previous", "next", "swing"])
def test_session_vwap_signal_does_not_cross_utc_session_boundary(boundary: str) -> None:
    frame = _signal_frame()
    i = 120
    frame.loc[i - 1, "vwap_deviation_atr"] = -1.5
    frame.loc[i, "vwap_deviation_atr"] = -0.5
    frame.loc[i, "open"] = 99.0
    frame.loc[i, "close"] = 99.5
    if boundary == "previous":
        frame.loc[i - 1, "session_id"] = "2025-12-31"
    elif boundary == "next":
        frame.loc[i + 1, "session_id"] = "2026-01-02"
    else:
        frame.loc[i - 2, "session_id"] = "2025-12-31"
    assert choose_session_vwap_signal(frame, i) is None


def test_session_vwap_rejects_trend_or_sub_average_volume() -> None:
    frame = _signal_frame()
    i = 120
    frame.loc[i - 1, "vwap_deviation_atr"] = -1.5
    frame.loc[i, "vwap_deviation_atr"] = -0.5
    frame.loc[i, "open"] = 99.0
    frame.loc[i, "close"] = 99.5
    frame.loc[i, "h1_adx14"] = 21.0
    assert choose_session_vwap_signal(frame, i) is None
    frame.loc[i, "h1_adx14"] = 15.0
    frame.loc[i, "volume_ratio_prior"] = 0.99
    assert choose_session_vwap_signal(frame, i) is None


def test_period_signal_blocks_entries_outside_split() -> None:
    frame = _signal_frame()
    i = 120
    frame.loc[i - 1, "vwap_deviation_atr"] = -1.5
    frame.loc[i, "vwap_deviation_atr"] = -0.5
    frame.loc[i, "open"] = 99.0
    frame.loc[i, "close"] = 99.5
    picker = _period_signal(choose_session_vwap_signal, 121 * 900_000, 130 * 900_000)
    assert picker(frame, i) is not None
    assert picker(frame, i - 1) is None


def test_is_validation_screen_requires_both_cost_cases_and_periods() -> None:
    good = {"trades": 30, "pf": 1.1, "net_expectancy_pct": 0.02}
    screen = {
        "base": {"IS": good, "VALIDATION": good},
        "stress_1_5x": {"IS": good, "VALIDATION": good},
    }
    assert _screen_passes(screen) == (True, [])
    bad = {"trades": 30, "pf": 0.99, "net_expectancy_pct": 0.02}
    screen["stress_1_5x"]["VALIDATION"] = bad
    passed, failures = _screen_passes(screen)
    assert not passed
    assert "stress_1_5x.VALIDATION:net_pf<1.0" in failures


def test_oos_gate_requires_100_trades_1_25_pf_months_and_stress() -> None:
    base = {
        "trades": 120,
        "pf": 1.3,
        "net_expectancy_pct": 0.03,
        "positive_month_share_pct": 75.0,
        "realized_equal_notional_drawdown_pct": -8.0,
        "trade_sequence_drawdown_pct": -15.0,
    }
    stress = {
        "trades": 120,
        "pf": 1.05,
        "net_expectancy_pct": 0.005,
        "positive_month_share_pct": 75.0,
        "realized_equal_notional_drawdown_pct": -9.0,
        "trade_sequence_drawdown_pct": -18.0,
    }
    result = _oos_gate(base, stress, long_trades=60, short_trades=60)
    assert result["passed"] is True
    assert result["side_specific_claims_allowed"] is True
    stress["net_expectancy_pct"] = -0.001
    assert _oos_gate(base, stress, 60, 60)["passed"] is False
    stress["net_expectancy_pct"] = 0.005
    stress["trade_sequence_drawdown_pct"] = -21.0
    assert _oos_gate(base, stress, 60, 60)["passed"] is False


def test_backtest_engine_accepts_isolated_signal_function(monkeypatch) -> None:
    monkeypatch.setattr(
        core,
        "choose_signal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("router was called")),
    )
    count = 200
    interval = 900_000
    times = np.arange(count, dtype=np.int64) * interval
    bars = pd.DataFrame({
        "open_time": times,
        "decision_time": times + interval,
        "open": np.full(count, 100.0),
        "high": np.full(count, 101.0),
        "low": np.full(count, 99.0),
        "close": np.full(count, 100.0),
        "atr14": np.full(count, 1.0),
    })
    funding = pd.DataFrame({
        "funding_time": np.arange(0, 7 * 8 * 3_600_000, 8 * 3_600_000, dtype=np.int64),
        "rate": np.full(7, 0.0001),
    })
    trades = core.simulate_symbol(
        "ETH-USDT",
        bars,
        funding,
        5.0,
        1.25,
        2.0,
        signal_fn=lambda _x, _i: None,
    )
    assert trades == []


def test_base_and_stress_cost_cases_must_share_trade_population() -> None:
    base = pd.DataFrame([
        {"symbol": "ETH-USDT", "side": "LONG", "entry_time": 100, "exit_time": 200}
    ])
    stress = base.copy()
    _assert_same_population(base, stress, "test")
    stress.loc[0, "exit_time"] = 201
    with pytest.raises(RuntimeError, match="identical trade population"):
        _assert_same_population(base, stress, "test")
