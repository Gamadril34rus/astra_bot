"""Deterministic tests for the SAR research engine's candle-time semantics."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sar_backtest_engine as engine


def test_interval_to_ms_supports_video_timeframe():
    assert engine.interval_to_ms("15m") == 900_000
    assert engine.interval_to_ms("1h") == 3_600_000


@pytest.mark.parametrize("value", ["", "m15", "0m", "-15m", "15x"])
def test_interval_to_ms_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        engine.interval_to_ms(value)


def test_sar_reversal_is_confirmed_from_current_bar_but_stop_was_known_before_it():
    high = np.array([10.0, 11.0, 12.0, 11.0])
    low = np.array([9.0, 10.0, 11.0, 8.0])
    sar, trend = engine.parabolic_sar_series(high, low)

    # At the open of bar 3, the stop known from the completed bar 2 is sar[2].
    assert trend[2] == 1
    assert sar[2] < low[2]
    # Bar 3 crosses that stop and confirms a reversal only at its close.
    assert low[3] < sar[2]
    assert trend[3] == -1
    # The stored SAR on the reversal bar is the prior trend's EP, not
    # the stop that was tested intrabar.
    assert sar[3] == pytest.approx(12.0)


def test_gap_detection_uses_selected_candle_interval():
    df = pd.DataFrame({"timestamp": [0, 900_000, 2_700_000]})
    result = engine.check_hourly_gaps(df, interval_ms=900_000)
    assert result["gaps"] == 1
    assert result["max_gap_intervals"] == pytest.approx(2.0)
