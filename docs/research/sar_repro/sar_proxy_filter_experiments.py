#!/usr/bin/env python3
"""Evaluate a small, pre-registered set of SAR regime filters on frozen proxy data.

Filters are fixed hypotheses, not a parameter grid. Binance USD-M is not BingX history.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sar_proxy_backtest import grouped_metrics, load_dataset, metrics, run_trades

INTERVAL_MS = 15 * 60 * 1000
BASELINE_FEE = 0.0005
BASELINE_SLIPPAGE = 0.001


def add_filter_indicators(
    trades: list[dict], bars_by_symbol: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    annotated = []
    for symbol, frame in bars_by_symbol.items():
        subset = pd.DataFrame([row for row in trades if row["symbol"] == symbol])
        if subset.empty:
            continue
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        close = frame["close"].astype(float)
        previous_close = close.shift(1)
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
            index=frame.index,
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
            index=frame.index,
        )
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        plus_di = (
            100
            * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            / atr
        )
        minus_di = (
            100
            * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            / atr
        )
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        adx_by_open = pd.Series(
            adx.to_numpy(), index=frame["timestamp"].to_numpy(dtype="int64")
        )

        subset["signal_ts"] = subset["entry_ts"].astype("int64") - INTERVAL_MS
        subset["adx14"] = subset["signal_ts"].map(adx_by_open)

        hourly_source = frame.copy()
        hourly_source["datetime"] = pd.to_datetime(
            hourly_source["timestamp"], unit="ms", utc=True
        )
        hourly = (
            hourly_source.set_index("datetime")
            .resample("1h", label="left", closed="left")
            .agg(
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
            )
            .dropna(subset=["close"])
        )
        hourly["ema200"] = hourly["close"].ewm(
            span=200, adjust=False, min_periods=200
        ).mean()
        hourly["hour_close_ts"] = hourly.index.as_unit("ms").asi8 + 3_600_000
        if len(hourly) and int(hourly["hour_close_ts"].max()) < 1_000_000_000_000:
            raise ValueError("hourly timestamps are not epoch milliseconds")
        right = hourly[["hour_close_ts", "close", "ema200"]].dropna(
            subset=["ema200"]
        )
        left = subset[["entry_ts"]].sort_values("entry_ts")
        joined = pd.merge_asof(
            left,
            right.sort_values("hour_close_ts"),
            left_on="entry_ts",
            right_on="hour_close_ts",
            direction="backward",
        )
        subset = subset.sort_values("entry_ts").reset_index(drop=True)
        joined = joined.reset_index(drop=True)
        matched = joined["hour_close_ts"].notna()
        if (
            joined.loc[matched, "hour_close_ts"].to_numpy()
            > subset.loc[matched, "entry_ts"].to_numpy()
        ).any():
            raise ValueError(f"{symbol}: higher-timeframe filter used a future candle")
        subset["hourly_close"] = joined["close"]
        subset["hourly_ema200"] = joined["ema200"]
        subset["htf_ema200_aligned"] = np.where(
            subset["side"].eq("long"),
            subset["hourly_close"] > subset["hourly_ema200"],
            subset["hourly_close"] < subset["hourly_ema200"],
        )
        annotated.append(subset)
    return pd.concat(annotated, ignore_index=True)


def split_metrics(
    frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> dict:
    total_days = (end - start).days
    cut_60 = start + pd.Timedelta(days=round(total_days * 0.6))
    cut_80 = start + pd.Timedelta(days=round(total_days * 0.8))
    result = {}
    frame = frame.copy()
    frame["entry_datetime"] = pd.to_datetime(frame["entry_ts"], unit="ms", utc=True)
    for label, lower, upper in (
        ("in_sample_60pct", start, cut_60),
        ("validation_20pct", cut_60, cut_80),
        ("out_of_sample_20pct", cut_80, end),
    ):
        subset = frame[
            (frame["entry_datetime"] >= lower) & (frame["entry_datetime"] < upper)
        ]
        result[label] = {
            "start_utc": lower.isoformat(),
            "end_utc_exclusive": upper.isoformat(),
            **metrics(subset.to_dict("records")),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    bars, funding, coverage = load_dataset(args.data_dir)
    baseline_trades = run_trades(
        bars, funding, BASELINE_FEE, BASELINE_SLIPPAGE, init_trend=1
    )
    annotated = add_filter_indicators(baseline_trades, bars)
    manifest = json.loads((args.data_dir / "manifest.json").read_text(encoding="utf-8"))
    start = pd.Timestamp(manifest["start_utc"])
    end = pd.Timestamp(manifest["end_utc_exclusive"])

    candidates = {
        "baseline_unfiltered": pd.Series(True, index=annotated.index),
        "adx14_ge_20": annotated["adx14"] >= 20,
        "hourly_ema200_direction_alignment": annotated["htf_ema200_aligned"].fillna(False),
        "adx14_ge_20_and_hourly_ema200_alignment": (
            (annotated["adx14"] >= 20)
            & annotated["htf_ema200_aligned"].fillna(False)
        ),
    }
    results = {}
    output_rows = []
    for name, mask in candidates.items():
        selected = annotated[mask].copy()
        records = selected.to_dict("records")
        results[name] = {
            "overall": metrics(records),
            "by_symbol_and_side": grouped_metrics(selected, ["symbol", "side"]),
            "chronological_splits": split_metrics(selected, start, end),
        }
        selected["filter_variant"] = name
        output_rows.extend(selected.to_dict("records"))

    annotated["signal_timestamp"] = pd.to_datetime(
        annotated["signal_ts"], unit="ms", utc=True
    )
    annotated.to_csv(args.out_dir / "all_baseline_trades_with_filter_indicators.csv", index=False)
    pd.DataFrame(output_rows).to_csv(args.out_dir / "selected_filter_trades.csv", index=False)

    summary = {
        "source": "Binance USD-M perpetuals; cross-exchange proxy, not BingX",
        "period_start_utc": start.isoformat(),
        "period_end_exclusive_utc": end.isoformat(),
        "interval": "15m",
        "cost_model": {
            "taker_fee_per_side": BASELINE_FEE,
            "slippage_per_side": BASELINE_SLIPPAGE,
        },
        "data_coverage": coverage,
        "fixed_hypotheses": {
            "adx14_ge_20": "15m ADX(14) >= 20 on the completed signal candle",
            "hourly_ema200_direction_alignment": (
                "latest fully closed 1h close above EMA(200) for longs, "
                "below EMA(200) for shorts"
            ),
            "combined": "both fixed filters must pass",
        },
        "parameter_search": False,
        "results": results,
        "decision": (
            "No filter qualifies for VST or demo promotion unless net PF and "
            "out-of-sample expectancy independently pass the research gate."
        ),
    }
    (args.out_dir / "fixed_filter_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
