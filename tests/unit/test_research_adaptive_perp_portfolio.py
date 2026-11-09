from __future__ import annotations

import pandas as pd
import pytest

from scripts.research_adaptive_perp_portfolio import (
    MAX_HOLD_BARS,
    add_features,
    funding_cost_fraction,
    load_bars,
)


def _bars(times: list[int], interval_ms: int) -> pd.DataFrame:
    frame = pd.DataFrame({
        "open_time": times,
        "open": [100.0 + i * 0.01 for i in range(len(times))],
        "high": [102.0 + i * 0.01 for i in range(len(times))],
        "low": [99.0 + i * 0.01 for i in range(len(times))],
        "close": [101.0 + i * 0.01 for i in range(len(times))],
        "volume": [10.0] * len(times),
        "quote_volume": [1000.0] * len(times),
    })
    frame["decision_time"] = frame["open_time"] + interval_ms
    return frame


def test_higher_timeframe_merge_uses_last_closed_hour_only() -> None:
    m15 = _bars([0, 900_000, 1_800_000, 2_700_000, 3_600_000], 900_000)
    h1 = _bars([0, 3_600_000, 7_200_000], 3_600_000)
    merged = add_features(m15, h1)
    assert merged.loc[0:2, "h1_ema20"].isna().all()
    assert pd.notna(merged.loc[3, "h1_ema20"])


def test_positive_funding_is_cost_for_long_and_credit_for_short() -> None:
    rates = pd.Series([0.0001, 0.0002])
    assert funding_cost_fraction(1, rates) == pytest.approx(0.0003)
    assert funding_cost_fraction(-1, rates) == pytest.approx(-0.0003)


def test_hard_hold_limit_is_nine_hours() -> None:
    assert MAX_HOLD_BARS * 15 == 540


def test_loader_rejects_candle_gaps(tmp_path) -> None:
    path = tmp_path / "BTCUSDT_15m.csv"
    _bars([0, 900_000, 2_700_000], 900_000).to_csv(path, index=False)
    with pytest.raises(ValueError, match="gaps"):
        load_bars(path, "15m")


def test_loader_rejects_duplicate_timestamps(tmp_path) -> None:
    path = tmp_path / "BTCUSDT_15m.csv"
    _bars([0, 900_000, 900_000], 900_000).to_csv(path, index=False)
    with pytest.raises(ValueError, match="duplicate"):
        load_bars(path, "15m")
