"""Residual mean-reversion vs BTC — research / shadow only.

Idea
----
Idiosyncratic 30m returns of an alt relative to BTC often overshoot over a
few hours. When the 6-bar residual sum is extreme and the bar closes with a
reclaim (CLV filter), fade the residual. Not a claim of production edge:
OOS was INJ-concentrated and failed multi-symbol gates (see research report).

Causal contract
---------------
All features use only information available at bar close. Entry is intended
at the next bar open (enforced by the backtest runner).

This module must not be registered into live Zeus without owner approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Side = Literal[-1, 0, 1]


@dataclass(frozen=True)
class ResidualRevertConfig:
    resid_window: int = 6
    resid_threshold: float = 0.03
    clv_long: float = 0.55
    clv_short: float = 0.45
    atr_period: int = 14
    atr_ma_period: int = 48
    atr_ratio_cap: float = 2.0
    stop_atr: float = 2.0
    take_rr: float = 2.5
    max_hold_bars: int = 18  # 9h on 30m


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_features(
    alt: pd.DataFrame,
    btc: pd.DataFrame,
    cfg: ResidualRevertConfig | None = None,
) -> pd.DataFrame:
    """Join BTC returns and build causal residual features on alt index."""
    cfg = cfg or ResidualRevertConfig()
    out = alt.copy()
    btc_ret = btc["close"].pct_change().reindex(out.index)
    out["ret1"] = out["close"].pct_change()
    out["btc_ret"] = btc_ret
    out["resid"] = out["ret1"] - out["btc_ret"].fillna(0.0)
    out["resid_sum"] = out["resid"].rolling(cfg.resid_window, min_periods=cfg.resid_window).sum()
    rng = (out["high"] - out["low"]).replace(0, np.nan)
    out["clv"] = ((out["close"] - out["low"]) / rng).fillna(0.5)
    out["atr"] = _atr(out, cfg.atr_period)
    out["atr_ma"] = out["atr"].rolling(cfg.atr_ma_period, min_periods=max(12, cfg.atr_ma_period // 4)).mean()
    out["atr_ratio"] = out["atr"] / out["atr_ma"].replace(0, np.nan)
    return out


def generate_signals(features: pd.DataFrame, cfg: ResidualRevertConfig | None = None) -> pd.Series:
    """Return -1/0/+1 signal series aligned to feature index (decision at close)."""
    cfg = cfg or ResidualRevertConfig()
    d = features
    long = (
        (d["resid_sum"] < -cfg.resid_threshold)
        & (d["clv"] > cfg.clv_long)
        & (d["atr_ratio"] < cfg.atr_ratio_cap)
        & (d["ret1"] > -0.008)
    )
    short = (
        (d["resid_sum"] > cfg.resid_threshold)
        & (d["clv"] < cfg.clv_short)
        & (d["atr_ratio"] < cfg.atr_ratio_cap)
        & (d["ret1"] < 0.008)
    )
    return pd.Series(np.where(long, 1, np.where(short, -1, 0)), index=d.index, dtype=int)


def direction(signal: int) -> Side:
    if signal > 0:
        return 1
    if signal < 0:
        return -1
    return 0


def stop_price(entry: float, side: Side, atr_value: float, cfg: ResidualRevertConfig | None = None) -> float:
    cfg = cfg or ResidualRevertConfig()
    dist = cfg.stop_atr * atr_value
    if side == 1:
        return entry - dist
    if side == -1:
        return entry + dist
    return entry


def take_price(entry: float, side: Side, atr_value: float, cfg: ResidualRevertConfig | None = None) -> float:
    cfg = cfg or ResidualRevertConfig()
    dist = cfg.stop_atr * cfg.take_rr * atr_value
    if side == 1:
        return entry + dist
    if side == -1:
        return entry - dist
    return entry


def max_hold_bars(cfg: ResidualRevertConfig | None = None) -> int:
    cfg = cfg or ResidualRevertConfig()
    return cfg.max_hold_bars


def regime_ok(features_row: pd.Series, cfg: ResidualRevertConfig | None = None) -> bool:
    """Market regime filter at decision time."""
    cfg = cfg or ResidualRevertConfig()
    ratio = features_row.get("atr_ratio", np.nan)
    if ratio is None or not np.isfinite(ratio):
        return False
    return bool(ratio < cfg.atr_ratio_cap)
