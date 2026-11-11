#!/usr/bin/env python3
"""Research: Funding Extreme Fade (BingX USDT-M).

Idea: fade crowded perpetual funding extremes confirmed by RSI.
Data: data/bingx_usdm/{SYMBOL}_{1h|30m}.csv + funding_{SYMBOL}.csv
  (branch research/positioning-data).

Execution: signal on close i, enter open i+1; stop priority; fees+slip.
RESEARCH ONLY — do not wire into live Zeus without owner word.

Example:
  python scripts/research/funding_extreme_fade.py --data-dir data/bingx_usdm --symbol SOLUSDT
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

FEE = 0.0004
SLIPPAGE = 0.0002
RISK_FRAC = 0.01
CAPITAL0 = 10_000.0


def load_ohlc(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def load_funding(path: Path) -> pd.DataFrame:
    f = pd.read_csv(path)
    f["ts"] = pd.to_datetime(f["funding_time"], unit="ms", utc=True)
    f["rate"] = pd.to_numeric(f["rate"], errors="coerce")
    return f.dropna().sort_values("ts").set_index("ts")[["rate"]]


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def fund_z(rate: pd.Series, window: int) -> pd.Series:
    mu = rate.rolling(window, min_periods=max(24, window // 8)).mean()
    sd = rate.rolling(window, min_periods=max(24, window // 8)).std().replace(0, np.nan)
    return ((rate - mu) / sd).fillna(0.0)


@dataclass
class Outcome:
    n: int
    pf: float
    wr: float
    net: float
    ret_pct: float
    max_dd_pct: float


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    stop_atr: float = 1.2,
    take_rr: float = 1.8,
    max_hold: int = 24,
) -> Outcome:
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    a = atr(df).values
    sig = signals.reindex(df.index).fillna(0).astype(int).values

    equity = CAPITAL0
    peak = CAPITAL0
    max_dd = 0.0
    pnls: list[float] = []

    pos = 0
    entry = 0.0
    sl = 0.0
    tp = 0.0
    entry_i = -1
    risk_cash = 0.0

    def close_pos(px: float) -> None:
        nonlocal equity, pos
        if pos == 0:
            return
        ret = (px / entry - 1.0) if pos == 1 else (1.0 - px / entry)
        stop_dist = abs(entry - sl) if sl else entry * 0.01
        notional = risk_cash / max(stop_dist / entry, 1e-8)
        pnl = (ret - 2 * FEE - 2 * SLIPPAGE) * notional
        equity += pnl
        pnls.append(float(pnl))
        pos = 0

    for i in range(1, len(df)):
        target = int(sig[i - 1])
        if pos != 0:
            hit_sl = (pos == 1 and l[i] <= sl) or (pos == -1 and h[i] >= sl)
            hit_tp = (pos == 1 and h[i] >= tp) or (pos == -1 and l[i] <= tp)
            if hit_sl:
                close_pos(sl)
            elif hit_tp:
                close_pos(tp)
            elif i - entry_i >= max_hold:
                close_pos(c[i])

        if pos == 0 and target != 0 and a[i - 1] > 0 and np.isfinite(a[i - 1]):
            pos = target
            entry = float(o[i])
            entry_i = i
            risk_cash = RISK_FRAC * max(equity, 100.0)
            dist = stop_atr * float(a[i - 1])
            if pos == 1:
                sl = entry - dist
                tp = entry + take_rr * dist
            else:
                sl = entry + dist
                tp = entry - take_rr * dist

        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    if not pnls:
        return Outcome(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    arr = np.asarray(pnls, dtype=float)
    wins = float(arr[arr > 0].sum())
    losses = float((-arr[arr <= 0]).sum())
    pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 0.0)
    return Outcome(
        n=len(arr),
        pf=round(pf, 3),
        wr=round(float((arr > 0).mean()), 3),
        net=round(float(arr.sum()), 2),
        ret_pct=round(float(arr.sum() / CAPITAL0 * 100), 2),
        max_dd_pct=round(float(max_dd * 100), 2),
    )


def build_signals(
    df: pd.DataFrame,
    z: float,
    rsi_lo: float,
    rsi_hi: float,
    z_window: int,
) -> pd.Series:
    r = rsi(df["close"], 14)
    fz = fund_z(df["rate"], z_window)
    long = (fz < -z) & (r < rsi_lo)
    short = (fz > z) & (r > rsi_hi)
    return pd.Series(np.where(long, 1, np.where(short, -1, 0)), index=df.index)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=Path("data/bingx_usdm"))
    p.add_argument("--symbol", default="SOLUSDT")
    p.add_argument("--timeframe", default="1h", choices=("1h", "30m"))
    p.add_argument("--z", type=float, default=2.5)
    p.add_argument("--rsi-lo", type=float, default=30.0)
    p.add_argument("--rsi-hi", type=float, default=70.0)
    p.add_argument("--stop-atr", type=float, default=1.2)
    p.add_argument("--take-rr", type=float, default=1.8)
    p.add_argument("--max-hold", type=int, default=24)
    p.add_argument("--out", type=Path, default=Path("reports/research/funding_extreme_fade.json"))
    args = p.parse_args()

    ohlc_path = args.data_dir / f"{args.symbol}_{args.timeframe}.csv"
    fund_path = args.data_dir / f"funding_{args.symbol}.csv"
    if not ohlc_path.exists():
        raise SystemExit(
            f"Missing {ohlc_path}. Use branch research/positioning-data or --data-dir."
        )
    if not fund_path.exists():
        raise SystemExit(f"Missing {fund_path}")

    df = load_ohlc(ohlc_path)
    fund = load_funding(fund_path)
    df = df.join(fund.reindex(df.index, method="ffill"), how="left")
    df["rate"] = df["rate"].fillna(0.0)

    z_window = 21 * (24 if args.timeframe == "1h" else 48)
    sig = build_signals(df, args.z, args.rsi_lo, args.rsi_hi, z_window)

    full = run_backtest(df, sig, args.stop_atr, args.take_rr, args.max_hold)
    mid = df.index[len(df) // 2]
    half1 = run_backtest(df.loc[:mid], sig, args.stop_atr, args.take_rr, args.max_hold)
    half2 = run_backtest(df.loc[mid:], sig, args.stop_atr, args.take_rr, args.max_hold)

    folds = []
    n = len(df)
    fold = max(n // 4, 1)
    idx = df.index
    for i in range(4):
        a = idx[i * fold]
        b = idx[min((i + 1) * fold, n - 1)]
        o = run_backtest(df.loc[a:b], sig, args.stop_atr, args.take_rr, args.max_hold)
        folds.append({"from": str(a), "to": str(b), **asdict(o)})

    report = {
        "strategy": "funding_extreme_fade",
        "research_only": True,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "params": {
            "fund_z": args.z,
            "rsi_lo": args.rsi_lo,
            "rsi_hi": args.rsi_hi,
            "stop_atr": args.stop_atr,
            "take_rr": args.take_rr,
            "max_hold": args.max_hold,
            "fee": FEE,
            "slippage": SLIPPAGE,
            "risk_frac": RISK_FRAC,
        },
        "range": {"start": str(df.index[0]), "end": str(df.index[-1]), "bars": int(len(df))},
        "full": asdict(full),
        "half1": asdict(half1),
        "half2": asdict(half2),
        "folds": folds,
        "notes": [
            "Small sample on SOL 1h; fold quality uneven.",
            "Not stable on ETH with same params.",
            "Do not enable on live Zeus without owner approval.",
        ],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
