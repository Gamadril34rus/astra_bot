from __future__ import annotations

import pandas as pd
import pytest

from scripts.research_perp_intraday_net_edge import (
    add_features,
    funding_cost_fraction,
    load_bars,
)


def _bars(times: list[int], interval_ms: int) -> pd.DataFrame:
    return pd.DataFrame({
        "open_time": times,
        "open": [100.0] * len(times),
        "high": [102.0] * len(times),
        "low": [99.0] * len(times),
        "close": [101.0] * len(times),
        "volume": [10.0] * len(times),
    })


def test_higher_timeframe_features_use_only_closed_hour() -> None:
    m15 = _bars([0, 900_000, 1_800_000, 2_700_000, 3_600_000], 900_000)
    h1 = _bars([0, 3_600_000], 3_600_000)
    merged = add_features(m15, h1)

    # At 00:15..00:45 no hourly candle has closed. At 01:00, only the
    # 00:00-01:00 hourly candle is eligible.
    assert merged.loc[0:2, "h1_ema20"].isna().all()
    assert pd.notna(merged.loc[3, "h1_ema20"])
    assert merged.loc[3, "h1_ema20"] == pytest.approx(101.0)


def test_funding_sign_is_directional() -> None:
    rates = pd.Series([0.0001, 0.0002])
    assert funding_cost_fraction(1, rates) == pytest.approx(0.0003)
    assert funding_cost_fraction(-1, rates) == pytest.approx(-0.0003)


def test_candle_loader_rejects_gaps(tmp_path) -> None:
    path = tmp_path / "BTC-USDT_15m.csv"
    _bars([0, 900_000, 2_700_000], 900_000).to_csv(path, index=False)
    with pytest.raises(ValueError, match="gaps"):
        load_bars(path, "15m")


def test_candle_loader_rejects_duplicate_timestamps(tmp_path) -> None:
    path = tmp_path / "BTC-USDT_15m.csv"
    _bars([0, 900_000, 900_000], 900_000).to_csv(path, index=False)
    with pytest.raises(ValueError, match="duplicate"):
        load_bars(path, "15m")
