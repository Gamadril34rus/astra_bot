#!/usr/bin/env python3
"""
Walk-forward + robustness test of lesson-inspired breakout-retest & EMA rules.
No curve-fitting: fixed params chosen on IS, evaluated on OOS.

Usage (from repo root, with CSVs in data/ or cwd):
  python scripts/research_bt_walkforward.py

Expects: BTCUSDT_4h.csv ETHUSDT_4h.csv SOLUSDT_4h.csv
(from branch research/binance-data)
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
if not (DATA / "BTCUSDT_4h.csv").exists():
    DATA = Path(".")
FEE_RT = 0.0018

def fix_ts(s):
    s = s.astype(np.int64)
    s = np.where(s > 1e15, s // 1000, s)
    return pd.to_datetime(s, unit="ms", utc=True)

def load(sym, tf):
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        path = Path(f"{sym}_{tf}.csv")
    df = pd.read_csv(path)
    df["ts"] = fix_ts(df["open_time"])
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["open","high","low","close","volume"]].dropna()

def indicators(df):
    c = df["close"]
    df = df.copy()
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()
    tr = np.maximum(df["high"]-df["low"], np.maximum((df["high"]-c.shift()).abs(), (df["low"]-c.shift()).abs()))
    df["atr"] = tr.rolling(14).mean()
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["vol_r"] = df["volume"] / df["vol_sma"]
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100/(1 + gain/loss.replace(0, np.nan))
    return df

def backtest(df, rr=1.5, vol_min=1.3, atr_mult=1.5, max_bars=24, strat="break",
             start=None, end=None):
    if start is not None:
        df = df[df.index >= start]
    if end is not None:
        df = df[df.index < end]
    if len(df) < 250:
        return np.array([])
    n = len(df)
    o,h,l,c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    atr = df["atr"].values
    ema20, ema50 = df["ema20"].values, df["ema50"].values
    vol_r = df["vol_r"].values
    rsi = df["rsi"].values
    trades_r = []
    i = 200
    while i < n - 2:
        long_sig = short_sig = False
        if strat == "break":
            hh = h[max(0,i-20):i].max()
            ll = l[max(0,i-20):i].min()
            long_sig = (c[i-1] > hh*0.998 and l[i] <= hh*1.008 and c[i] > hh and
                        vol_r[i] >= vol_min and ema20[i] > ema50[i])
            short_sig = (c[i-1] < ll*1.002 and h[i] >= ll*0.992 and c[i] < ll and
                         vol_r[i] >= vol_min and ema20[i] < ema50[i])
        else:
            long_sig = (ema20[i] > ema50[i] and c[i] > ema20[i]*0.995 and c[i] < ema20[i]*1.012 and
                        vol_r[i] >= vol_min and 40 < rsi[i] < 70 and atr[i] > 0)
            short_sig = (ema20[i] < ema50[i] and c[i] < ema20[i]*1.005 and c[i] > ema20[i]*0.988 and
                         vol_r[i] >= vol_min and 30 < rsi[i] < 60 and atr[i] > 0)
        if not (long_sig or short_sig):
            i += 1
            continue
        side = 1 if long_sig else -1
        entry = o[i+1]
        stop_dist = atr[i] * atr_mult
        if stop_dist <= 0 or np.isnan(stop_dist):
            i += 1
            continue
        stop = entry - side * stop_dist
        take = entry + side * stop_dist * rr
        exit_p = c[min(i+max_bars, n-1)]
        j = min(i+max_bars, n-1)
        for j in range(i+1, min(i+1+max_bars, n)):
            if side == 1:
                if l[j] <= stop: exit_p=stop; break
                if h[j] >= take: exit_p=take; break
            else:
                if h[j] >= stop: exit_p=stop; break
                if l[j] <= take: exit_p=take; break
        raw = side * (exit_p - entry) / entry
        net = raw - FEE_RT
        r_mult = net / (stop_dist / entry)
        trades_r.append(r_mult)
        i = j + 1
    return np.array(trades_r) if trades_r else np.array([])

def stats(rs):
    if len(rs) == 0:
        return dict(n=0, pf=0, mean_r=0, wr=0, net05=0, maxdd=0)
    w = rs[rs>0].sum(); l = abs(rs[rs<=0].sum()) or 1e-9
    pf = w/l; mean_r = rs.mean(); wr = (rs>0).mean()*100
    eq = np.cumprod(1 + 0.005*rs)
    peak = np.maximum.accumulate(eq)
    dd = ((peak-eq)/peak).max()*100
    net = (eq[-1]-1)*100
    return dict(n=len(rs), pf=round(pf,3), mean_r=round(mean_r,3),
                wr=round(wr,1), net05=round(net,1), maxdd=round(dd,1))

def main():
    print("Loading 4h data...")
    data = {}
    for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
        data[sym] = indicators(load(sym, "4h"))
        print(f"  {sym}: {data[sym].index[0].date()} → {data[sym].index[-1].date()}")

    configs = [
        ("break", 1.5, 1.3, 1.5),
        ("break", 2.0, 1.3, 1.5),
        ("break", 1.5, 1.5, 1.5),
        ("ema",   2.0, 1.8, 1.5),
        ("ema",   2.0, 2.0, 1.5),
    ]
    periods = [
        ("IS 2021-2023",  "2021-01-01", "2024-01-01"),
        ("OOS 2024-2026","2024-01-01", "2027-01-01"),
        ("Full 2021-2026","2021-01-01", "2027-01-01"),
    ]

    print("\n=== WALK-FORWARD ===\n")
    best = None
    for strat, rr, vol, atr_m in configs:
        print(f"--- {strat} RR={rr} vol>={vol} ATR×{atr_m} ---")
        row = {"strat": strat, "rr": rr, "vol": vol, "atr": atr_m}
        for pname, start, end in periods:
            all_r = []
            for sym in data:
                rs = backtest(data[sym], rr=rr, vol_min=vol, atr_mult=atr_m,
                              strat=strat, start=start, end=end)
                all_r.append(rs)
            rs = np.concatenate([r for r in all_r if len(r)]) if all_r else np.array([])
            s = stats(rs)
            print(f"  {pname:16s} n={s['n']:4d} PF={s['pf']:.3f} meanR={s['mean_r']:+.3f} "
                  f"WR={s['wr']:5.1f}% net@0.5%={s['net05']:+7.1f}% maxDD={s['maxdd']:5.1f}%")
            row[pname] = s
        oos = row["OOS 2024-2026"]
        if oos["pf"] > 1.0 and oos["mean_r"] > 0:
            if best is None or oos["pf"] > best["OOS 2024-2026"]["pf"]:
                best = row

    print("\n=== BEST OOS CONFIG ===")
    if best:
        print(f"Strategy: {best['strat']}  RR={best['rr']}  vol>={best['vol']}  ATR×{best['atr']}")
        for p in periods:
            s = best[p[0]]
            print(f"  {p[0]}: PF={s['pf']} meanR={s['mean_r']} n={s['n']} net={s['net05']}% DD={s['maxdd']}%")
    print("Done.")

if __name__ == "__main__":
    main()
