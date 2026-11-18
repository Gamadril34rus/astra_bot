#!/usr/bin/env python3
"""Reproduce the exact 15m Parabolic SAR baseline on a frozen Binance proxy dataset.

This is research-only. Binance USD-M data is a cross-exchange proxy, not BingX history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

import sar_backtest_engine as engine
from sar_funding import apply_funding_to_trades

INTERVAL_MS = 15 * 60 * 1000
EXPECTED_BARS = 365 * 24 * 4
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
SCENARIOS = (
    ("no_cost", 0.0, 0.0),
    ("fee_0.02pct_no_slippage", 0.0002, 0.0),
    ("fee_0.05pct_no_slippage", 0.0005, 0.0),
    ("baseline_fee_0.05pct_slip_0.10pct", 0.0005, 0.001),
    ("stress_1.5x_baseline_costs", 0.00075, 0.0015),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_dataset(data_dir: Path) -> tuple[dict[str, pd.DataFrame], dict[str, list[dict]], dict]:
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    if "Binance USD-M" not in manifest.get("source", ""):
        raise ValueError("Dataset source is not explicitly identified as Binance USD-M")
    if manifest.get("interval") != "15m":
        raise ValueError("Expected a 15m dataset")

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    funding_by_symbol: dict[str, list[dict]] = {}
    coverage = {}
    for symbol in SYMBOLS:
        meta = manifest["symbols"][symbol]
        kline_path = data_dir / meta["klines"]["file"]
        funding_path = data_dir / meta["funding"]["file"]
        if sha256_file(kline_path) != meta["klines"]["file_sha256"]:
            raise ValueError(f"{symbol}: candle file SHA256 mismatch")
        if sha256_file(funding_path) != meta["funding"]["file_sha256"]:
            raise ValueError(f"{symbol}: funding file SHA256 mismatch")

        frame = pd.read_csv(kline_path)
        frame["timestamp"] = frame["open_time"].astype("int64")
        frame["close_ts"] = frame["timestamp"] + INTERVAL_MS
        if len(frame) != EXPECTED_BARS:
            raise ValueError(f"{symbol}: expected {EXPECTED_BARS} bars, got {len(frame)}")
        if frame["timestamp"].duplicated().any():
            raise ValueError(f"{symbol}: duplicate candle timestamps")
        if not (frame["timestamp"].diff().dropna() == INTERVAL_MS).all():
            raise ValueError(f"{symbol}: candle gap or irregular timestamp")
        bars_by_symbol[symbol] = frame

        funding_frame = pd.read_csv(funding_path)
        if len(funding_frame) != 1095:
            raise ValueError(f"{symbol}: expected 1095 funding events, got {len(funding_frame)}")
        if funding_frame["mark_price"].isna().any() or (funding_frame["mark_price"] <= 0).any():
            raise ValueError(f"{symbol}: missing or invalid funding mark price")
        funding_by_symbol[symbol] = [
            {
                "symbol": symbol,
                "fundingTime": int(row.funding_time),
                "fundingRate": float(row.funding_rate),
                "markPrice": float(row.mark_price),
            }
            for row in funding_frame.itertuples(index=False)
        ]
        coverage[symbol] = {
            "candles": len(frame),
            "candle_gaps": 0,
            "candle_duplicates": 0,
            "funding_events": len(funding_frame),
            "funding_mark_fallbacks": int(
                funding_frame["mark_price_source"].str.contains(
                    "previous", case=False, na=False
                ).sum()
            ),
            "funding_mark_sources": {
                str(key): int(value)
                for key, value in funding_frame["mark_price_source"].value_counts().items()
            },
        }
    return bars_by_symbol, funding_by_symbol, coverage


def run_trades(
    bars_by_symbol: dict[str, pd.DataFrame],
    funding_by_symbol: dict[str, list[dict]],
    taker_fee: float,
    slippage: float,
    init_trend: int = 1,
) -> list[dict]:
    engine.TAKER = taker_fee
    engine.SLIP = slippage
    trades = []
    for symbol, frame in bars_by_symbol.items():
        raw = engine.extract_trades(
            frame,
            symbol,
            min_prev=0,
            init_trend=init_trend,
            force_close_end=True,
            warmup=50,
            interval_ms=INTERVAL_MS,
        )
        trades.extend(apply_funding_to_trades(raw, funding_by_symbol))
    return sorted(trades, key=lambda row: (row["entry_ts"], row["symbol"], row["exit_ts"]))


def metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    frame = pd.DataFrame(trades)
    pnl = frame["net_pnl_pct"].astype(float)
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    holding = (frame["exit_ts"] - frame["entry_ts"]) / 60_000
    return {
        "trades": int(len(frame)),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "winrate_pct": float((pnl > 0).mean() * 100),
        "profit_factor_net": gains / losses if losses else None,
        "avg_net_bps": float(pnl.mean() * 10_000),
        "median_net_bps": float(pnl.median() * 10_000),
        "avg_gross_bps_after_slippage": float(frame["gross_pct"].mean() * 10_000),
        "avg_funding_bps": float(frame["funding_pnl_pct"].mean() * 10_000),
        "trades_with_funding": int((frame["funding_events"] > 0).sum()),
        "funding_events_charged": int(frame["funding_events"].sum()),
        "holding_median_minutes": float(holding.median()),
        "holding_mean_minutes": float(holding.mean()),
        "holding_p90_minutes": float(holding.quantile(0.9)),
        "holding_max_hours": float(holding.max() / 60),
    }


def grouped_metrics(frame: pd.DataFrame, columns: list[str]) -> list[dict]:
    results = []
    for key, group in frame.groupby(columns, sort=True):
        keys = key if isinstance(key, tuple) else (key,)
        row = dict(zip(columns, keys, strict=True))
        row.update(metrics(group.to_dict("records")))
        results.append(row)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    bars, funding, coverage = load_dataset(args.data_dir)
    scenarios = {}
    baseline_trades = None
    baseline_fee = 0.0005
    baseline_slippage = 0.001
    for name, fee, slip in SCENARIOS:
        trades = run_trades(bars, funding, fee, slip)
        scenarios[name] = {
            "taker_fee_per_side": fee,
            "slippage_per_side": slip,
            "overall": metrics(trades),
            "by_symbol_and_side": grouped_metrics(pd.DataFrame(trades), ["symbol", "side"]),
        }
        if name == "baseline_fee_0.05pct_slip_0.10pct":
            baseline_trades = trades

    if baseline_trades is None:
        raise RuntimeError("Baseline scenario was not generated")
    baseline_frame = pd.DataFrame(baseline_trades)
    baseline_frame["entry_month"] = pd.to_datetime(
        baseline_frame["entry_ts"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    monthly = grouped_metrics(baseline_frame, ["entry_month"])
    baseline_frame.to_csv(args.out_dir / "baseline_trades.csv", index=False)
    pd.DataFrame(monthly).to_csv(args.out_dir / "baseline_monthly.csv", index=False)
    breakdown = grouped_metrics(baseline_frame, ["symbol", "side"])
    pd.DataFrame(breakdown).to_csv(args.out_dir / "baseline_symbol_side.csv", index=False)

    manifest = json.loads((args.data_dir / "manifest.json").read_text(encoding="utf-8"))
    start = pd.Timestamp(manifest["start_utc"])
    end = pd.Timestamp(manifest["end_utc_exclusive"])
    days = (end - start).days
    cut_60 = start + pd.Timedelta(days=round(days * 0.6))
    cut_80 = start + pd.Timedelta(days=round(days * 0.8))
    baseline_frame["entry_datetime"] = pd.to_datetime(
        baseline_frame["entry_ts"], unit="ms", utc=True
    )
    splits = {}
    for label, lower, upper in (
        ("in_sample_60pct", start, cut_60),
        ("validation_20pct", cut_60, cut_80),
        ("out_of_sample_20pct", cut_80, end),
    ):
        subset = baseline_frame[
            (baseline_frame["entry_datetime"] >= lower)
            & (baseline_frame["entry_datetime"] < upper)
        ]
        splits[label] = {
            "start_utc": lower.isoformat(),
            "end_utc_exclusive": upper.isoformat(),
            **metrics(subset.to_dict("records")),
        }

    reverse_seed = run_trades(bars, funding, baseline_fee, baseline_slippage, init_trend=-1)
    reverse_metrics = metrics(reverse_seed)
    summary = {
        "source": "Binance USD-M perpetuals; cross-exchange proxy, not BingX",
        "period_start_utc": start.isoformat(),
        "period_end_exclusive_utc": end.isoformat(),
        "interval": "15m",
        "symbols": list(SYMBOLS),
        "sar": {"start": 0.02, "increment": 0.02, "maximum": 0.2},
        "execution": {
            "entry": "next candle open after a completed SAR trend-flip candle",
            "stop": "previous completed candle SAR; gap-through fills at candle open",
            "intrabar_stop_funding_cutoff": "conservatively at stop candle open",
            "warmup_bars": 50,
            "parameter_optimization": False,
        },
        "data_coverage": coverage,
        "cost_scenarios": scenarios,
        "baseline_monthly_positive_months": sum(
            row["avg_net_bps"] > 0 for row in monthly
        ),
        "baseline_monthly_count": len(monthly),
        "chronological_splits": splits,
        "initial_trend_minus_one_sensitivity": reverse_metrics,
        "notes": [
            "Funding uses Binance historical settlement rates and mark prices.",
            "Fallback funding mark prices are counted by source in data_coverage.",
            "No leverage or margin optimization is performed on a negative baseline.",
            "Results are not BingX execution results and do not authorize runtime changes.",
        ],
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "baseline": scenarios["baseline_fee_0.05pct_slip_0.10pct"]["overall"],
        "no_cost": scenarios["no_cost"]["overall"],
        "low_fee_no_slippage": scenarios["fee_0.02pct_no_slippage"]["overall"],
        "positive_months": summary["baseline_monthly_positive_months"],
        "splits": splits,
        "output": str(args.out_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
