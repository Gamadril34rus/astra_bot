#!/usr/bin/env python3
"""Reproduce residual-revert protocol splits on BingX 30m archives.

Expects data/bingx_usdm/{SYMBOL}_30m.csv from branch research/positioning-data.
Research only — does not touch live Zeus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astra_bot.strategies.research.residual_revert import (  # noqa: E402
    ResidualRevertConfig,
    compute_features,
    generate_signals,
)

FEE = 0.0004
SLIP = 0.0002
RISK = 0.01
CAP = 10_000.0
ALTS = ["ETHUSDT", "SOLUSDT", "HYPEUSDT", "INJUSDT"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_ohlc(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # taker fields: do not fill empty with 0
    return df.dropna(subset=["open", "high", "low", "close", "volume"])


def load_funding(path: Path) -> pd.Series | None:
    if not path.exists():
        return None
    f = pd.read_csv(path)
    f["ts"] = pd.to_datetime(f["funding_time"], unit="ms", utc=True)
    f["rate"] = pd.to_numeric(f["rate"], errors="coerce")
    f = f.dropna().sort_values("ts").set_index("ts")
    return f["rate"]


def split3(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n = len(df)
    a, b = n // 2, int(n * 0.75)
    return df.iloc[:a], df.iloc[a:b], df.iloc[b:]


def backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    cfg: ResidualRevertConfig,
    fee_mult: float = 1.0,
) -> dict:
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    a = df["atr"].values
    rate = df["rate"].fillna(0.0).values if "rate" in df.columns else np.zeros(len(df))
    sig = signals.reindex(df.index).fillna(0).astype(int).values

    fee = FEE * fee_mult
    slip = SLIP * fee_mult
    equity = CAP
    peak = CAP
    max_dd = 0.0
    trades: list[dict] = []

    pos = 0
    entry = sl = tp = 0.0
    entry_i = -1
    risk_cash = 0.0
    entry_rate = 0.0

    def close_pos(i: int, px: float) -> None:
        nonlocal equity, pos
        if pos == 0:
            return
        ret = (px / entry - 1.0) if pos == 1 else (1.0 - px / entry)
        hold_h = (i - entry_i) * 0.5
        fund = entry_rate * (hold_h / 8.0) * (1 if pos == 1 else -1)
        stop_dist = abs(entry - sl) if abs(entry - sl) > 0 else entry * 0.01
        notional = risk_cash / max(stop_dist / entry, 1e-8)
        pnl = (ret - 2 * fee - 2 * slip - fund) * notional
        equity += pnl
        trades.append({"pnl": float(pnl), "side": pos, "hold": i - entry_i})
        pos = 0

    for i in range(1, len(df)):
        target = int(sig[i - 1])
        if pos != 0:
            hit_sl = (pos == 1 and l[i] <= sl) or (pos == -1 and h[i] >= sl)
            hit_tp = (pos == 1 and h[i] >= tp) or (pos == -1 and l[i] <= tp)
            if hit_sl:
                close_pos(i, sl)
            elif hit_tp:
                close_pos(i, tp)
            elif i - entry_i >= cfg.max_hold_bars:
                close_pos(i, c[i])
        if pos == 0 and target != 0 and a[i - 1] > 0 and np.isfinite(a[i - 1]):
            pos = target
            entry = float(o[i])
            entry_i = i
            risk_cash = RISK * max(equity, 100.0)
            entry_rate = float(rate[i - 1])
            dist = cfg.stop_atr * float(a[i - 1])
            if pos == 1:
                sl, tp = entry - dist, entry + cfg.take_rr * dist
            else:
                sl, tp = entry + dist, entry - cfg.take_rr * dist
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    if not trades:
        return {"n": 0, "pf": 0.0, "net": 0.0, "wr": 0.0, "max_dd": 0.0, "exp": 0.0}
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins = pnls[pnls > 0].sum()
    losses = -pnls[pnls <= 0].sum()
    pf = float(wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
    return {
        "n": int(len(pnls)),
        "pf": round(pf, 3),
        "net": round(float(pnls.sum()), 2),
        "wr": round(float((pnls > 0).mean()), 3),
        "max_dd": round(float(max_dd * 100), 2),
        "exp": round(float(pnls.mean()), 2),
        "long_n": sum(1 for t in trades if t["side"] == 1),
        "short_n": sum(1 for t in trades if t["side"] == -1),
        "avg_hold": round(float(np.mean([t["hold"] for t in trades])), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, default=Path("data/bingx_usdm"))
    ap.add_argument("--out", type=Path, default=Path("reports/research/strategy_protocol_residual_revert.json"))
    args = ap.parse_args()

    btc_path = args.data_dir / "BTCUSDT_30m.csv"
    if not btc_path.exists():
        raise SystemExit(f"Missing {btc_path}. Checkout research/positioning-data data files.")

    hashes = {btc_path.name: sha256_file(btc_path)}
    btc = load_ohlc(btc_path)
    cfg = ResidualRevertConfig()

    report: dict = {
        "strategy": "residual_revert",
        "research_only": True,
        "verdict": "NOT_PASSED_MULTI_SYMBOL_OOS",
        "config": cfg.__dict__,
        "hashes": hashes,
        "symbols": {},
        "pooled": {},
    }

    pooled = {"is": [], "val": [], "oos": [], "stress": []}

    for sym in ALTS:
        path = args.data_dir / f"{sym}_30m.csv"
        if not path.exists():
            continue
        hashes[path.name] = sha256_file(path)
        raw = load_ohlc(path)
        feat = compute_features(raw, btc, cfg)
        fund = load_funding(args.data_dir / f"funding_{sym}.csv")
        if fund is not None:
            feat = feat.join(fund.reindex(feat.index, method="ffill").rename("rate"), how="left")
        else:
            feat["rate"] = np.nan
        sig = generate_signals(feat, cfg)
        is_, val, oos = split3(feat.dropna(subset=["atr", "resid_sum"]))
        m_is = backtest(is_, sig, cfg)
        m_val = backtest(val, sig, cfg)
        m_oos = backtest(oos, sig, cfg)
        m_stress = backtest(oos, sig, cfg, fee_mult=1.5)
        report["symbols"][sym] = {"is": m_is, "val": m_val, "oos": m_oos, "stress": m_stress}
        for key, m in ("is", m_is), ("val", m_val), ("oos", m_oos), ("stress", m_stress):
            pooled[key].append(m)

    def agg(ms: list[dict]) -> dict:
        if not ms:
            return {}
        n = sum(m["n"] for m in ms)
        net = sum(m["net"] for m in ms)
        return {"n": n, "net": round(net, 2)}

    report["hashes"] = hashes
    report["pooled"] = {k: agg(v) for k, v in pooled.items()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nWrote {args.out}")
    print("Verdict: NOT_PASSED_MULTI_SYMBOL_OOS (see docs/research/NEW_STRATEGY_PROTOCOL_20261111.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
