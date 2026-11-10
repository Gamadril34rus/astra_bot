#!/usr/bin/env python3
"""Describe BingX price/OI/taker-flow states and their forward returns.

Research-only diagnostic. This module does not generate trading entries or
recommend long/short positions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
BAR_INTERVAL_MS = 30 * 60_000
MAX_SNAPSHOT_CANDLE_AGE_MS = 20 * 60_000
MAX_OI_INTERVAL_MS = 90 * 60_000
FORWARD_HORIZONS_MIN = (30, 60, 120, 240, 540)


def load_candles(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {
        "open_time", "open", "high", "low", "close", "volume", "quote_volume",
        "taker_buy_volume", "taker_buy_quote_volume",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing required fields {sorted(missing)}")

    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
    for column in required - {"open_time"}:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["open_time"].duplicated().any():
        raise ValueError(f"{path}: duplicate candle open_time")
    frame = frame.sort_values("open_time").reset_index(drop=True)
    if frame[list(required - {"taker_buy_volume", "taker_buy_quote_volume"})].isna().any().any():
        raise ValueError(f"{path}: null OHLCV")
    if (frame["volume"] < 0).any() or (frame["quote_volume"] < 0).any():
        raise ValueError(f"{path}: negative volume")
    gaps = frame["open_time"].diff().dropna()
    if (gaps != BAR_INTERVAL_MS).any():
        raise ValueError(f"{path}: missing 30m candles; repair or segment before analysis")
    frame["decision_time"] = frame["open_time"] + BAR_INTERVAL_MS
    frame["close"] = frame["close"].astype(float)
    return frame


def load_open_interest(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"symbol", "source_time", "observed_at_ms", "open_interest_base"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing OI fields {sorted(missing)}")
    frame["source_time"] = pd.to_numeric(frame["source_time"], errors="raise").astype("int64")
    frame["observed_at_ms"] = pd.to_numeric(
        frame["observed_at_ms"], errors="raise"
    ).astype("int64")
    frame["open_interest_base"] = pd.to_numeric(
        frame["open_interest_base"], errors="raise"
    )
    if frame["observed_at_ms"].duplicated().any():
        raise ValueError(f"{path}: duplicate local observation time")
    if (frame["open_interest_base"] <= 0).any():
        raise ValueError(f"{path}: OI must be positive, missing values cannot be zero")
    return frame.sort_values("observed_at_ms").reset_index(drop=True)


def _taker_buy_share(row: pd.Series) -> tuple[float, str]:
    quote_total = float(row["quote_volume"])
    quote_buy = row["taker_buy_quote_volume"]
    if pd.notna(quote_buy) and quote_total > 0 and 0 <= float(quote_buy) <= quote_total * 1.001:
        return min(1.0, float(quote_buy) / quote_total), "quote"
    base_total = float(row["volume"])
    base_buy = row["taker_buy_volume"]
    if pd.notna(base_buy) and base_total > 0 and 0 <= float(base_buy) <= base_total * 1.001:
        return min(1.0, float(base_buy) / base_total), "base"
    return float("nan"), "missing"


def build_symbol_shadow(candles: pd.DataFrame, oi: pd.DataFrame) -> pd.DataFrame:
    """Join each OI observation to the latest completed 30m candle, never a future bar."""
    if candles.empty or oi.empty:
        return pd.DataFrame()

    candle_times = candles["decision_time"].to_numpy(dtype="int64")
    oi_rows = oi.sort_values("observed_at_ms").reset_index(drop=True)
    records: list[dict] = []

    for index, snapshot in oi_rows.iterrows():
        observed_at = int(snapshot["observed_at_ms"])
        candle_index = int(np.searchsorted(candle_times, observed_at, side="right") - 1)
        if candle_index < 1:
            continue
        candle = candles.iloc[candle_index]
        candle_close_time = int(candle["decision_time"])
        snapshot_age = observed_at - candle_close_time
        if snapshot_age < 0 or snapshot_age > MAX_SNAPSHOT_CANDLE_AGE_MS:
            continue

        previous_snapshot = oi_rows.iloc[index - 1] if index > 0 else None
        if previous_snapshot is None:
            continue
        oi_elapsed = observed_at - int(previous_snapshot["observed_at_ms"])
        if oi_elapsed <= 0 or oi_elapsed > MAX_OI_INTERVAL_MS:
            continue

        previous_oi = float(previous_snapshot["open_interest_base"])
        current_oi = float(snapshot["open_interest_base"])
        if previous_oi <= 0 or current_oi <= 0:
            continue
        oi_change_pct = (current_oi / previous_oi - 1.0) * 100.0

        previous_close = float(candles.iloc[candle_index - 1]["close"])
        current_close = float(candle["close"])
        if previous_close <= 0:
            continue
        price_change_pct = (current_close / previous_close - 1.0) * 100.0

        taker_share, taker_unit = _taker_buy_share(candle)
        if not np.isfinite(taker_share):
            continue
        taker_imbalance = (2.0 * taker_share - 1.0) * 100.0

        price_state = "up" if price_change_pct >= 0 else "down"
        oi_state = "up" if oi_change_pct >= 0 else "down"
        taker_state = "buy" if taker_imbalance >= 0 else "sell"
        state = f"price_{price_state}_oi_{oi_state}_taker_{taker_state}"

        record = {
            "symbol": str(snapshot["symbol"]),
            "observed_at_ms": observed_at,
            "observed_at_utc": pd.to_datetime(observed_at, unit="ms", utc=True).isoformat(),
            "candle_open_time": int(candle["open_time"]),
            "candle_close_time": candle_close_time,
            "snapshot_age_minutes": round(snapshot_age / 60_000, 2),
            "oi_observation_interval_minutes": round(oi_elapsed / 60_000, 2),
            "open_interest_base": current_oi,
            "open_interest_usdt_est": current_oi * current_close,
            "oi_change_pct": oi_change_pct,
            "price_change_pct": price_change_pct,
            "taker_buy_share_pct": taker_share * 100.0,
            "taker_imbalance_pct": taker_imbalance,
            "taker_volume_unit": taker_unit,
            "positioning_state": state,
        }

        for horizon in FORWARD_HORIZONS_MIN:
            # Forward horizons are anchored to the last fully closed bar,
            # not the later polling timestamp, so "30m" means one 30m bar.
            target_time = candle_close_time + horizon * 60_000
            target_index = int(np.searchsorted(candle_times, target_time, side="left"))
            if target_index >= len(candles):
                record[f"forward_{horizon}m_pct"] = np.nan
            else:
                target_close = float(candles.iloc[target_index]["close"])
                record[f"forward_{horizon}m_pct"] = (
                    (target_close / current_close - 1.0) * 100.0
                )
        records.append(record)

    return pd.DataFrame.from_records(records)


def summarize_shadow(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"snapshots": 0, "states": {}}
    states: dict[str, dict] = {}
    for state, group in frame.groupby("positioning_state", sort=True):
        metrics = {"snapshots": int(len(group))}
        for horizon in FORWARD_HORIZONS_MIN:
            column = f"forward_{horizon}m_pct"
            values = group[column].dropna()
            metrics[f"n_{horizon}m"] = int(values.size)
            metrics[f"mean_forward_{horizon}m_pct"] = (
                round(float(values.mean()), 5) if not values.empty else None
            )
            metrics[f"median_forward_{horizon}m_pct"] = (
                round(float(values.median()), 5) if not values.empty else None
            )
            metrics[f"positive_forward_{horizon}m_pct"] = (
                round(float((values > 0).mean() * 100.0), 2)
                if not values.empty else None
            )
        states[str(state)] = metrics
    return {
        "snapshots": int(len(frame)),
        "symbols": sorted(frame["symbol"].unique().tolist()),
        "first_observation_utc": str(frame["observed_at_utc"].min()),
        "last_observation_utc": str(frame["observed_at_utc"].max()),
        "states": states,
        "warning": (
            "Descriptive observational joins only. Total OI and taker-buy volume "
            "do not reveal participant identity, position side, or whether a trade "
            "opened or closed a position. Forward-return summaries are not a "
            "strategy backtest and do not establish a causal signal."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/bingx_usdm"))
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--out", type=Path, default=Path("reports/positioning_shadow"))
    args = parser.parse_args()
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    if not symbols or set(symbols) - set(SYMBOLS):
        raise SystemExit("symbols must be a non-empty subset of the five ASTRA BingX perpetuals")

    frames = []
    for symbol in symbols:
        stem = symbol.replace("-", "")
        candle_path = args.data_dir / f"{stem}_30m.csv"
        oi_path = args.data_dir / "open_interest" / f"{stem}_open_interest.csv"
        for path in (candle_path, oi_path):
            if not path.exists():
                raise SystemExit(f"INCOMPLETE: missing source file {path}")
        candles = load_candles(candle_path)
        oi = load_open_interest(oi_path)
        symbol_frame = build_symbol_shadow(candles, oi)
        frames.append(symbol_frame)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    args.out.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.out / "positioning_samples.csv", index=False)
    summary = summarize_shadow(combined)
    summary["method"] = {
        "signal_interval": "30m",
        "oi_join": "latest fully closed 30m candle at or before local OI observation timestamp",
        "max_candle_age_minutes": MAX_SNAPSHOT_CANDLE_AGE_MS / 60_000,
        "max_oi_observation_gap_minutes": MAX_OI_INTERVAL_MS / 60_000,
        "relative_volume_source": "taker-buy quote volume where available, else base volume",
        "forward_horizons_minutes": list(FORWARD_HORIZONS_MIN),
        "strategy_execution": "none; research-only descriptive shadow",
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
