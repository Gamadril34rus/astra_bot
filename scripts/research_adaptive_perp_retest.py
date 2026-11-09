#!/usr/bin/env python3
"""Research-only confirmed breakout/retest strategy for BingX USDT-M perps.

This is an independent hypothesis runner. It reuses the frozen portfolio
simulator, funding ledger, cost model, split logic and reporting from the
adaptive-perp research module, while replacing only the signal selector.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from scripts import research_adaptive_perp_portfolio as portfolio


def choose_retest_breakout_signal(frame: pd.DataFrame, index: int) -> dict | None:
    """Enter only after a volume-confirmed range break has held its retest.

    A breakout must have happened 1-3 closed 30m bars earlier. Entry remains
    the next 30m open, provided by the shared simulator. The current retest
    candle must touch the broken level area and close back on the breakout
    side. The 1h regime must confirm direction.
    """
    if index < 21:
        return None

    row = frame.iloc[index]
    required = ("atr14", "h1_adx14", "h1_ema20", "h1_ema50")
    if any(pd.isna(row[column]) for column in required):
        return None

    atr = float(row["atr14"])
    if atr <= 0 or float(row["h1_adx14"]) < 18:
        return None

    volume_column = "quote_volume" if "quote_volume" in frame.columns else "volume"
    long_regime = (
        float(row["h1_ema20"]) > float(row["h1_ema50"])
        and pd.notna(row["h1_ema20_rising"])
        and bool(row["h1_ema20_rising"])
    )
    short_regime = (
        float(row["h1_ema20"]) < float(row["h1_ema50"])
        and pd.notna(row["h1_ema20_falling"])
        and bool(row["h1_ema20_falling"])
    )

    # Most recent valid break wins. Only the preceding three closed bars
    # qualify, so a stale level cannot trigger a late entry.
    for breakout_index in range(index - 1, max(19, index - 4), -1):
        breakout = frame.iloc[breakout_index]
        prior = frame.iloc[breakout_index - 20:breakout_index]
        if len(prior) != 20:
            continue

        prior_volume_mean = float(prior[volume_column].mean())
        breakout_volume = float(breakout[volume_column])
        if prior_volume_mean <= 0 or breakout_volume <= 0:
            continue
        relative_volume = breakout_volume / prior_volume_mean

        between = frame.iloc[breakout_index + 1:index]
        if long_regime and relative_volume >= 1.3:
            level = float(prior["high"].max())
            held_level = between.empty or bool((between["close"] >= level).all())
            retest = (
                float(breakout["close"]) > level
                and held_level
                and float(row["low"]) <= level + 0.15 * atr
                and float(row["close"]) >= level
                and float(row["close"]) <= level + 0.50 * atr
                and float(row["close"]) > float(row["open"])
            )
            if retest:
                swing_low = float(frame.iloc[breakout_index + 1:index + 1]["low"].min())
                return {
                    "family": "RETEST_BREAKOUT",
                    "side": 1,
                    "stop": swing_low - 0.15 * atr,
                    "target_rr": 2.0,
                }

        if short_regime and relative_volume >= 1.3:
            level = float(prior["low"].min())
            held_level = between.empty or bool((between["close"] <= level).all())
            retest = (
                float(breakout["close"]) < level
                and held_level
                and float(row["high"]) >= level - 0.15 * atr
                and float(row["close"]) <= level
                and float(row["close"]) >= level - 0.50 * atr
                and float(row["close"]) < float(row["open"])
            )
            if retest:
                swing_high = float(frame.iloc[breakout_index + 1:index + 1]["high"].max())
                return {
                    "family": "RETEST_BREAKOUT",
                    "side": -1,
                    "stop": swing_high + 0.15 * atr,
                    "target_rr": 2.0,
                }

    return None


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--signal-timeframe", choices=("15m", "30m"), default="15m")
    parser.add_argument("--out", type=Path, default=Path("reports/adaptive_perp"))
    args, _ = parser.parse_known_args()
    if args.signal_timeframe != "30m":
        raise SystemExit(
            "confirmed breakout/retest hypothesis requires --signal-timeframe 30m"
        )

    portfolio.choose_signal = choose_retest_breakout_signal
    result = portfolio.main()

    summary_path = args.out / "summary.json"
    if summary_path.exists():
        report = json.loads(summary_path.read_text(encoding="utf-8"))
        report["strategy_hypothesis"] = {
            "name": "confirmed_breakout_retest",
            "signal_timeframe": args.signal_timeframe,
            "context_timeframe": "1h",
            "breakout_lookback_bars": 20,
            "retest_window_bars": 3,
            "breakout_relative_quote_volume_min": 1.3,
            "retest_touch_tolerance_atr": 0.15,
            "maximum_retest_close_extension_atr": 0.50,
            "target_r": 2.0,
            "entry": "next 30m open after the retest candle closes",
            "holding_rules": "shared 30m / 90m no-progress invalidation and 9h hard limit",
            "note": "Pre-registered exploratory hypothesis; no parameter search on OOS.",
        }
        summary_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
