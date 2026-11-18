#!/usr/bin/env python3
"""
SAR research backtest engine v3 — BingX USDT-M perps.

P1 fixes after independent audit:
  - MaxDD from equity path sorted by exit_ts
  - force_close open trades at last completed bar; drop forming last bar
  - risk: notional = risk*equity / (risk_dist + cost_buffer)
  - fees: separate entry/exit notional (not 2*TAKER on one base)

Funding still 0. Param search blocked until re-audit.

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
COST_BUFFER = 2 * (TAKER + SLIP)


def fetch_klines(symbol: str, interval: str = "1h", limit_total: int = 9000) -> pd.DataFrame:
    url = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
    rows_all: list = []
    end_time = None
    empty_streak = 0
    while len(rows_all) < limit_total:
        params: dict = {"symbol": symbol, "interval": interval, "limit": 1000}
        if end_time is not None:
            params["endTime"] = end_time
        batch = None
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                batch = (r.json().get("data") or [])
                break
            except Exception:
                time.sleep(0.4 * (attempt + 1))
        if not batch:
            empty_streak += 1
            if empty_streak >= 2:
                break
            time.sleep(0.3)
            continue
        empty_streak = 0
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


def check_hourly_gaps(df: pd.DataFrame) -> dict:
    if len(df) < 2:
        return {"gaps": 0, "max_gap_hours": 0.0, "median_hours": 0.0}
    deltas = np.diff(df["timestamp"].values) / 3_600_000.0
    return {
        "gaps": int(np.sum(deltas > 1.5)),
        "max_gap_hours": float(deltas.max()),
        "median_hours": float(np.median(deltas)),
    }


def parabolic_sar_series(
    high: np.ndarray,
    low: np.ndarray,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2,
    init_trend: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    n = len(high)
    sar = np.full(n, np.nan)
    trend = np.zeros(n, dtype=int)
    ep = np.full(n, np.nan)
    af = np.full(n, np.nan)
    trend[0] = 1 if init_trend >= 0 else -1
    if trend[0] == 1:
        sar[0] = low[0]
        ep[0] = high[0]
    else:
        sar[0] = high[0]
        ep[0] = low[0]
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


def _pnl_long(entry_mid: float, exit_mid: float) -> tuple[float, float, float, float]:
    entry_fill = entry_mid * (1 + SLIP)
    exit_fill = exit_mid * (1 - SLIP)
    fee_entry = TAKER * entry_fill
    fee_exit = TAKER * exit_fill
    net = (exit_fill - entry_fill - fee_entry - fee_exit) / entry_fill
    gross = (exit_fill - entry_fill) / entry_fill
    return entry_fill, exit_fill, gross, net


def _pnl_short(entry_mid: float, exit_mid: float) -> tuple[float, float, float, float]:
    entry_fill = entry_mid * (1 - SLIP)
    exit_fill = exit_mid * (1 + SLIP)
    fee_entry = TAKER * entry_fill
    fee_exit = TAKER * exit_fill
    net = (entry_fill - exit_fill - fee_entry - fee_exit) / entry_fill
    gross = (entry_fill - exit_fill) / entry_fill
    return entry_fill, exit_fill, gross, net


def extract_trades(
    df: pd.DataFrame,
    symbol: str,
    min_prev: int = 0,
    init_trend: int = 1,
    force_close_end: bool = True,
) -> list[dict]:
    if len(df) < 5:
        return []
    h, l, c, o = df["high"].values, df["low"].values, df["close"].values, df["open"].values
    ts = df["timestamp"].values
    sar, tr = parabolic_sar_series(h, l, init_trend=init_trend)
    length = np.ones(len(tr), dtype=int)
    for i in range(1, len(tr)):
        length[i] = length[i - 1] + 1 if tr[i] == tr[i - 1] else 1

    trades: list[dict] = []
    pos = 0
    entry_mid = 0.0
    stop0 = 0.0
    ets = 0
    entry_i = -1

    def close_trade(exit_i: int, exit_mid: float, reason: str) -> None:
        nonlocal pos
        if pos == 1:
            ef, xf, gross, net = _pnl_long(entry_mid, exit_mid)
        else:
            ef, xf, gross, net = _pnl_short(entry_mid, exit_mid)
        trades.append(
            {
                "symbol": symbol,
                "side": "long" if pos == 1 else "short",
                "entry_ts": int(ets),
                "exit_ts": int(ts[exit_i]),
                "entry_time": pd.to_datetime(ets, unit="ms", utc=True).isoformat(),
                "exit_time": pd.to_datetime(ts[exit_i], unit="ms", utc=True).isoformat(),
                "entry_mid": float(entry_mid),
                "exit_mid": float(exit_mid),
                "entry_fill": float(ef),
                "exit_fill": float(xf),
                "stop_at_entry": float(stop0),
                "risk_dist": float(abs(entry_mid - stop0) / entry_mid) if entry_mid else 0.0,
                "gross_pct": float(gross),
                "net_pnl_pct": float(net),
                "exit_reason": reason,
            }
        )
        pos = 0

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
                close_trade(i, exit_mid, "sar_stop")

        if pos == 0 and tr[i - 1] != tr[i - 2] and int(length[i - 2]) >= min_prev:
            d = int(tr[i - 1])
            stop_ref = float(sar[i - 1])
            entry_mid = float(o[i])
            if d == 1 and stop_ref >= entry_mid:
                continue
            if d == -1 and stop_ref <= entry_mid:
                continue
            pos = d
            stop0 = stop_ref
            ets = int(ts[i])
            entry_i = i
            stop = stop0
            if pos == 1 and l[i] <= stop:
                exit_mid = float(min(o[i], stop)) if o[i] <= stop else float(stop)
                close_trade(i, exit_mid, "sar_stop_same_bar")
            elif pos == -1 and h[i] >= stop:
                exit_mid = float(max(o[i], stop)) if o[i] >= stop else float(stop)
                close_trade(i, exit_mid, "sar_stop_same_bar")

    if force_close_end and pos != 0:
        close_trade(len(df) - 1, float(c[-1]), "eod_force_close")
    return trades


def portfolio_run(trades: list[dict], risk: float, cap: float, start: float = 5000.0) -> dict:
    trades = sorted(trades, key=lambda x: (x["entry_ts"], x["exit_ts"]))
    eq = start
    open_pos: list[dict] = []
    rows: list[dict] = []
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
            else:
                still.append(p)
        open_pos = still
        if sum(x["risk_frac"] for x in open_pos) + risk > cap + 1e-12:
            skipped += 1
            continue
        rd = max(t["risk_dist"] + COST_BUFFER, 1e-6)
        notional = (risk * eq) / rd
        open_pos.append({**t, "notional": notional, "risk_frac": risk, "net_pnl_pct": t["net_pnl_pct"]})

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

    # MaxDD on chronological exit path
    eq2 = start
    curve_pts = [{"ts": 0, "equity": start, "event": "start"}]
    for r in sorted(rows, key=lambda x: (x["exit_ts"], x["entry_ts"])):
        eq2 += r["pnl_usd"]
        curve_pts.append({"ts": r["exit_ts"], "equity": eq2, "event": "exit"})

    peak = start
    mdd = 0.0
    for pt in curve_pts:
        peak = max(peak, pt["equity"])
        mdd = min(mdd, (pt["equity"] - peak) / peak if peak else 0.0)

    usd = np.array([r["pnl_usd"] for r in rows]) if rows else np.array([0.0])
    gp = float(usd[usd > 0].sum())
    gl = float(-usd[usd <= 0].sum())
    pf_usd = (gp / gl) if gl > 0 else None
    pct = np.array([r["net_pnl_pct"] for r in rows]) if rows else np.array([0.0])

    return {
        "rows": rows,
        "equity_curve": curve_pts,
        "summary": {
            "start_equity": start,
            "final_equity": eq2,
            "return_pct": 100 * (eq2 / start - 1),
            "max_dd_pct": 100 * mdd,
            "trades": len(rows),
            "skipped": skipped,
            "winrate_pct": 100 * wins / len(rows) if rows else 0,
            "profit_factor_usd": pf_usd,
            "avg_net_bps": float(pct.mean() * 10000) if len(pct) else 0,
            "risk_per_trade": risk,
            "max_total_risk": cap,
            "cost_buffer": COST_BUFFER,
            "taker_fee": TAKER,
            "slippage": SLIP,
            "engine_version": "v3_audit_fixes",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--out", type=str, default="sar_repro_out")
    ap.add_argument("--init-trend", type=int, default=1)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    all_trades: list[dict] = []
    bars_meta = {}
    for sym in symbols:
        df = fetch_klines(sym)
        if len(df) > 10:
            df = df.iloc[:-1].reset_index(drop=True)
        gaps = check_hourly_gaps(df)
        bars_meta[sym] = {
            "bars": len(df),
            "start": df["datetime"].iloc[0].isoformat() if len(df) else None,
            "end": df["datetime"].iloc[-1].isoformat() if len(df) else None,
            "gaps": gaps,
        }
        print(sym, bars_meta[sym])
        all_trades.extend(
            extract_trades(df, sym, min_prev=0, init_trend=args.init_trend, force_close_end=True)
        )
        time.sleep(0.3)
    pd.DataFrame(all_trades).to_csv(out / "trades_raw_signals.csv", index=False)
    print("raw trades", len(all_trades))

    profiles = [
        ("risk_1pct_cap3", 0.01, 0.03),
        ("risk_1_5pct_cap6", 0.015, 0.06),
        ("risk_2pct_cap8", 0.02, 0.08),
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
            f"dd={s['max_dd_pct']:.1f}% n={s['trades']} PF_usd={s['profit_factor_usd']}"
        )

    meta = {
        "engine_version": "v3_audit_fixes",
        "audit_status": "P1 fixes applied; re-audit required before param search",
        "symbols": symbols,
        "interval": "1h",
        "sar": {"start": 0.02, "increment": 0.02, "max": 0.2, "init_trend": args.init_trend},
        "cost_model": {
            "taker_fee_per_side": TAKER,
            "slippage_per_side": SLIP,
            "cost_buffer": COST_BUFFER,
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
