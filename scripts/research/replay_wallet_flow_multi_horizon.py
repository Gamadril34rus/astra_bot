#!/usr/bin/env python3
"""Multi-horizon BingX replay for the frozen Hyperliquid sell-flow event rule.

This is a fixed-rule discovery replay, not an OOS backtest. The candidate event
set is selected once from the 9h flow-ratio signal and 1h-up/4h-down regime;
the same non-overlapping entries are evaluated at every holding horizon.
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
MAX_HOLD = pd.Timedelta(hours=9)
HORIZONS_MINUTES = (30, 60, 120, 240, 540)


def load_market_data(
    data_dir: Path, symbol: str
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    bars = pd.read_csv(data_dir / f"{symbol}_30m.csv")
    bars["open_dt"] = pd.to_datetime(bars["open_time"], unit="ms", utc=True)
    bars = bars.sort_values("open_dt").set_index("open_dt")
    if bars.index.has_duplicates:
        raise ValueError(f"{symbol}: duplicate 30m bar timestamps")
    if not bars.index.to_series().diff().dropna().eq(BAR).all():
        raise ValueError(f"{symbol}: 30m archive contains gaps")
    if (bars[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"{symbol}: non-positive OHLC value")

    funding = pd.read_csv(data_dir / f"funding_{symbol}.csv")
    funding["fund_dt"] = pd.to_datetime(
        funding["funding_time"], unit="ms", utc=True
    )
    funding = funding.sort_values("fund_dt")
    if funding["fund_dt"].duplicated().any():
        raise ValueError(f"{symbol}: duplicate funding timestamps")
    diffs = (
        funding["fund_dt"].diff().dt.total_seconds().div(3600).dropna()
    )
    cadence_values = diffs[diffs <= 24]
    if cadence_values.empty:
        raise ValueError(f"{symbol}: cannot infer funding cadence")
    return bars, funding, float(cadence_values.median())


def closed_context(
    bars: pd.DataFrame, signal_close: pd.Timestamp
) -> tuple[float, float] | None:
    index = bars.index
    position = index.searchsorted(signal_close - BAR, side="right") - 1
    if position < 8:
        return None
    close = float(bars.iloc[position]["close"])
    close_1h = float(bars.iloc[position - 2]["close"])
    close_4h = float(bars.iloc[position - 8]["close"])
    if min(close, close_1h, close_4h) <= 0:
        return None
    return (
        100.0 * (close / close_1h - 1.0),
        100.0 * (close / close_4h - 1.0),
    )


def funding_for_short(
    funding: pd.DataFrame,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    cadence_hours: float,
    horizon_minutes: int,
) -> tuple[float | None, bool, int]:
    inside = funding.loc[
        (funding["fund_dt"] > entry_time)
        & (funding["fund_dt"] <= exit_time)
    ]
    before = funding.loc[funding["fund_dt"] <= entry_time, "fund_dt"]
    after = funding.loc[funding["fund_dt"] > exit_time, "fund_dt"]
    if before.empty or after.empty:
        return None, False, len(inside)
    nearby = funding.loc[
        (funding["fund_dt"] >= before.iloc[-1])
        & (funding["fund_dt"] <= after.iloc[0]),
        "fund_dt",
    ]
    gaps = nearby.diff().dt.total_seconds().div(3600).dropna()
    expected_minimum = int((horizon_minutes / 60) // cadence_hours)
    covered = (
        len(inside) >= expected_minimum
        and not gaps.empty
        and float(gaps.max()) <= cadence_hours * 1.5 + 1e-9
    )
    if not covered:
        return None, False, len(inside)
    # Positive funding is credited to a short; negative funding is paid.
    return float(inside["rate"].sum() * 100.0), True, len(inside)


def non_overlapping_9h(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    last_entry: pd.Timestamp | None = None
    for row in sorted(rows, key=lambda item: item["entry_time"]):
        entry = row["entry_time"]
        if last_entry is None or entry - last_entry >= MAX_HOLD:
            selected.append(row)
            last_entry = entry
    return selected


def make_horizon_rows(
    rows: list[dict[str, Any]],
    bars: pd.DataFrame,
    funding: pd.DataFrame,
    cadence_hours: float,
    symbol: str,
) -> list[dict[str, Any]]:
    output = []
    cost_pct = BASE_COST_BPS[symbol] / 100.0
    for row in rows:
        entry_time = row["entry_time"]
        if entry_time not in bars.index:
            continue
        entry_price = float(bars.loc[entry_time, "open"])
        for horizon in HORIZONS_MINUTES:
            exit_open = entry_time + pd.Timedelta(minutes=horizon) - BAR
            if exit_open not in bars.index:
                continue
            exit_time = entry_time + pd.Timedelta(minutes=horizon)
            exit_price = float(bars.loc[exit_open, "close"])
            if min(entry_price, exit_price) <= 0:
                continue
            funding_pct, covered, funding_events = funding_for_short(
                funding, entry_time, exit_time, cadence_hours, horizon
            )
            if not covered or funding_pct is None:
                continue
            gross_pct = (entry_price / exit_price - 1.0) * 100.0
            output.append({
                **row,
                "horizon_minutes": horizon,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "gross_short_return_pct": gross_pct,
                "funding_return_pct": funding_pct,
                "funding_events": funding_events,
                "base_cost_pct": cost_pct,
                "net_base_pct": gross_pct + funding_pct - cost_pct,
                "net_stress_pct": gross_pct + funding_pct - cost_pct * 1.5,
            })
    return output


def _stats(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"events": 0, "mean_net_base_pct": None}
    return {
        "events": len(frame),
        "mean_gross_pct": float(frame["gross_short_return_pct"].mean()),
        "mean_funding_pct": float(frame["funding_return_pct"].mean()),
        "mean_net_base_pct": float(frame["net_base_pct"].mean()),
        "mean_net_stress_pct": float(frame["net_stress_pct"].mean()),
        "median_net_base_pct": float(frame["net_base_pct"].median()),
        "hit_rate_pct": float((frame["net_base_pct"] > 0).mean() * 100.0),
        "mean_entry_delay_minutes": float(frame["entry_delay_minutes"].mean()),
    }


def _bootstrap_equal_market(
    frame: pd.DataFrame,
    markets: list[str],
    *,
    n_boot: int = 20_000,
    seed: int = 20261121,
) -> list[float] | None:
    if not markets:
        return None
    working = frame.copy()
    working["day"] = pd.to_datetime(
        working["entry_time"], utc=True
    ).dt.floor("D")
    days = sorted(working["day"].unique())
    if not days:
        return None
    groups = {
        (market, day, kind): group["net_base_pct"].to_numpy()
        for (market, day, kind), group in working.groupby(
            ["market", "day", "sample_type"]
        )
    }
    rng = np.random.default_rng(seed)
    differences = []
    for _ in range(n_boot):
        chosen = rng.choice(days, size=len(days), replace=True)
        market_differences = []
        for market in markets:
            candidate_parts = [
                groups[(market, day, "candidate")]
                for day in chosen
                if (market, day, "candidate") in groups
            ]
            baseline_parts = [
                groups[(market, day, "baseline")]
                for day in chosen
                if (market, day, "baseline") in groups
            ]
            if not candidate_parts or not baseline_parts:
                break
            market_differences.append(
                float(np.concatenate(candidate_parts).mean())
                - float(np.concatenate(baseline_parts).mean())
            )
        if len(market_differences) == len(markets):
            differences.append(float(np.mean(market_differences)))
    if not differences:
        return None
    return [
        float(value)
        for value in np.quantile(differences, [0.025, 0.5, 0.975])
    ]


def replay(
    events_path: Path,
    bingx_dir: Path,
    out_dir: Path,
    *,
    bootstrap_resamples: int = 20_000,
) -> dict[str, Any]:
    events = pd.read_csv(events_path)
    events["bucket_ts"] = pd.to_datetime(events["bucket_ts"], utc=True)
    events = events.loc[
        (events["horizon"] == "9h")
        & (events["metric"] == "flow_ratio")
        & (events["signal_side"] == "sell_pressure")
        & events["market"].isin(SYMBOLS)
    ].copy()
    if events.empty:
        raise ValueError("no 9h flow_ratio sell-pressure events in input")
    window_start = events["bucket_ts"].min() + pd.Timedelta(minutes=15)
    window_end = (
        events["bucket_ts"].max()
        + pd.Timedelta(minutes=30, microseconds=1)
    )

    result_rows: list[dict[str, Any]] = []
    market_meta: dict[str, Any] = {}
    candidate_markets: list[str] = []
    for market, symbol in SYMBOLS.items():
        bars, funding, cadence = load_market_data(bingx_dir, symbol)
        market_events = events.loc[
            events["market"] == market
        ].sort_values("bucket_ts")
        candidate_rows = []
        for event in market_events.itertuples(index=False):
            signal_close = event.bucket_ts + pd.Timedelta(minutes=15)
            context = closed_context(bars, signal_close)
            if context is None or not (context[0] > 0 and context[1] < 0):
                continue
            entry_position = bars.index.searchsorted(signal_close, side="left")
            if entry_position >= len(bars):
                continue
            entry_time = bars.index[entry_position]
            delay = (entry_time - signal_close).total_seconds() / 60.0
            if delay < 0 or delay > 15:
                continue
            candidate_rows.append({
                "market": market,
                "sample_type": "candidate",
                "signal_time": signal_close.isoformat(),
                "entry_time": entry_time,
                "entry_delay_minutes": delay,
                "signal_value": float(event.signal_value),
            })
        candidate_rows = non_overlapping_9h(candidate_rows)

        baseline_rows = []
        eligible_times = bars.index[
            (bars.index >= window_start) & (bars.index < window_end)
        ]
        for entry_time in eligible_times:
            pseudo_signal_close = entry_time - pd.Timedelta(minutes=15)
            context = closed_context(bars, pseudo_signal_close)
            if context is None or not (context[0] > 0 and context[1] < 0):
                continue
            baseline_rows.append({
                "market": market,
                "sample_type": "baseline",
                "signal_time": pseudo_signal_close.isoformat(),
                "entry_time": entry_time,
                "entry_delay_minutes": 15.0,
                "signal_value": None,
            })
        baseline_rows = non_overlapping_9h(baseline_rows)

        if len(candidate_rows) >= 5 and len(baseline_rows) >= 5:
            candidate_markets.append(market)
        result_rows.extend(make_horizon_rows(
            candidate_rows, bars, funding, cadence, symbol
        ))
        result_rows.extend(make_horizon_rows(
            baseline_rows, bars, funding, cadence, symbol
        ))
        market_meta[market] = {
            "bars": len(bars),
            "funding_events": len(funding),
            "funding_cadence_hours": cadence,
            "candidate_entries": len(candidate_rows),
            "baseline_entries": len(baseline_rows),
        }

    result = pd.DataFrame(result_rows)
    if result.empty:
        raise ValueError("no funding-covered candidate or baseline outcomes")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "wallet_flow_multi_horizon_events.csv"
    result.to_csv(csv_path, index=False)

    summaries = {}
    equal_market = {}
    for horizon in HORIZONS_MINUTES:
        horizon_frame = result.loc[
            result["horizon_minutes"] == horizon
        ].copy()
        market_rows = {}
        candidate_means = []
        baseline_means = []
        for market in SYMBOLS:
            candidate = horizon_frame.loc[
                (horizon_frame["market"] == market)
                & (horizon_frame["sample_type"] == "candidate")
            ]
            baseline = horizon_frame.loc[
                (horizon_frame["market"] == market)
                & (horizon_frame["sample_type"] == "baseline")
            ]
            market_rows[market] = {
                "candidate": _stats(candidate),
                "baseline": _stats(baseline),
                "candidate_minus_baseline_net_base_pp": (
                    float(candidate["net_base_pct"].mean()
                          - baseline["net_base_pct"].mean())
                    if len(candidate) and len(baseline) else None
                ),
            }
            if market in candidate_markets and len(candidate) and len(baseline):
                candidate_means.append(float(candidate["net_base_pct"].mean()))
                baseline_means.append(float(baseline["net_base_pct"].mean()))
        difference = (
            float(np.mean(candidate_means) - np.mean(baseline_means))
            if candidate_means else None
        )
        summaries[f"{horizon}m"] = market_rows
        equal_market[f"{horizon}m"] = {
            "markets": candidate_markets,
            "candidate_equal_market_mean_net_pct": (
                float(np.mean(candidate_means)) if candidate_means else None
            ),
            "baseline_equal_market_mean_net_pct": (
                float(np.mean(baseline_means)) if baseline_means else None
            ),
            "candidate_minus_baseline_pp": difference,
            "daily_cluster_bootstrap_95pct_ci_pp": _bootstrap_equal_market(
                horizon_frame,
                candidate_markets,
                n_boot=bootstrap_resamples,
                seed=20261121 + horizon,
            ),
        }

    report = {
        "status": "EXPLORATORY_MULTI_HORIZON_REPLAY_NOT_OOS",
        "signal": (
            "9h Hyperliquid cohort flow_ratio sell_pressure; BingX prior "
            "1h return > 0 and prior 4h return < 0"
        ),
        "entry": "first BingX 30m open at or after the completed 15m signal close, max 15m delay",
        "exit": "close of the BingX 30m bar ending at the selected holding horizon",
        "non_overlap": "candidate and baseline entries are greedily separated by at least 9h per market; same entries used for every horizon",
        "horizons_minutes": list(HORIZONS_MINUTES),
        "base_cost_bps_round_trip": BASE_COST_BPS,
        "stress_cost_multiplier": 1.5,
        "funding": "actual archived BingX funding; positive funding credited to shorts; settlement timestamps in (entry, exit]",
        "data_window": {
            "start_utc": window_start.isoformat(),
            "end_utc_exclusive": window_end.isoformat(),
        },
        "candidate_markets_in_equal_market_summary": candidate_markets,
        "market_data": market_meta,
        "equal_market_summary": equal_market,
        "per_market_summary": summaries,
        "limitations": [
            "The 37-day cohort flow_ratio is signed wallet-side trade notional, not verified Open Short inventory or aggressor-only flow.",
            "The current 24h position-direction audit is an interpretation check, not historical classification of the 37-day candidate events.",
            "The 100-wallet cohort is selected from a public leaderboard snapshot and is not a market-wide census.",
            "The replay is a discovery sample with no untouched OOS period.",
            "Base/stress costs are fixed assumptions; historical spread, market impact and actual account fee tier are unavailable.",
            "No stop/target or portfolio-level risk simulation is included.",
        ],
        "outputs": {"events_csv": str(csv_path)},
    }
    json_path = out_dir / "wallet_flow_multi_horizon_summary.json"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--bingx-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/wallet_flow_multi_horizon"))
    parser.add_argument("--bootstrap-resamples", type=int, default=20_000)
    args = parser.parse_args()
    report = replay(
        args.events,
        args.bingx_dir,
        args.out_dir,
        bootstrap_resamples=args.bootstrap_resamples,
    )
    print(json.dumps(report["equal_market_summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
