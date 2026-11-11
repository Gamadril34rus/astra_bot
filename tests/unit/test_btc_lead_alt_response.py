from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scripts.research_adaptive_perp_portfolio import (
    add_btc_lead_features,
    choose_signals,
)


HOUR_MS = 3_600_000


def _hourly(close: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({
        "decision_time": np.arange(1, len(close) + 1, dtype="int64") * HOUR_MS,
        "close": close,
    })


def _synthetic_hourly() -> tuple[pd.DataFrame, pd.DataFrame]:
    index = np.arange(300, dtype=float)
    btc_returns = 0.001 * np.sin(index * 0.31) + 0.0002 * np.cos(index * 0.07)
    alt_returns = 0.75 * btc_returns + 0.0003 * np.sin(index * 0.13)
    btc_close = 60_000 * np.exp(np.cumsum(btc_returns))
    alt_close = 2_000 * np.exp(np.cumsum(alt_returns))
    return _hourly(alt_close), _hourly(btc_close)


def test_btc_lead_event_is_only_marked_at_fully_closed_hour() -> None:
    alt, btc = _synthetic_hourly()
    last_hour_close = int(alt.decision_time.iloc[-1])
    signal_bars = pd.DataFrame({
        "decision_time": [
            last_hour_close - 900_000,
            last_hour_close,
            last_hour_close + 900_000,
        ],
        "close": [float(alt.close.iloc[-1])] * 3,
    })

    result = add_btc_lead_features(signal_bars, alt, btc)

    assert result["btc_lead_event"].tolist() == [False, True, False]
    assert pd.notna(result.loc[1, "btc_beta_168h"])
    assert pd.notna(result.loc[1, "btc_shock_z"])
    assert pd.notna(result.loc[1, "btc_lag_z"])


def test_btc_beta_uses_only_prior_hourly_returns() -> None:
    alt, btc = _synthetic_hourly()
    signal_bars = pd.DataFrame({"decision_time": alt.decision_time, "close": alt.close})
    baseline = add_btc_lead_features(signal_bars, alt, btc)

    changed_alt = alt.copy()
    changed_btc = btc.copy()
    changed_alt.loc[changed_alt.index[-1], "close"] *= 1.08
    changed_btc.loc[changed_btc.index[-1], "close"] *= 0.94
    changed = add_btc_lead_features(signal_bars, changed_alt, changed_btc)

    assert changed.loc[len(changed) - 1, "btc_beta_168h"] == pytest.approx(
        baseline.loc[len(baseline) - 1, "btc_beta_168h"]
    )


@pytest.mark.parametrize(
    ("btc_return", "expected_side"),
    [(0.02, 1), (-0.02, -1)],
)
def test_btc_lead_alt_response_requires_shock_and_underreaction(
    btc_return: float, expected_side: int
) -> None:
    rows = 30
    frame = pd.DataFrame({
        "open": [100.0] * rows,
        "high": [100.5] * rows,
        "low": [99.5] * rows,
        "close": [100.0] * rows,
        "atr14": [1.0] * rows,
        "ema20": [100.0] * rows,
        "ema50": [100.0] * rows,
        "rsi14": [50.0] * rows,
        "bb_upper": [110.0] * rows,
        "bb_lower": [90.0] * rows,
        "bb_width_pct": [0.5] * rows,
        "volume_ratio": [1.0] * rows,
        "h1_ema20": [100.0] * rows,
        "h1_ema50": [100.0] * rows,
        "h1_ema20_rising": [False] * rows,
        "h1_ema20_falling": [False] * rows,
        "h1_adx14": [15.0] * rows,
        "btc_lead_event": [False] * rows,
        "btc_return_1h": [np.nan] * rows,
        "btc_shock_z": [np.nan] * rows,
        "btc_beta_168h": [np.nan] * rows,
        "btc_lag_z": [np.nan] * rows,
    })
    frame.loc[25, [
        "btc_lead_event", "btc_return_1h", "btc_shock_z",
        "btc_beta_168h", "btc_lag_z",
    ]] = [True, btc_return, 2.0, 0.8, 1.0]

    signals = choose_signals(frame, 25)
    candidate = [item for item in signals if item["family"] == "BTC_LEAD_ALT_RESPONSE"]

    assert len(candidate) == 1
    assert candidate[0]["side"] == expected_side
    assert candidate[0]["stop"] == pytest.approx(100.0 - expected_side * 1.2)
    assert candidate[0]["target_rr"] == 1.5
