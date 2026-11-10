#!/usr/bin/env python3
"""Research-only 30m pullback continuation with completed 4h regime context."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import research_adaptive_perp_portfolio as portfolio


def build_completed_4h_bars(signal_bars: pd.DataFrame) -> pd.DataFrame:
    """Aggregate only complete UTC 4h candles from eight contiguous 30m bars."""
    frame = signal_bars.copy()
    frame["_utc"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame = frame.set_index("_utc").sort_index()
    aggregations = {
        "open_time": ("open_time", "first"),
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
        "volume": ("volume", "sum"),
        "quote_volume": ("quote_volume", "sum"),
        "bars": ("open_time", "count"),
        "first_open": ("open_time", "min"),
        "last_open": ("open_time", "max"),
    }
    grouped = frame.resample("4h", origin="epoch", label="left", closed="left").agg(**aggregations)
    interval = portfolio.INTERVAL_MS["30m"]
    expected = grouped["open_time"] + 7 * interval
    complete = (
        (grouped["bars"] == 8)
        & (grouped["last_open"] - grouped["first_open"] == 7 * interval)
        & (grouped["last_open"] == expected)
    )
    result = grouped.loc[complete].copy()
    if result.empty:
        raise ValueError("not enough complete 4h candles in 30m source")
    result["decision_time"] = result["open_time"] + 4 * 60 * 60_000
    return result.reset_index(drop=True)


def add_4h_regime_features(signal_bars: pd.DataFrame, hourly_bars: pd.DataFrame) -> pd.DataFrame:
    """Keep shared 30m features, then attach only the last completed 4h regime."""
    result = _BASE_ADD_FEATURES(signal_bars, hourly_bars)
    volume = result["quote_volume"].where(
        result["quote_volume"] > 0, result["volume"]
    )
    result["volume_ratio"] = volume / volume.shift(1).rolling(
        20, min_periods=20
    ).mean()
    h4 = build_completed_4h_bars(signal_bars)
    close = h4["close"]
    h4["h4_ema20"] = close.ewm(span=20, adjust=False).mean()
    h4["h4_ema50"] = close.ewm(span=50, adjust=False).mean()
    h4["h4_ema20_rising"] = h4["h4_ema20"] > h4["h4_ema20"].shift(3)
    h4["h4_ema20_falling"] = h4["h4_ema20"] < h4["h4_ema20"].shift(3)
    h4["h4_adx14"] = portfolio._adx(h4)
    columns = [
        "decision_time", "h4_ema20", "h4_ema50",
        "h4_ema20_rising", "h4_ema20_falling", "h4_adx14",
    ]
    return pd.merge_asof(
        result.sort_values("decision_time"),
        h4[columns].sort_values("decision_time"),
        on="decision_time",
        direction="backward",
        allow_exact_matches=True,
    )


def choose_4h_pullback_signal(frame: pd.DataFrame, index: int) -> dict | None:
    """Buy/sell a 30m pullback reclaim only in a completed 4h directional regime."""
    if index < 30:
        return None
    row = frame.iloc[index]
    required = (
        "atr14", "ema20", "ema50", "volume_ratio",
        "h4_ema20", "h4_ema50", "h4_ema20_rising",
        "h4_ema20_falling", "h4_adx14",
    )
    if any(column not in frame.columns or pd.isna(row[column]) for column in required):
        return None
    atr = float(row["atr14"])
    if atr <= 0 or float(row["h4_adx14"]) < 18 or float(row["volume_ratio"]) < 1.2:
        return None

    pullback = frame.iloc[index - 6:index]
    previous = frame.iloc[index - 1]
    current_close = float(row["close"])
    current_open = float(row["open"])
    previous_high = float(previous["high"])
    previous_low = float(previous["low"])

    long_regime = (
        float(row["h4_ema20"]) > float(row["h4_ema50"])
        and bool(row["h4_ema20_rising"])
    )
    short_regime = (
        float(row["h4_ema20"]) < float(row["h4_ema50"])
        and bool(row["h4_ema20_falling"])
    )

    if long_regime:
        touched_zone = (
            (pullback["low"] <= pullback["ema20"])
            & (pullback["low"] >= pullback["ema50"] - 0.25 * pullback["atr14"])
        ).any()
        held_structure = (pullback["close"] > pullback["ema50"] - 0.25 * pullback["atr14"]).all()
        reclaimed = (
            current_close > float(row["ema20"])
            and current_close > previous_high
            and current_close > current_open
        )
        if touched_zone and held_structure and reclaimed:
            stop = float(pullback["low"].min()) - 0.15 * atr
            return {
                "family": "FOUR_HOUR_PULLBACK_CONTINUATION",
                "side": 1,
                "stop": stop,
                "target_rr": 2.0,
            }

    if short_regime:
        touched_zone = (
            (pullback["high"] >= pullback["ema20"])
            & (pullback["high"] <= pullback["ema50"] + 0.25 * pullback["atr14"])
        ).any()
        held_structure = (pullback["close"] < pullback["ema50"] + 0.25 * pullback["atr14"]).all()
        reclaimed = (
            current_close < float(row["ema20"])
            and current_close < previous_low
            and current_close < current_open
        )
        if touched_zone and held_structure and reclaimed:
            stop = float(pullback["high"].max()) + 0.15 * atr
            return {
                "family": "FOUR_HOUR_PULLBACK_CONTINUATION",
                "side": -1,
                "stop": stop,
                "target_rr": 2.0,
            }
    return None


_BASE_ADD_FEATURES = portfolio.add_features
_BASE_CHOOSE_SIGNAL = portfolio.choose_signal


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--out", type=Path, default=Path("reports/adaptive_perp"))
    args, _ = parser.parse_known_args()
    portfolio.add_features = add_4h_regime_features
    portfolio.choose_signal = choose_4h_pullback_signal
    try:
        result = portfolio.main()
    finally:
        portfolio.add_features = _BASE_ADD_FEATURES
        portfolio.choose_signal = _BASE_CHOOSE_SIGNAL

    summary_path = args.out / "summary.json"
    if summary_path.exists():
        report = json.loads(summary_path.read_text(encoding="utf-8"))
        report["strategy_hypothesis"] = {
            "name": "four_hour_pullback_continuation",
            "signal_timeframe": "30m",
            "regime_timeframe": "4h aggregated from eight complete contiguous 30m candles",
            "entry": "next 30m open after closed-bar pullback-zone touch and reclaim",
            "regime": "completed 4h EMA20/EMA50 direction, 3-bar EMA20 slope, ADX14 >=18",
            "relative_volume": ">=1.2x previous 20-bar mean; quote volume if positive, else base volume",
            "stop": "lowest/highest of preceding six 30m pullback bars plus/minus 0.15 ATR",
            "target": "fixed 2R",
            "hold": "shared 30m/90m no-progress exits and 9h hard cap",
            "oos_policy": "frozen hypothesis; no OOS tuning",
        }
        summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
