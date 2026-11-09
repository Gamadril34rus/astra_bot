#!/usr/bin/env python3
"""Deterministic research harness for the preregistered BingX perpetual intraday hypothesis.

Research-only. Requires BingX USDT-M 15m/1h OHLCV plus a complete funding-event
CSV. OHLC execution is a conservative proxy, not exchange-fill parity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
BAR_MS = {"15m": 900_000, "1h": 3_600_000}


def load_bars(path: Path, interval: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"open_time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    df["open_time"] = pd.to_numeric(df["open_time"], errors="raise").astype("int64")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[list(required)].isna().any().any():
        raise ValueError(f"{path}: null/non-numeric OHLCV")
    if df["open_time"].duplicated().any():
        raise ValueError(f"{path}: duplicate candle timestamps")
    df = df.sort_values("open_time").reset_index(drop=True)
    bad = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    bad |= df["high"] < df[["open", "low", "close"]].max(axis=1)
    bad |= df["low"] > df[["open", "high", "close"]].min(axis=1)
    bad |= df["volume"] < 0
    if bad.any():
        raise ValueError(f"{path}: {int(bad.sum())} invalid OHLCV rows")
    gaps = df["open_time"].diff().dropna()
    if (gaps != BAR_MS[interval]).any():
        raise ValueError(f"{path}: candle gaps found; repair/segment data before testing")
    df["decision_time"] = df["open_time"] + BAR_MS[interval]
    return df


def funding_events(path: Path, symbol: str) -> pd.DataFrame:
    f = pd.read_csv(path)
    required = {"symbol", "funding_time", "rate"}
    if required - set(f.columns):
        raise ValueError(f"{path}: funding CSV requires {sorted(required)}")
    f = f[f["symbol"] == symbol].copy()
    f["funding_time"] = pd.to_numeric(f["funding_time"], errors="raise").astype("int64")
    f["rate"] = pd.to_numeric(f["rate"], errors="raise")
    if f["funding_time"].duplicated().any():
        raise ValueError(f"{path}: duplicate funding timestamps for {symbol}")
    return f.sort_values("funding_time")


def add_features(m15: pd.DataFrame, h1: pd.DataFrame) -> pd.DataFrame:
    x = m15.copy()
    c = x["close"]
    x["ema20"] = c.ewm(span=20, adjust=False).mean()
    x["ema50"] = c.ewm(span=50, adjust=False).mean()
    prev = c.shift()
    tr = pd.concat(
        [x["high"] - x["low"], (x["high"] - prev).abs(),
         (x["low"] - prev).abs()], axis=1
    ).max(axis=1)
    x["atr14"] = tr.rolling(14, min_periods=14).mean()
    x["vol_ratio"] = x["volume"] / x["volume"].rolling(20, min_periods=20).mean()

    h = h1.copy()
    hc = h["close"]
    h["h1_ema20"] = hc.ewm(span=20, adjust=False).mean()
    h["h1_ema50"] = hc.ewm(span=50, adjust=False).mean()
    h["h1_ema20_rising"] = h["h1_ema20"] > h["h1_ema20"].shift(3)
    # decision_time is the close of each 1h candle; only closed candles are eligible.
    h = h[["decision_time", "h1_ema20", "h1_ema50", "h1_ema20_rising"]].sort_values("decision_time")
    x = pd.merge_asof(
        x.sort_values("decision_time"), h, on="decision_time",
        direction="backward", allow_exact_matches=True
    )
    return x


def funding_cost_fraction(side: int, rates: pd.Series) -> float:
    """Positive rate is paid by LONG and received by SHORT."""
    return float(rates.sum()) * side


def trade_symbol(symbol: str, x: pd.DataFrame, funding: pd.DataFrame,
                 fee_bps: float, slippage_bps: float, half_spread_bps: float) -> list[dict]:
    trades: list[dict] = []
    i = 205
    n = len(x)
    max_hold_bars = 36
    # Every decision uses a fully closed 15m candle; fill proxy is next candle open.
    while i < n - max_hold_bars - 1:
        r = x.iloc[i]
        if pd.isna(r["atr14"]) or pd.isna(r["h1_ema50"]) or pd.isna(r["vol_ratio"]):
            i += 1
            continue
        side = 0
        h1_long = r["h1_ema20"] > r["h1_ema50"] and bool(r["h1_ema20_rising"])
        h1_short = r["h1_ema20"] < r["h1_ema50"] and not bool(r["h1_ema20_rising"])
        recent = x.iloc[i-4:i]
        pull_long = (recent["low"] <= recent["ema20"]).any() and (recent["close"] > recent["ema50"]).all()
        pull_short = (recent["high"] >= recent["ema20"]).any() and (recent["close"] < recent["ema50"]).all()
        if h1_long and pull_long and r["close"] > r["ema20"] and r["close"] > x.iloc[i-1]["high"] and r["vol_ratio"] >= 1.2:
            side = 1
            swing = float(recent["low"].min())
        elif h1_short and pull_short and r["close"] < r["ema20"] and r["close"] < x.iloc[i-1]["low"] and r["vol_ratio"] >= 1.2:
            side = -1
            swing = float(recent["high"].max())
        if not side:
            i += 1
            continue

        atr = float(r["atr14"])
        stop_ref = swing - 0.15 * atr if side == 1 else swing + 0.15 * atr
        raw_entry = float(x.iloc[i+1]["open"])
        risk = raw_entry - stop_ref if side == 1 else stop_ref - raw_entry
        if risk <= 0 or risk < 0.35 * atr or risk > 1.8 * atr:
            i += 1
            continue
        target_ref = raw_entry + side * 1.5 * risk
        # Adverse entry proxy. Costs are charged separately in net PnL.
        entry = raw_entry * (1 + side * (slippage_bps + half_spread_bps) / 10_000)
        stop = stop_ref
        target = target_ref
        entry_time = int(x.iloc[i+1]["open_time"])
        exit_i = i + max_hold_bars
        exit_ref = float(x.iloc[exit_i]["close"])
        reason = "time"
        for j in range(i+1, i+1+max_hold_bars):
            bar = x.iloc[j]
            stop_hit = float(bar["low"]) <= stop if side == 1 else float(bar["high"]) >= stop
            target_hit = float(bar["high"]) >= target if side == 1 else float(bar["low"]) <= target
            if stop_hit:
                exit_i, exit_ref, reason = j, stop, "stop"
                break
            if target_hit:
                exit_i, exit_ref, reason = j, target, "target"
                break
        exit_time = int(x.iloc[exit_i]["decision_time"])
        exit_px = exit_ref * (1 - side * (slippage_bps + half_spread_bps) / 10_000)
        gross = side * (exit_ref / raw_entry - 1)
        fee_cost = 2 * fee_bps / 10_000
        spread_slippage_cost = 2 * (slippage_bps + half_spread_bps) / 10_000
        # Positive funding rate: longs pay, shorts receive. Include a settlement
        # exactly at entry conservatively because OHLC cannot order fills vs settlement.
        crossed = funding[(funding["funding_time"] >= entry_time) & (funding["funding_time"] <= exit_time)]
        funding_cost = funding_cost_fraction(side, crossed["rate"])
        net = gross - fee_cost - spread_slippage_cost - funding_cost
        hold_minutes = (exit_time - entry_time) / 60_000
        trades.append({
            "symbol": symbol, "side": "LONG" if side == 1 else "SHORT",
            "entry_time": entry_time, "exit_time": exit_time,
            "hold_minutes": hold_minutes, "reason": reason,
            "entry": entry, "exit": exit_px, "gross_pct": gross * 100,
            "fee_pct": fee_cost * 100, "spread_slippage_pct": spread_slippage_cost * 100,
            "funding_pct": funding_cost * 100, "net_pct": net * 100,
            "net_r": net / (risk / raw_entry),
        })
        i = exit_i + 1
    return trades


def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "pf": 0.0, "net_expectancy_pct": 0.0, "median_hold_min": None}
    wins = trades.loc[trades.net_pct > 0, "net_pct"].sum()
    losses = -trades.loc[trades.net_pct <= 0, "net_pct"].sum()
    return {
        "trades": len(trades),
        "wins": int((trades.net_pct > 0).sum()),
        "win_rate_pct": round(float((trades.net_pct > 0).mean() * 100), 2),
        "pf": round(float(wins / losses), 4) if losses else ("inf" if wins else 0.0),
        "net_expectancy_pct": round(float(trades.net_pct.mean()), 5),
        "net_expectancy_r": round(float(trades.net_r.mean()), 5),
        "median_hold_min": round(float(trades.hold_minutes.median()), 1),
        "mean_hold_min": round(float(trades.hold_minutes.mean()), 1),
        "p90_hold_min": round(float(trades.hold_minutes.quantile(.9)), 1),
        "max_hold_min": round(float(trades.hold_minutes.max()), 1),
        "net_sum_pct": round(float(trades.net_pct.sum()), 4),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, default=Path("data/bingx_usdm"))
    p.add_argument("--funding", type=Path, required=True,
                   help="Complete BingX funding events CSV: symbol,funding_time,rate")
    p.add_argument("--fee-bps", type=float, default=5.0, help="taker fee per side, bps")
    p.add_argument("--slippage-bps", type=float, default=3.0, help="adverse slippage per side, bps")
    p.add_argument("--half-spread-bps", type=float, default=2.0,
                   help="half-spread proxy per side; set 0 only when executable BBO is used")
    p.add_argument("--out", type=Path, default=Path("reports/perp_intraday_net_edge"))
    args = p.parse_args()
    if not args.funding.exists():
        raise SystemExit("INCOMPLETE: funding events file is required; zero funding is not assumed.")
    all_trades = []
    coverage = {}
    for symbol in SYMBOLS:
        stem = symbol.replace("-", "")
        m15_path = args.data_dir / f"{symbol}_15m.csv"
        h1_path = args.data_dir / f"{symbol}_1h.csv"
        if not m15_path.exists():
            m15_path = args.data_dir / f"{stem}_15m.csv"
        if not h1_path.exists():
            h1_path = args.data_dir / f"{stem}_1h.csv"
        if not m15_path.exists() or not h1_path.exists():
            raise SystemExit(f"INCOMPLETE: missing BingX perpetual candles for {symbol}: {m15_path}, {h1_path}")
        m15, h1 = load_bars(m15_path, "15m"), load_bars(h1_path, "1h")
        funding = funding_events(args.funding, symbol)
        if funding.empty:
            raise SystemExit(f"INCOMPLETE: no funding events for {symbol}")
        candle_start = int(m15.open_time.iloc[0])
        candle_end = int(m15.decision_time.iloc[-1])
        funding_times = funding["funding_time"].to_numpy()
        if funding_times[0] > candle_start or funding_times[-1] < candle_end:
            raise SystemExit(f"INCOMPLETE: funding history does not cover candle window for {symbol}")
        if len(funding_times) > 1 and np.diff(funding_times).max() > 9 * 60 * 60 * 1000:
            raise SystemExit(f"INCOMPLETE: funding event gap exceeds 9h for {symbol}")
        x = add_features(m15, h1)
        coverage[symbol] = {
            "15m_first": int(m15.open_time.iloc[0]), "15m_last": int(m15.open_time.iloc[-1]),
            "15m_rows": len(m15), "1h_first": int(h1.open_time.iloc[0]),
            "1h_last": int(h1.open_time.iloc[-1]), "1h_rows": len(h1),
            "funding_rows": len(funding), "funding_first": int(funding.funding_time.min()),
            "funding_last": int(funding.funding_time.max()),
        }
        all_trades.extend(trade_symbol(symbol, x, funding, args.fee_bps,
                                       args.slippage_bps, args.half_spread_bps))
    df = pd.DataFrame(all_trades)
    args.out.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out / "trades.csv", index=False)
    report = {
        "status": "RESEARCH_ONLY_PROXY_EXECUTION",
        "costs": {"fee_bps_per_side": args.fee_bps, "slippage_bps_per_side": args.slippage_bps,
                  "half_spread_bps_per_side": args.half_spread_bps},
        "coverage": coverage,
        "combined": summarize(df),
        "by_symbol_side": {
            f"{symbol}_{side}": summarize(group)
            for (symbol, side), group in df.groupby(["symbol", "side"])
        } if not df.empty else {},
        "warning": "OHLC proxy is not exchange fill parity; validate funding coverage, quote execution and chronological OOS separately."
    }
    (args.out / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
