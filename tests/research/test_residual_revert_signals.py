"""Unit tests for residual-revert research strategy (no network, no live)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from astra_bot.strategies.research.residual_revert import (
    ResidualRevertConfig,
    compute_features,
    generate_signals,
    max_hold_bars,
    stop_price,
    take_price,
)


def _ohlc(n: int = 80, start: float = 100.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="30min", tz="UTC")
    close = start + np.cumsum(rng.normal(0, 0.5, size=n))
    high = close + rng.uniform(0.1, 1.0, size=n)
    low = close - rng.uniform(0.1, 1.0, size=n)
    open_ = close + rng.normal(0, 0.2, size=n)
    vol = rng.uniform(100, 200, size=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_features_align_and_no_future_btc_only_past():
    alt = _ohlc(100, 50.0, seed=1)
    btc = _ohlc(100, 100.0, seed=2)
    feat = compute_features(alt, btc)
    assert len(feat) == len(alt)
    assert "resid_sum" in feat.columns
    # first resid_window-1 residual sums are NaN
    cfg = ResidualRevertConfig()
    assert feat["resid_sum"].iloc[: cfg.resid_window - 1].isna().all()


def test_signals_are_minus1_0_1():
    alt = _ohlc(120, 50.0, seed=3)
    btc = _ohlc(120, 100.0, seed=4)
    feat = compute_features(alt, btc)
    sig = generate_signals(feat)
    assert set(sig.unique()).issubset({-1, 0, 1})


def test_stop_take_sides():
    cfg = ResidualRevertConfig(stop_atr=2.0, take_rr=2.5)
    assert stop_price(100.0, 1, 1.0, cfg) == 98.0
    assert take_price(100.0, 1, 1.0, cfg) == 105.0
    assert stop_price(100.0, -1, 1.0, cfg) == 102.0
    assert take_price(100.0, -1, 1.0, cfg) == 95.0


def test_max_hold_within_nine_hours_on_30m():
    assert max_hold_bars() <= 18


def test_empty_taker_not_required():
    """Strategy must not depend on taker_buy columns."""
    alt = _ohlc(90, seed=5)
    btc = _ohlc(90, seed=6)
    assert "taker_buy_volume" not in alt.columns
    feat = compute_features(alt, btc)
    _ = generate_signals(feat)
