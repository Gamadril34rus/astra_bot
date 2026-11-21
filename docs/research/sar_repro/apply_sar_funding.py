#!/usr/bin/env python3
"""Apply archived BingX perpetual funding to SAR 15m trades."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SYMBOLS = {
    "BTC-USDT": "BTCUSDT",
    "ETH-USDT": "ETHUSDT",
    "SOL-USDT": "SOLUSDT",
}


def _metrics(values: pd.Series) -> dict[str, Any]:
    clean = values.dropna()
    if clean.empty:
        return {"trades": 0, "mean_net_bps": None, "profit_factor": None}
    wins = float(clean.loc[clean > 0].sum())
    losses = float(-clean.loc[clean < 0].sum())
    return {
        "trades": len(clean),
        "mean_net_bps": float(clean.mean() * 10_000.0),
        "median_net_bps": float(clean.median() * 10_000.0),
        "win_rate_pct": float((clean > 0).mean() * 100.0),
        "profit_factor": wins / losses if losses > 0 else None,
    }


def apply_funding(
    trades_path: Path,
    funding_dir: Path,
    out_dir: Path,
) -> dict[str, Any]:
    trades = pd.read_csv(trades_path)
    required = {"symbol", "side", "entry_ts", "exit_ts", "net_pnl_pct"}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"trade file missing columns: {sorted(missing)}")
    trades["entry_time"] = pd.to_datetime(
        trades["entry_ts"], unit="ms", utc=True
    )
    trades["exit_time"] = pd.to_datetime(
        trades["exit_ts"], unit="ms", utc=True
    )
    trades["hold_minutes"] = (
        trades["exit_time"] - trades["entry_time"]
    ).dt.total_seconds() / 60.0
    trades["funding_pnl_pct"] = np.nan
    trades["funding_events"] = 0
    trades["funding_covered"] = False
    trades["funding_exclusion_reason"] = "unknown_symbol"

    coverage = {}
    for symbol, filename in SYMBOLS.items():
        path = funding_dir / f"funding_{filename}.csv"
        funding = pd.read_csv(path)
        funding["funding_time_utc"] = pd.to_datetime(
            funding["funding_time"], unit="ms", utc=True
        )
        funding = funding.sort_values("funding_time_utc").reset_index(drop=True)
        if funding["funding_time_utc"].duplicated().any():
            raise ValueError(f"{symbol}: duplicate funding timestamps")
        times = funding["funding_time_utc"]
        diffs = times.diff().dt.total_seconds().div(3600).dropna()
        normal_diffs = diffs[diffs <= 24]
        if normal_diffs.empty:
            raise ValueError(f"{symbol}: cannot infer funding cadence")
        cadence_hours = float(normal_diffs.median())
        subset = trades.loc[trades["symbol"] == symbol]
        covered_count = 0
        for idx, trade in subset.iterrows():
            entry = trade["entry_time"]
            exit_time = trade["exit_time"]
            before_pos = int(times.searchsorted(entry, side="right") - 1)
            after_pos = int(times.searchsorted(exit_time, side="right"))
            if before_pos < 0 or after_pos >= len(times):
                trades.at[idx, "funding_exclusion_reason"] = "archive_boundary"
                continue
            window = times.iloc[before_pos : after_pos + 1]
            gaps = window.diff().dt.total_seconds().div(3600).dropna()
            if gaps.empty or float(gaps.max()) > cadence_hours * 1.5 + 1e-9:
                trades.at[idx, "funding_exclusion_reason"] = "funding_gap"
                continue
            inside = funding.loc[
                (funding["funding_time_utc"] > entry)
                & (funding["funding_time_utc"] <= exit_time)
            ]
            expected_minimum = int(
                (trade["hold_minutes"] / 60.0) // cadence_hours
            )
            if len(inside) < expected_minimum:
                trades.at[idx, "funding_exclusion_reason"] = "missing_settlement"
                continue
            rate_sum = float(inside["rate"].sum())
            sign = -1.0 if str(trade["side"]).lower() == "long" else 1.0
            funding_pct = sign * rate_sum
            trades.at[idx, "funding_pnl_pct"] = funding_pct
            trades.at[idx, "funding_events"] = len(inside)
            trades.at[idx, "funding_covered"] = True
            trades.at[idx, "funding_exclusion_reason"] = ""
            covered_count += 1
        coverage[symbol] = {
            "trades": len(subset),
            "funding_rows": len(funding),
            "first_funding_utc": times.iloc[0].isoformat(),
            "last_funding_utc": times.iloc[-1].isoformat(),
            "median_cadence_hours": cadence_hours,
            "covered_trades": covered_count,
        }

    covered = trades.loc[trades["funding_covered"]].copy()
    covered["net_with_funding_pct"] = (
        covered["net_pnl_pct"] + covered["funding_pnl_pct"]
    )
    full_metrics = _metrics(trades["net_pnl_pct"])
    covered_without_funding = _metrics(covered["net_pnl_pct"])
    covered_with_funding = _metrics(covered["net_with_funding_pct"])

    by_symbol = {}
    for symbol in SYMBOLS:
        group = covered.loc[covered["symbol"] == symbol]
        by_symbol[symbol] = {
            "without_funding": _metrics(group["net_pnl_pct"]),
            "with_funding": _metrics(group["net_with_funding_pct"]),
            "mean_funding_bps": (
                float(group["funding_pnl_pct"].mean() * 10_000.0)
                if not group.empty else None
            ),
        }
    by_side = {}
    for side in ("long", "short"):
        group = covered.loc[covered["side"].str.lower() == side]
        by_side[side] = {
            "without_funding": _metrics(group["net_pnl_pct"]),
            "with_funding": _metrics(group["net_with_funding_pct"]),
            "mean_funding_bps": (
                float(group["funding_pnl_pct"].mean() * 100.0)
                if not group.empty else None
            ),
        }

    if covered.empty:
        holding_time = {
            "median": None,
            "p90": None,
            "share_30m_to_9h_pct": None,
        }
    else:
        holding_time = {
            "median": float(covered["hold_minutes"].median()),
            "p90": float(covered["hold_minutes"].quantile(0.9)),
            "share_30m_to_9h_pct": float(
                covered["hold_minutes"].between(30, 540).mean() * 100.0
            ),
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "sar_15m_funding_adjusted_trades.csv"
    covered.to_csv(csv_path, index=False)
    report = {
        "status": "FUNDING_ADJUSTED_SUBSET_ONLY",
        "funding_sign": "long pays positive funding; short receives positive funding",
        "funding_interval": "(entry_time, exit_time]",
        "full_sample_without_funding": full_metrics,
        "funding_covered_subset_without_funding": covered_without_funding,
        "funding_covered_subset_with_funding": covered_with_funding,
        "funding_coverage": {
            "raw_trades": len(trades),
            "covered_trades": len(covered),
            "coverage_pct": float(len(covered) / len(trades) * 100.0),
            "excluded_by_reason": {
                str(key): int(value)
                for key, value in trades.loc[
                    ~trades["funding_covered"],
                    "funding_exclusion_reason",
                ].value_counts().items()
            },
            "by_symbol": coverage,
        },
        "by_symbol": by_symbol,
        "by_side": by_side,
        "holding_time_minutes": holding_time,
        "outputs": {"trades_csv": str(csv_path)},
        "limitations": [
            "Funding archive ends before the latest 15m candle window; trades outside complete funding coverage are excluded.",
            "This trade-level adjustment does not rerun portfolio sizing or equity compounding with funding cashflows.",
            "The original portfolio profile metrics are therefore reported separately and remain pre-funding.",
        ],
    }
    (out_dir / "sar_15m_funding_adjusted_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--funding-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    report = apply_funding(args.trades, args.funding_dir, args.out_dir)
    print(json.dumps({
        "full_sample_without_funding": report["full_sample_without_funding"],
        "funding_covered_subset_with_funding": report[
            "funding_covered_subset_with_funding"
        ],
        "funding_coverage": report["funding_coverage"]["coverage_pct"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
