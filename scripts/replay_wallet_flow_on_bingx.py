#!/usr/bin/env python3
"""Replay Hyperliquid wallet-flow events on BingX 30m OHLC and actual funding.

This is a cross-venue fixed-time event replay, not a complete strategy backtest:
it has no stop/target model and uses a 0-15m delayed 30m-bar entry proxy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SYMBOLS = {
    "BTC-PERP": "BTCUSDT",
    "ETH-PERP": "ETHUSDT",
    "SOL-PERP": "SOLUSDT",
    "HYPE-PERP": "HYPEUSDT",
    "INJ-PERP": "INJUSDT",
}
BASE_COST_BPS = {
    "BTCUSDT": 12.0,
    "ETHUSDT": 12.0,
    "SOLUSDT": 18.0,
    "HYPEUSDT": 18.0,
    "INJUSDT": 18.0,
}
BAR = pd.Timedelta(minutes=30)
HOLD = pd.Timedelta(hours=9)
EXIT_BAR_OFFSET = HOLD - BAR


def _load_market_data(data_dir: Path, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    bars = pd.read_csv(data_dir / f"{symbol}_30m.csv")
    bars["open_dt"] = pd.to_datetime(bars["open_time"], unit="ms", utc=True)
    bars = bars.sort_values("open_dt").set_index("open_dt")
    if bars.index.has_duplicates:
        raise ValueError(f"{symbol}: duplicate 30m bar timestamps")
    gaps = bars.index.to_series().diff().dropna()
    if not gaps.eq(BAR).all():
        raise ValueError(f"{symbol}: 30m archive contains gaps")

    funding = pd.read_csv(data_dir / f"funding_{symbol}.csv")
    funding["fund_dt"] = pd.to_datetime(funding["funding_time"], unit="ms", utc=True)
    funding = funding.sort_values("fund_dt")
    diffs = funding["fund_dt"].diff().dt.total_seconds().div(3600).dropna()
    cadence_values = diffs[diffs <= 24]
    if cadence_values.empty:
        raise ValueError(f"{symbol}: cannot infer funding cadence")
    cadence_hours = float(cadence_values.median())
    return bars, funding, cadence_hours


def _context(bars: pd.DataFrame, signal_close: pd.Timestamp) -> tuple[float, float, pd.Timestamp]:
    idx = bars.index
    # A 30m candle is usable only after its close; no partial-candle lookahead.
    context_pos = idx.searchsorted(signal_close - BAR, side="right") - 1
    if context_pos < 8:
        raise ValueError("insufficient closed 30m history for 1h/4h context")
    context_close = float(bars.iloc[context_pos]["close"])
    close_1h = float(bars.iloc[context_pos - 2]["close"])
    close_4h = float(bars.iloc[context_pos - 8]["close"])
    return (
        100.0 * (context_close / close_1h - 1.0),
        100.0 * (context_close / close_4h - 1.0),
        idx[context_pos],
    )


def _funding_for_short(
    funding: pd.DataFrame,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    cadence_hours: float,
) -> tuple[float | None, bool, int]:
    # A position opened exactly at settlement is treated as too late to receive it.
    inside = funding.loc[
        (funding["fund_dt"] > entry_time) & (funding["fund_dt"] <= exit_time)
    ]
    before = funding.loc[funding["fund_dt"] <= entry_time, "fund_dt"]
    after = funding.loc[funding["fund_dt"] > exit_time, "fund_dt"]
    if before.empty or after.empty:
        return None, False, int(len(inside))
    nearby = funding.loc[
        (funding["fund_dt"] >= before.iloc[-1])
        & (funding["fund_dt"] <= after.iloc[0]),
        "fund_dt",
    ]
    gaps = nearby.diff().dt.total_seconds().div(3600).dropna()
    expected_min = int((HOLD.total_seconds() / 3600) // cadence_hours)
    coverage = (
        len(inside) >= expected_min
        and not gaps.empty
        and float(gaps.max()) <= cadence_hours * 1.5 + 1e-9
    )
    if not coverage:
        return None, False, int(len(inside))
    # Positive funding is received by a short; negative funding is paid.
    return float(inside["rate"].sum() * 100.0), True, int(len(inside))


def _make_trade(
    bars: pd.DataFrame,
    funding: pd.DataFrame,
    cadence_hours: float,
    *,
    entry_time: pd.Timestamp,
    signal_time: pd.Timestamp | None,
    signal_value: float | None,
    prior_1h: float,
    prior_4h: float,
    symbol: str,
    market: str,
) -> dict[str, Any] | None:
    idx = bars.index
    entry_pos = idx.get_indexer([entry_time])[0]
    if entry_pos < 0:
        return None
    exit_open_time = entry_time + EXIT_BAR_OFFSET
    exit_pos = idx.get_indexer([exit_open_time])[0]
    if exit_pos < 0:
        return None
    exit_time = entry_time + HOLD
    entry_price = float(bars.iloc[entry_pos]["open"])
    exit_price = float(bars.iloc[exit_pos]["close"])
    if entry_price <= 0 or exit_price <= 0:
        return None
    gross = (entry_price / exit_price - 1.0) * 100.0
    funding_pct, funding_coverage, funding_events = _funding_for_short(
        funding, entry_time, exit_time, cadence_hours
    )
    cost_pct = BASE_COST_BPS[symbol] / 100.0
    net = gross + funding_pct - cost_pct if funding_pct is not None else None
    stress = (
        gross + funding_pct - cost_pct * 1.5
        if funding_pct is not None else None
    )
    return {
        "market": market,
        "symbol": symbol,
        "signal_time": signal_time.isoformat() if signal_time is not None else None,
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_delay_minutes": (
            (entry_time - signal_time).total_seconds() / 60.0
            if signal_time is not None else 0.0
        ),
        "prior_1h_return_pct": prior_1h,
        "prior_4h_return_pct": prior_4h,
        "signal_value": signal_value,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_short_return_pct": gross,
        "funding_return_pct": funding_pct,
        "funding_events": funding_events,
        "funding_coverage": funding_coverage,
        "base_cost_pct": cost_pct,
        "net_base_pct": net,
        "net_stress_pct": stress,
    }


def _non_overlapping(frame: pd.DataFrame, time_col: str) -> pd.DataFrame:
    kept = []
    for _, group in frame.sort_values(time_col).groupby("market", sort=False):
        last_time: pd.Timestamp | None = None
        for _, row in group.iterrows():
            current = pd.Timestamp(row[time_col])
            if last_time is None or current - last_time >= HOLD:
                kept.append(row)
                last_time = current
    if not kept:
        return frame.iloc[0:0].copy()
    return pd.DataFrame(kept).reset_index(drop=True)


def _stats(frame: pd.DataFrame) -> dict[str, Any]:
    valid = frame.loc[frame["net_base_pct"].notna()]
    if valid.empty:
        return {
            "events": int(len(frame)),
            "funding_covered_events": 0,
            "mean_net_base_pct": None,
            "median_net_base_pct": None,
            "hit_rate_pct": None,
            "mean_net_stress_pct": None,
        }
    return {
        "events": int(len(frame)),
        "funding_covered_events": int(len(valid)),
        "mean_gross_short_return_pct": float(valid["gross_short_return_pct"].mean()),
        "median_gross_short_return_pct": float(valid["gross_short_return_pct"].median()),
        "mean_funding_return_pct": float(valid["funding_return_pct"].mean()),
        "mean_net_base_pct": float(valid["net_base_pct"].mean()),
        "median_net_base_pct": float(valid["net_base_pct"].median()),
        "hit_rate_pct": float((valid["net_base_pct"] > 0).mean() * 100.0),
        "mean_net_stress_pct": float(valid["net_stress_pct"].mean()),
        "mean_entry_delay_minutes": float(valid["entry_delay_minutes"].mean()),
    }


def _bootstrap_mean_difference(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    n_boot: int = 5000,
    seed: int = 20261120,
) -> list[float] | None:
    c = candidate.loc[candidate["net_base_pct"].notna()].copy()
    b = baseline.loc[baseline["net_base_pct"].notna()].copy()
    if c.empty or b.empty:
        return None
    c["day"] = pd.to_datetime(c["entry_time"], utc=True).dt.floor("D")
    b["day"] = pd.to_datetime(b["entry_time"], utc=True).dt.floor("D")
    days = sorted(set(c["day"]) | set(b["day"]))
    c_groups = {day: g["net_base_pct"].to_numpy() for day, g in c.groupby("day")}
    b_groups = {day: g["net_base_pct"].to_numpy() for day, g in b.groupby("day")}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(n_boot):
        chosen = rng.choice(days, size=len(days), replace=True)
        c_parts = [c_groups[day] for day in chosen if day in c_groups]
        b_parts = [b_groups[day] for day in chosen if day in b_groups]
        if c_parts and b_parts:
            values.append(float(np.concatenate(c_parts).mean() - np.concatenate(b_parts).mean()))
    if not values:
        return None
    return [float(v) for v in np.quantile(values, [0.025, 0.5, 0.975])]


def replay(events_path: Path, bingx_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    events = pd.read_csv(events_path)
    events["bucket_ts"] = pd.to_datetime(events["bucket_ts"], utc=True)
    events = events.loc[
        (events["horizon"] == "9h")
        & (events["metric"] == "flow_ratio")
        & (events["signal_side"] == "sell_pressure")
        & events["market"].isin(SYMBOLS)
    ].copy()
    if events.empty:
        raise ValueError("no 9h sell-pressure flow_ratio events in input")

    all_event_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    data_quality: dict[str, dict[str, Any]] = {}
    window_start = events["bucket_ts"].min() + pd.Timedelta(minutes=15)
    window_end = events["bucket_ts"].max() + pd.Timedelta(minutes=30, microseconds=1)

    for market, symbol in SYMBOLS.items():
        market_events = events.loc[events["market"] == market].sort_values("bucket_ts")
        if market_events.empty:
            continue
        bars, funding, cadence = _load_market_data(bingx_dir, symbol)
        data_quality[market] = {
            "bars": int(len(bars)),
            "first_bar_utc": bars.index[0].isoformat(),
            "last_bar_utc": bars.index[-1].isoformat(),
            "gaps": 0,
            "funding_cadence_hours": cadence,
            "funding_events": int(len(funding)),
            "quote_volume_nonzero_rows": int((bars["quote_volume"] > 0).sum()),
            "trades_count_nonzero_rows": int((bars["trades_count"] > 0).sum()),
        }

        for _, event in market_events.iterrows():
            signal_time = pd.Timestamp(event["bucket_ts"])
            signal_close = signal_time + pd.Timedelta(minutes=15)
            prior_1h, prior_4h, _ = _context(bars, signal_close)
            entry_pos = bars.index.searchsorted(signal_close, side="left")
            if entry_pos >= len(bars):
                continue
            entry_time = bars.index[entry_pos]
            delay = (entry_time - signal_close).total_seconds() / 60.0
            if delay < 0 or delay > 15:
                continue
            row = _make_trade(
                bars, funding, cadence,
                entry_time=entry_time,
                signal_time=signal_close,
                signal_value=float(event["signal_value"]),
                prior_1h=prior_1h,
                prior_4h=prior_4h,
                symbol=symbol,
                market=market,
            )
            if row is not None:
                row["regime_1h_up_4h_down"] = bool(prior_1h > 0 and prior_4h < 0)
                all_event_rows.append(row)

        # Price-only matched-regime baseline, sampled without overlapping 9h windows.
        eligible_times = bars.index[(bars.index >= window_start) & (bars.index < window_end)]
        for entry_time in eligible_times:
            # Matched price-only control: assume the same conservative 15m
            # signal-to-entry delay as the coarsest candidate events.
            pseudo_signal_close = entry_time - pd.Timedelta(minutes=15)
            prior_1h, prior_4h, _ = _context(bars, pseudo_signal_close)
            if not (prior_1h > 0 and prior_4h < 0):
                continue
            row = _make_trade(
                bars, funding, cadence,
                entry_time=entry_time,
                signal_time=pseudo_signal_close,
                signal_value=None,
                prior_1h=prior_1h,
                prior_4h=prior_4h,
                symbol=symbol,
                market=market,
            )
            if row is not None:
                baseline_rows.append(row)

    all_events = pd.DataFrame(all_event_rows)
    candidate = all_events.loc[
        all_events["regime_1h_up_4h_down"]
        & all_events["funding_coverage"]
    ].copy()
    candidate = _non_overlapping(candidate, "entry_time")
    baseline_frame = pd.DataFrame(baseline_rows)
    baseline_frame = baseline_frame.loc[baseline_frame["funding_coverage"]].copy()
    baseline = _non_overlapping(baseline_frame, "entry_time")

    all_events.to_csv(out_dir / "wallet_flow_bingx_all_events.csv", index=False)
    candidate.to_csv(out_dir / "wallet_flow_bingx_candidate_events.csv", index=False)
    baseline.to_csv(out_dir / "wallet_flow_bingx_regime_baseline.csv", index=False)

    per_market: dict[str, Any] = {}
    markets_with_candidate: list[str] = []
    for market in SYMBOLS:
        c = candidate.loc[candidate["market"] == market]
        b = baseline.loc[baseline["market"] == market]
        if len(c) >= 5 and len(b.loc[b["funding_coverage"]]) >= 5:
            markets_with_candidate.append(market)
        diff = None
        if len(c) and len(b):
            diff = {
                "mean_net_base_pct": float(c["net_base_pct"].mean() - b["net_base_pct"].mean()),
                "daily_cluster_bootstrap_95pct_ci": _bootstrap_mean_difference(c, b),
            }
        per_market[market] = {
            "candidate": _stats(c),
            "matched_regime_baseline": _stats(b),
            "candidate_minus_baseline": diff,
        }

    equal_market_candidate = None
    equal_market_baseline = None
    equal_market_difference = None
    if markets_with_candidate:
        equal_market_candidate = float(np.mean([
            candidate.loc[candidate["market"] == market, "net_base_pct"].mean()
            for market in markets_with_candidate
        ]))
        equal_market_baseline = float(np.mean([
            baseline.loc[baseline["market"] == market, "net_base_pct"].mean()
            for market in markets_with_candidate
        ]))
        # Bootstrap equal-market means by UTC day, keeping market weights equal.
        days = sorted(
            set(pd.to_datetime(candidate["entry_time"], utc=True).dt.floor("D"))
            | set(pd.to_datetime(baseline["entry_time"], utc=True).dt.floor("D"))
        )
        c_groups = {
            (market, day): group["net_base_pct"].to_numpy()
            for (market, day), group in candidate.assign(
                day=pd.to_datetime(candidate["entry_time"], utc=True).dt.floor("D")
            ).groupby(["market", "day"])
        }
        b_groups = {
            (market, day): group["net_base_pct"].to_numpy()
            for (market, day), group in baseline.assign(
                day=pd.to_datetime(baseline["entry_time"], utc=True).dt.floor("D")
            ).groupby(["market", "day"])
        }
        rng = np.random.default_rng(20261120)
        diffs = []
        for _ in range(5000):
            chosen = rng.choice(days, size=len(days), replace=True)
            c_means, b_means = [], []
            for market in markets_with_candidate:
                c_parts = [c_groups[(market, day)] for day in chosen if (market, day) in c_groups]
                b_parts = [b_groups[(market, day)] for day in chosen if (market, day) in b_groups]
                if c_parts and b_parts:
                    c_means.append(float(np.concatenate(c_parts).mean()))
                    b_means.append(float(np.concatenate(b_parts).mean()))
            if len(c_means) == len(markets_with_candidate):
                diffs.append(float(np.mean(c_means) - np.mean(b_means)))
        if diffs:
            equal_market_difference = {
                "mean_net_base_pct": equal_market_candidate - equal_market_baseline,
                "daily_cluster_bootstrap_95pct_ci": [
                    float(v) for v in np.quantile(diffs, [0.025, 0.5, 0.975])
                ],
            }

    report = {
        "status": "EXPLORATORY_CROSS_VENUE_EVENT_REPLAY_NOT_OOS",
        "signal_definition": {
            "source": "Hyperliquid cohort flow",
            "horizon": "9h",
            "metric": "flow_ratio",
            "side": "sell_pressure",
            "wallet_minimum": 3,
            "price_regime": "BingX prior 1h return > 0 and prior 4h return < 0",
        },
        "execution_proxy": {
            "entry": "first BingX 30m bar open at or after the 15m signal candle close",
            "entry_delay_minutes_max": 15,
            "exit": "close of the BingX 30m bar ending exactly 9h after entry",
            "holding_period": "9h",
            "base_cost_bps": BASE_COST_BPS,
            "stress_cost_multiplier": 1.5,
            "funding": "actual archived BingX funding settlements; positive funding is credited to shorts",
            "baseline": "price-only 1h-up/4h-down regime, greedy non-overlapping 9h entries",
        },
        "data_window": {
            "start_utc": window_start.isoformat(),
            "end_utc": window_end.isoformat(),
            "data_quality": data_quality,
        },
        "counts": {
            "hyperliquid_sell_flow_events": int(len(all_events)),
            "bingx_price_regime_events": int(all_events["regime_1h_up_4h_down"].sum()),
            "candidate_non_overlapping_events": int(len(candidate)),
            "matched_regime_baseline_non_overlapping_events": int(len(baseline)),
            "markets_with_at_least_5_candidate_events": markets_with_candidate,
        },
        "candidate_equal_market_mean_net_base_pct": equal_market_candidate,
        "baseline_equal_market_mean_net_base_pct": equal_market_baseline,
        "equal_market_candidate_minus_baseline": equal_market_difference,
        "per_market": per_market,
        "limitations": [
            "This is a discovery-sample event replay, not untouched OOS validation.",
            "BingX 30m candles are a coarse execution proxy for a 15m Hyperliquid flow signal; entry is delayed 0-15m.",
            "BingX quote_volume and trades_count are zero in the frozen archive; no BingX volume/taker confirmation is available.",
            "The 37-day Hyperliquid cohort flow data does not identify Open Short versus Close Long or all liquidation cases.",
            "The replay uses a fixed 9h time exit only; no stop/target or portfolio-level risk simulation is included.",
            "No strategy profitability claim is supported by this sample.",
        ],
    }
    (out_dir / "wallet_flow_bingx_replay_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--bingx-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/wallet_flow_bingx"))
    args = parser.parse_args()
    report = replay(args.events, args.bingx_dir, args.out_dir)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
