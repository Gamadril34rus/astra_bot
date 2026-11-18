#!/usr/bin/env python3
"""
Reproducible Parabolic SAR portfolio backtest for BingX USDT-M perps.

FIXES vs first audit version:
  1) No look-ahead on stop: stop for bar i is SAR known at open of i
     (computed from data through i-1 only). Bar i OHLC only tests that stop.
  2) Entry at next bar open after signal confirmed on prior close.
  3) Portfolio PF computed from pnl_usd, not raw net_pnl_pct.

Cost model (astra_bot bingx_perps paper):
  taker_fee_rate = 0.0005 per side
  slippage_pct   = 0.001 per side
  funding        = 0 (still omitted — known gap)

Usage:
  python sar_backtest_engine.py --start 5000 --out sar_repro_out
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

TAKER = 0.0005
SLIP = 0.001


def fetch_klines(symbol: str, interval: str = "1h", limit_total: int = 9000) -> pd.DataFrame:
    url = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
    rows_all: list = []
    end_time = None
    while len(rows_all) < limit_total:
        params: dict = {"symbol": symbol, "interval": interval, "limit": 1000}
        if end_time is not None:
            params["endTime"] = end_time
        r = requests.get(url, params=params, timeout=30)
        batch = (r.json().get("data") or [])
        if not batch:
            break
        for x in batch:
            rows_all.append(
                (
                    int(x["time"]),
                    float(x["open"]),
                    float(x["high"]),
                    float(x["low"]),
                    float(x["close"]),
                    float(x.get("volume", 0)),
                )
            )
        oldest = min(int(x["time"]) for x in batch)
        if end_time is not None and oldest >= end_time:
            break
        end_time = oldest - 1
        if len(batch) < 40:
            break
        time.sleep(0.12)
    df = pd.DataFrame(rows_all, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def parabolic_sar_series(
    high: np.ndarray,
    low: np.ndarray,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2,
) -> tuple[np.ndarray, np.ndarray]:
    """SAR after each bar close. For bar i open use sar[i-1]."""
    n = len(high)
    sar = np.full(n, np.nan)
    trend = np.zeros(n, dtype=int)
    ep = np.full(n, np.nan)
    af = np.full(n, np.nan)
    trend[0] = 1
    sar[0] = low[0]
    ep[0] = high[0]
    af[0] = af_start
    for i in range(1, n):
        ps, pe, pa, pt = sar[i - 1], ep[i - 1], af[i - 1], trend[i - 1]
        if pt == 1:
            cand = ps + pa * (pe - ps)
            cand = min(cand, low[i - 1])
            if i >= 2:
                cand = min(cand, low[i - 2])
            if low[i] < cand:
                trend[i] = -1
                sar[i] = pe
                ep[i] = low[i]
                af[i] = af_start
            else:
                trend[i] = 1
                sar[i] = cand
                if high[i] > pe:
                    ep[i] = high[i]
                    af[i] = min(pa + af_step, af_max)
                else:
                    ep[i] = pe
                    af[i] = pa
        else:
            cand = ps + pa * (pe - ps)
            cand = max(cand, high[i - 1])
            if i >= 2:
                cand = max(cand, high[i - 2])
            if high[i] > cand:
                trend[i] = 1
                sar[i] = pe
                ep[i] = high[i]
                af[i] = af_start
            else:
                trend[i] = -1
                sar[i] = cand
                if low[i] < pe:
                    ep[i] = low[i]
                    af[i] = min(pa + af_step, af_max)
                else:
                    ep[i] = pe
                    af[i] = pa
    return sar, trend


def extract_trades(df: pd.DataFrame, symbol: str, min_prev: int = 0) -> list[dict]:
    """Signal on close i-1; entry at open i; stop on bar i uses sar[i-1]."""
    h, l, c, o = df["high"].values, df["low"].values, df["close"].values, df["open"].values
    ts = df["timestamp"].values
    sar, tr = parabolic_sar_series(h, l)
    length = np.ones(len(tr), dtype=int)
    for i in range(1, len(tr)):
        length[i] = length[i - 1] + 1 if tr[i] == tr[i - 1] else 1

    trades: list[dict] = []
    pos = 0
    entry_fill = 0.0
    entry_mid = 0.0
    stop0 = 0.0
    ets = 0
    entry_i = -1

    for i in range(2, len(df)):
        if pos != 0 and i > entry_i:
            stop = float(sar[i - 1])
            hit = False
            exit_mid = None
            if pos == 1:
                if o[i] <= stop:
                    exit_mid = float(o[i])
                    hit = True
                elif l[i] <= stop:
                    exit_mid = float(stop)
                    hit = True
            else:
                if o[i] >= stop:
                    exit_mid = float(o[i])
                    hit = True
                elif h[i] >= stop:
                    exit_mid = float(stop)
                    hit = True
            if hit:
                if pos == 1:
                    xe = exit_mid * (1 - SLIP)
                    gross = (xe - entry_fill) / entry_fill
                else:
                    xe = exit_mid * (1 + SLIP)
                    gross = (entry_fill - xe) / entry_fill
                net = gross - 2 * TAKER
                trades.append(
                    {
                        "symbol": symbol,
                        "side": "long" if pos == 1 else "short",
                        "entry_ts": int(ets),
                        "exit_ts": int(ts[i]),
                        "entry_time": pd.to_datetime(ets, unit="ms", utc=True).isoformat(),
                        "exit_time": pd.to_datetime(ts[i], unit="ms", utc=True).isoformat(),
                        "entry_mid": float(entry_mid),
                        "exit_mid": float(exit_mid),
                        "entry_fill": float(entry_fill),
                        "exit_fill": float(xe),
                        "stop_at_entry": float(stop0),
                        "risk_dist": float(abs(entry_mid - stop0) / entry_mid) if entry_mid else 0.0,
                        "gross_pct": float(gross),
                        "fees_pct": float(2 * TAKER),
                        "slip_model_pct": float(2 * SLIP),
                        "net_pnl_pct": float(net),
                        "exit_reason": "sar_stop",
                    }
                )
                pos = 0

        if pos == 0 and tr[i - 1] != tr[i - 2] and int(length[i - 2]) >= min_prev:
            d = int(tr[i - 1])
            stop_ref = float(sar[i - 1])
            entry_mid = float(o[i])
            if d == 1:
                if stop_ref >= entry_mid:
                    continue
                entry_fill = entry_mid * (1 + SLIP)
            else:
                if stop_ref <= entry_mid:
                    continue
                entry_fill = entry_mid * (1 - SLIP)
            pos = d
            stop0 = stop_ref
            ets = int(ts[i])
            entry_i = i

            stop = stop0
            hit = False
            exit_mid = None
            if pos == 1 and l[i] <= stop:
                exit_mid = float(min(o[i], stop)) if o[i] <= stop else float(stop)
                hit = True
            elif pos == -1 and h[i] >= stop:
                exit_mid = float(max(o[i], stop)) if o[i] >= stop else float(stop)
                hit = True
            if hit:
                if pos == 1:
                    xe = exit_mid * (1 - SLIP)
                    gross = (xe - entry_fill) / entry_fill
                else:
                    xe = exit_mid * (1 + SLIP)
                    gross = (entry_fill - xe) / entry_fill
                net = gross - 2 * TAKER
                trades.append(
                    {
                        "symbol": symbol,
                        "side": "long" if pos == 1 else "short",
                        "entry_ts": int(ets),
                        "exit_ts": int(ts[i]),
                        "entry_time": pd.to_datetime(ets, unit="ms", utc=True).isoformat(),
                        "exit_time": pd.to_datetime(ts[i], unit="ms", utc=True).isoformat(),
                        "entry_mid": float(entry_mid),
                        "exit_mid": float(exit_mid),
                        "entry_fill": float(entry_fill),
                        "exit_fill": float(xe),
                        "stop_at_entry": float(stop0),
                        "risk_dist": float(abs(entry_mid - stop0) / entry_mid) if entry_mid else 0.0,
                        "gross_pct": float(gross),
                        "fees_pct": float(2 * TAKER),
                        "slip_model_pct": float(2 * SLIP),
                        "net_pnl_pct": float(net),
                        "exit_reason": "sar_stop_same_bar",
                    }
                )
                pos = 0

    return trades


def portfolio_run(trades: list[dict], risk: float, cap: float, start: float = 5000.0) -> dict:
    trades = sorted(trades, key=lambda x: x["entry_ts"])
    eq = start
    open_pos: list[dict] = []
    rows: list[dict] = []
    curve: list[dict] = [{"ts": trades[0]["entry_ts"] if trades else 0, "equity": eq, "event": "start"}]
    skipped = 0
    wins = 0
    for t in trades:
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t["entry_ts"]:
                pnl_usd = p["notional"] * p["net_pnl_pct"]
                eq_before = eq
                eq += pnl_usd
                if pnl_usd > 0:
                    wins += 1
                rows.append(
                    {
                        **{k: p[k] for k in p if k not in ("notional", "risk_frac", "net_pnl_pct")},
                        "notional": p["notional"],
                        "risk_frac": p["risk_frac"],
                        "net_pnl_pct": p["net_pnl_pct"],
                        "pnl_usd": pnl_usd,
                        "equity_before": eq_before,
                        "equity_after": eq,
                    }
                )
                curve.append({"ts": p["exit_ts"], "equity": eq, "event": "exit"})
            else:
                still.append(p)
        open_pos = still
        open_risk = sum(x["risk_frac"] for x in open_pos)
        if open_risk + risk > cap + 1e-12:
            skipped += 1
            continue
        rd = max(t["risk_dist"], 1e-6)
        notional = (risk * eq) / rd
        open_pos.append({**t, "notional": notional, "risk_frac": risk, "net_pnl_pct": t["net_pnl_pct"]})
        curve.append({"ts": t["entry_ts"], "equity": eq, "event": "entry"})
    for p in open_pos:
        pnl_usd = p["notional"] * p["net_pnl_pct"]
        eq_before = eq
        eq += pnl_usd
        if pnl_usd > 0:
            wins += 1
        rows.append(
            {
                **{k: p[k] for k in p if k not in ("notional", "risk_frac", "net_pnl_pct")},
                "notional": p["notional"],
                "risk_frac": p["risk_frac"],
                "net_pnl_pct": p["net_pnl_pct"],
                "pnl_usd": pnl_usd,
                "equity_before": eq_before,
                "equity_after": eq,
            }
        )
        curve.append({"ts": p["exit_ts"], "equity": eq, "event": "exit"})

    peak = start
    mdd = 0.0
    for pt in curve:
        peak = max(peak, pt["equity"])
        mdd = min(mdd, (pt["equity"] - peak) / peak if peak else 0.0)

    usd = np.array([r["pnl_usd"] for r in rows]) if rows else np.array([0.0])
    gp = float(usd[usd > 0].sum())
    gl = float(-usd[usd <= 0].sum())
    pf_usd = (gp / gl) if gl > 0 else None
    pct = np.array([r["net_pnl_pct"] for r in rows]) if rows else np.array([0.0])
    gp_p = float(pct[pct > 0].sum())
    gl_p = float(-pct[pct <= 0].sum())
    pf_pct = (gp_p / gl_p) if gl_p > 0 else None

    return {
        "rows": rows,
        "equity_curve": curve,
        "summary": {
            "start_equity": start,
            "final_equity": eq,
            "return_pct": 100 * (eq / start - 1),
            "max_dd_pct": 100 * mdd,
            "trades": len(rows),
            "skipped": skipped,
            "winrate_pct": 100 * wins / len(rows) if rows else 0,
            "profit_factor_usd": pf_usd,
            "profit_factor_pct_legacy": pf_pct,
            "avg_net_bps": float(pct.mean() * 10000) if len(pct) else 0,
            "risk_per_trade": risk,
            "max_total_risk": cap,
            "taker_fee": TAKER,
            "slippage": SLIP,
            "engine_version": "v2_no_lookahead_next_open_pf_usd",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--out", type=str, default="sar_repro_out")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    all_trades: list[dict] = []
    bars_meta = {}
    for sym in symbols:
        df = fetch_klines(sym)
        bars_meta[sym] = {
            "bars": len(df),
            "start": df["datetime"].iloc[0].isoformat() if len(df) else None,
            "end": df["datetime"].iloc[-1].isoformat() if len(df) else None,
        }
        print(sym, bars_meta[sym])
        all_trades.extend(extract_trades(df, sym, min_prev=0))
        time.sleep(0.3)
    pd.DataFrame(all_trades).to_csv(out / "trades_raw_signals.csv", index=False)
    print("raw trades", len(all_trades))

    profiles = [
        ("risk_1pct_cap3", 0.01, 0.03),
        ("risk_1_5pct_cap6", 0.015, 0.06),
        ("risk_2pct_cap8", 0.02, 0.08),
        ("risk_3pct_cap12", 0.03, 0.12),
    ]
    summaries = {}
    for name, risk, cap in profiles:
        res = portfolio_run(all_trades, risk=risk, cap=cap, start=args.start)
        summaries[name] = res["summary"]
        pd.DataFrame(res["rows"]).to_csv(out / f"trades_{name}.csv", index=False)
        pd.DataFrame(res["equity_curve"]).to_csv(out / f"equity_{name}.csv", index=False)
        s = res["summary"]
        print(
            f"{name}: final={s['final_equity']:.2f} ret={s['return_pct']:.1f}% "
            f"dd={s['max_dd_pct']:.1f}% n={s['trades']} "
            f"PF_usd={s['profit_factor_usd']} PF_pct_legacy={s['profit_factor_pct_legacy']}"
        )

    meta = {
        "engine_version": "v2_no_lookahead_next_open_pf_usd",
        "symbols": symbols,
        "interval": "1h",
        "sar": {"start": 0.02, "increment": 0.02, "max": 0.2},
        "min_prev_series": 0,
        "execution": {
            "signal": "trend flip on bar close",
            "entry": "next bar open",
            "stop_for_bar_i": "sar after prior bar close (sar[i-1])",
            "gap": "if open gaps through stop, fill at open",
        },
        "cost_model": {
            "taker_fee_per_side": TAKER,
            "slippage_per_side": SLIP,
            "funding": 0,
        },
        "start_equity": args.start,
        "bars": bars_meta,
        "raw_trade_count": len(all_trades),
        "profiles": summaries,
    }
    (out / "summary.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print("Wrote", out)


if __name__ == "__main__":
    main()
