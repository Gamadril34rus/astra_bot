#!/usr/bin/env python3
"""Research-only 15m pullback continuation with the last closed 1h regime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import research_adaptive_perp_portfolio as portfolio

import pandas as pd


def add_15m_features(signal_bars: pd.DataFrame, hourly_bars: pd.DataFrame) -> pd.DataFrame:
    """Add shared closed-1h context and a prior-20-bar relative-volume baseline."""
    result = _BASE_ADD_FEATURES(signal_bars, hourly_bars)
    volume = result["quote_volume"].where(
        result["quote_volume"] > 0, result["volume"]
    )
    result["volume_ratio"] = volume / volume.shift(1).rolling(
        20, min_periods=20
    ).mean()
    return result


def choose_15m_pullback_signal(frame: pd.DataFrame, index: int) -> dict | None:
    """Trade a 15m pullback reclaim only when the last closed 1h regime agrees."""
    if index < 30:
        return None
    row = frame.iloc[index]
    required = (
        "atr14", "ema20", "ema50", "volume_ratio",
        "h1_ema20", "h1_ema50", "h1_ema20_rising",
        "h1_ema20_falling", "h1_adx14",
    )
    if any(column not in frame.columns or pd.isna(row[column]) for column in required):
        return None

    atr = float(row["atr14"])
    if (
        atr <= 0
        or float(row["h1_adx14"]) < 18
        or float(row["volume_ratio"]) < 1.2
    ):
        return None

    pullback = frame.iloc[index - 4:index]
    previous = frame.iloc[index - 1]
    current_close = float(row["close"])
    current_open = float(row["open"])

    long_regime = (
        float(row["h1_ema20"]) > float(row["h1_ema50"])
        and bool(row["h1_ema20_rising"])
    )
    short_regime = (
        float(row["h1_ema20"]) < float(row["h1_ema50"])
        and bool(row["h1_ema20_falling"])
    )

    if long_regime:
        touched_zone = (
            (pullback["low"] <= pullback["ema20"])
            & (pullback["low"] >= pullback["ema50"] - 0.25 * pullback["atr14"])
        ).any()
        held_structure = (
            pullback["close"] > pullback["ema50"] - 0.25 * pullback["atr14"]
        ).all()
        reclaimed = (
            current_close > float(row["ema20"])
            and current_close > float(previous["high"])
            and current_close > current_open
        )
        if touched_zone and held_structure and reclaimed:
            stop = float(pullback["low"].min()) - 0.15 * atr
            return {
                "family": "15M_PULLBACK_CONTINUATION",
                "side": 1,
                "stop": stop,
                "target_rr": 1.5,
            }

    if short_regime:
        touched_zone = (
            (pullback["high"] >= pullback["ema20"])
            & (pullback["high"] <= pullback["ema50"] + 0.25 * pullback["atr14"])
        ).any()
        held_structure = (
            pullback["close"] < pullback["ema50"] + 0.25 * pullback["atr14"]
        ).all()
        reclaimed = (
            current_close < float(row["ema20"])
            and current_close < float(previous["low"])
            and current_close < current_open
        )
        if touched_zone and held_structure and reclaimed:
            stop = float(pullback["high"].max()) + 0.15 * atr
            return {
                "family": "15M_PULLBACK_CONTINUATION",
                "side": -1,
                "stop": stop,
                "target_rr": 1.5,
            }

    return None


_BASE_ADD_FEATURES = portfolio.add_features
_BASE_CHOOSE_SIGNAL = portfolio.choose_signal


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--out", type=Path, default=Path("reports/fifteen_minute_pullback"))
    args, _ = parser.parse_known_args()

    portfolio.add_features = add_15m_features
    portfolio.choose_signal = choose_15m_pullback_signal
    try:
        result = portfolio.main()
    finally:
        portfolio.add_features = _BASE_ADD_FEATURES
        portfolio.choose_signal = _BASE_CHOOSE_SIGNAL

    summary_path = args.out / "summary.json"
    if summary_path.exists():
        report = json.loads(summary_path.read_text(encoding="utf-8"))
        report["strategy_hypothesis"] = {
            "name": "15m_pullback_continuation_closed_1h_context",
            "signal_timeframe": "15m closed candles",
            "context_timeframe": "latest fully closed 1h candle, backward as-of merge on decision_time",
            "regime": "1h EMA20/EMA50 direction, 3-bar EMA20 slope, ADX14 >=18",
            "pullback": "touch of 15m EMA20 within preceding four closed bars while EMA50 structure holds within 0.25 ATR",
            "trigger": "current closed 15m candle reclaims EMA20 and closes beyond the prior candle extreme in the regime direction",
            "relative_volume": ">=1.2x mean of the preceding 20 closed 15m bars; current signal bar excluded; quote volume or within-symbol base-volume fallback",
            "entry": "next 15m open",
            "stop": "four-bar pullback swing plus/minus 0.15 ATR; shared 0.35-1.8 ATR eligibility",
            "target": "fixed 1.5R",
            "hold": "shared 30m and 90m no-progress invalidation; 9h hard maximum",
            "oos_policy": "frozen rules; trades crossing IS/validation/OOS boundaries are excluded; no OOS tuning",
        }
        summary_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
