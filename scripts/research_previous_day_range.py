#!/usr/bin/env python3
"""Research-only previous-UTC-day range breakout for BingX USDT-M perps."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import research_adaptive_perp_portfolio as portfolio


def add_previous_day_levels(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach only the prior completed UTC day's high and low to each bar."""
    result = frame.copy()
    utc_day = pd.to_datetime(result["open_time"], unit="ms", utc=True).dt.floor("D")
    result["_utc_day"] = utc_day
    result["_utc_day_bar_index"] = result.groupby("_utc_day").cumcount()

    daily = result.groupby("_utc_day", sort=True).agg(
        day_high=("high", "max"),
        day_low=("low", "min"),
    )
    previous = daily.shift(1)
    result["previous_day_high"] = utc_day.map(previous["day_high"])
    result["previous_day_low"] = utc_day.map(previous["day_low"])
    return result


def _relative_volume(frame: pd.DataFrame, index: int) -> float:
    """Current base/quote volume divided by the preceding 20-bar mean."""
    if "quote_volume" in frame:
        volume = frame["quote_volume"].where(
            frame["quote_volume"] > 0, frame["volume"]
        )
    else:
        volume = frame["volume"]
    baseline = float(volume.iloc[index - 20:index].mean())
    current = float(volume.iloc[index])
    if baseline <= 0 or not np.isfinite(baseline) or current <= 0:
        return 0.0
    return current / baseline


def choose_previous_day_breakout_signal(
    frame: pd.DataFrame, index: int
) -> dict | None:
    """Trade the first 30m close outside yesterday's UTC range, with 1h trend."""
    if index < 20:
        return None

    row = frame.iloc[index]
    required = (
        "atr14",
        "h1_adx14",
        "h1_ema20",
        "h1_ema50",
        "previous_day_high",
        "previous_day_low",
        "_utc_day_bar_index",
    )
    if any(column not in frame.columns or pd.isna(row[column]) for column in required):
        return None

    atr = float(row["atr14"])
    adx = float(row["h1_adx14"])
    if atr <= 0 or adx < 20:
        return None
    if _relative_volume(frame, index) < 1.5:
        return None

    prior = frame.iloc[index - 4:index]
    previous_close = float(frame.iloc[index - 1]["close"])
    current_close = float(row["close"])
    current_open = float(row["open"])
    day_start = index - int(row["_utc_day_bar_index"])
    earlier_today = frame.iloc[day_start:index]
    day_high = float(row["previous_day_high"])
    day_low = float(row["previous_day_low"])

    long_context = (
        float(row["h1_ema20"]) > float(row["h1_ema50"])
        and bool(row["h1_ema20_rising"])
    )
    short_context = (
        float(row["h1_ema20"]) < float(row["h1_ema50"])
        and bool(row["h1_ema20_falling"])
    )

    # First close outside the prior-day range only. A later recross on the
    # same UTC day cannot create a second entry from the same level.
    if (
        long_context
        and current_close > day_high
        and previous_close <= day_high
        and current_close > current_open
        and not bool((earlier_today["close"] > day_high).any())
    ):
        stop = float(prior["low"].min()) - 0.15 * atr
        return {
            "family": "PREVIOUS_DAY_RANGE_BREAKOUT",
            "side": 1,
            "stop": stop,
            "target_rr": 2.5,
        }

    if (
        short_context
        and current_close < day_low
        and previous_close >= day_low
        and current_close < current_open
        and not bool((earlier_today["close"] < day_low).any())
    ):
        stop = float(prior["high"].max()) + 0.15 * atr
        return {
            "family": "PREVIOUS_DAY_RANGE_BREAKOUT",
            "side": -1,
            "stop": stop,
            "target_rr": 2.5,
        }

    return None


_BASE_ADD_FEATURES = portfolio.add_features


def add_features_with_previous_day(
    signal_bars: pd.DataFrame, hourly_bars: pd.DataFrame
) -> pd.DataFrame:
    return add_previous_day_levels(_BASE_ADD_FEATURES(signal_bars, hourly_bars))


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--signal-timeframe", choices=("15m", "30m"), default="15m")
    parser.add_argument("--out", type=Path, default=Path("reports/previous_day_range"))
    args, _ = parser.parse_known_args()
    if args.signal_timeframe != "30m":
        raise SystemExit(
            "previous-day range breakout requires --signal-timeframe 30m"
        )

    portfolio.choose_signal = choose_previous_day_breakout_signal
    portfolio.add_features = add_features_with_previous_day
    try:
        result = portfolio.main()
    finally:
        portfolio.choose_signal = _BASE_CHOOSE_SIGNAL
        portfolio.add_features = _BASE_ADD_FEATURES

    summary_path = args.out / "summary.json"
    if summary_path.exists():
        report = json.loads(summary_path.read_text(encoding="utf-8"))
        report["strategy_hypothesis"] = {
            "name": "previous_utc_day_range_breakout",
            "signal_timeframe": "30m",
            "context_timeframe": "1h",
            "prior_day_range": "completed UTC day high/low, from 00:00 to 23:59:59",
            "entry": "next 30m open after first 30m close outside prior-day range",
            "relative_volume_min": 1.5,
            "relative_volume_baseline": "mean of preceding 20 30m bars, current bar excluded",
            "trend_filter": "1h ADX14 >=20 plus EMA20/EMA50 direction and 3-bar EMA20 slope",
            "stop": "preceding four 30m bar swing plus/minus 0.15 ATR",
            "target_r": 2.5,
            "reentry": "one first breakout per direction and UTC day",
            "holding_rules": "shared 30m / 90m no-progress invalidation and 9h hard limit",
            "note": "Pre-registered exploratory hypothesis; no OOS parameter tuning.",
        }
        summary_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


_BASE_CHOOSE_SIGNAL = portfolio.choose_signal


if __name__ == "__main__":
    raise SystemExit(main())
