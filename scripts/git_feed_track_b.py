#!/usr/bin/env python3
"""Track B replay on provenance-labelled OHLCV files obtained via git clone.

Read-only with respect to models/. The caller supplies a cloned repository;
this script neither downloads data nor tunes strategy parameters. Windows are
fixed at 30 days for 1h and 60 days for 4h, ending at each file's last bar.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astra_bot.backtester.engine import BacktestConfig, BacktestEngine
from astra_bot.strategies import (
    BookBreakoutStrategy, MeanReversionStrategy, MomentumStrategy,
    PullbackStrategy, Scalp5mStrategy, ScalpStrategy,
    TimeSeriesMomentumStrategy,
)

SEED = 20260913
SYMBOLS = ("BTC", "ETH", "SOL")
TIMEFRAMES = {"1h": 30, "4h": 60}
STRATEGIES = {
    "book_breakout": BookBreakoutStrategy,
    "mean_reversion": MeanReversionStrategy,
    "momentum": MomentumStrategy,
    "pullback": PullbackStrategy,
    "scalp": ScalpStrategy,
    "scalp5m": Scalp5mStrategy,
    "ts_momentum": TimeSeriesMomentumStrategy,
}


def load(path: Path, days: int) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        raise ValueError(f"{path}: missing {sorted(required-set(df.columns))}")
    dt = pd.to_datetime(df["timestamp"], utc=True)
    df = df.assign(open_time=(dt.astype("int64") // 1_000_000)).sort_values("open_time")
    df = df.drop_duplicates("open_time")
    end = int(df.open_time.max()) + 1
    start = end - days * 86_400_000
    return df[df.open_time >= start].reset_index(drop=True)


def trade_r(trade) -> float | None:
    risk = abs(float(trade.entry_price) - float(trade.stop_loss)) * float(trade.quantity)
    return float(trade.pnl) / risk if risk > 0 else None


def bootstrap_ci(values: list[float], reps: int = 20_000) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(SEED)
    samples = sorted(mean(rng.choices(values, k=len(values))) for _ in range(reps))
    return [samples[int(.025 * reps)], samples[int(.975 * reps)-1]]


def run(df: pd.DataFrame, symbol: str, timeframe: str, name: str, factory) -> dict:
    start = datetime.fromtimestamp(int(df.open_time.min()) / 1000, UTC)
    end = datetime.fromtimestamp(int(df.open_time.max()) / 1000, UTC)
    cfg = BacktestConfig(
        symbol=f"{symbol}/USDT", timeframe=timeframe, start_date=start, end_date=end,
        initial_capital=Decimal("10000"), max_open_positions=1,
        risk_config={"risk_per_trade": "0.004", "drawdown_adaptation": [{"drawdown": 0, "risk_multiplier": 1.0}]},
    )
    engine = BacktestEngine(cfg)
    engine.add_strategy(name, factory())
    engine.load_candles_from_dataframe(df[["open_time", "open", "high", "low", "close", "volume"]])
    result = engine.run()
    rs = [r for t in result.trades if (r := trade_r(t)) is not None]
    gp = sum(max(0, r) for r in rs); gl = -sum(min(0, r) for r in rs)
    return {
        "bars": len(df), "start": start.isoformat(), "end": end.isoformat(),
        "n": len(rs), "sum_r": sum(rs), "mean_r": mean(rs) if rs else None,
        "gross_profit_r": gp, "gross_loss_r": gl,
        "pf_r": gp/gl if gl else None,
        "bootstrap_95_ci_mean_r": bootstrap_ci(rs),
        "engine_net_profit": float(result.net_profit),
        "engine_max_drawdown_pct": float(result.max_drawdown_pct),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True,
                    help="directory containing Binance_{BTC,ETH,SOL}USDT_{1h,4h}.csv")
    ap.add_argument("--provenance", required=True,
                    help="human-readable source repository and commit")
    args = ap.parse_args()
    output = {"seed": SEED, "provenance": args.provenance, "runs": {}, "failures": {}}
    for tf, days in TIMEFRAMES.items():
        for sym in SYMBOLS:
            path = args.data_dir / f"Binance_{sym}USDT_{tf}.csv"
            df = load(path, days)
            for name, factory in STRATEGIES.items():
                key = f"{sym}USDT|{tf}|{name}"
                try:
                    output["runs"][key] = run(df, sym, tf, name, factory)
                except Exception as exc:
                    output["failures"][key] = f"{type(exc).__name__}: {exc}"
    output["honest_label"] = (
        "Exploratory only: one short window, many hypotheses, IID bootstrap; "
        "Binance feed and backtester semantics are not comparable to BingX paper."
    )
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
