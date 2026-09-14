#!/usr/bin/env python3
"""ASTRA Ultimate Strategy Research.

Research-only harness. Does not touch production configs/models.
Uses committed Binance spot OHLCV CSVs and evaluates pre-registered
trend/breakout/pullback families with honest next-open execution, costs,
ATR stops, time stops, and walk-forward validation.

The goal is not maximum in-sample return. Selection is based on OOS quality,
robustness across symbols/years, and stress under higher costs.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIRS = (ROOT, ROOT / "data")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = ("1h", "4h")
BASE_CAPITAL = 10_000.0
RISK_PCT = 0.005
MAX_NOTIONAL_FRAC = 0.75
DEFAULT_FEE = 0.0005
DEFAULT_SLIPPAGE = 0.0010


@dataclass(frozen=True)
class Config:
    family: str
    lookback: int
    band: float
    atr_stop: float
    trail_atr: float
    max_hold: int
    adx_min: float
    ema_filter: int
    time_filter: int
    take_r: float
    exit_band: float


@dataclass
class Backtest:
    trades: int
    wins: int
    gross_win: float
    gross_loss: float
    ret_pct: float
    max_dd_pct: float
    sharpe: float
    exposure_pct: float
    avg_r: float
    median_r: float
    profit_factor: float
    expectancy: float
    calmar: float
    eq: pd.Series
    trade_r: list[float]

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


def find_csv(symbol: str, tf: str) -> Path:
    for d in DATA_DIRS:
        p = d / f"{symbol}_{tf}.csv"
        if p.exists():
            return p
    raise FileNotFoundError(f"missing {symbol}_{tf}.csv")


def load(symbol: str, tf: str) -> pd.DataFrame:
    p = find_csv(symbol, tf)
    df = pd.read_csv(p)
    df.columns = [str(c).strip().lower() for c in df.columns]
    need = ["open_time", "open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"{p}: missing {missing}")
    df = df[need].copy()
    for c in need:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    return df


def bars_per_day(df: pd.DataFrame) -> int:
    step = float(df.open_time.diff().median())
    return max(1, min(24, round(86_400_000 / step)))


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat(
        [df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df.high.diff()
    dn = -df.low.diff()
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat(
        [df.high - df.low, (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], axis=1
    ).max(axis=1)
    atrv = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atrv
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atrv
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def build_signals(df: pd.DataFrame, cfg: Config) -> pd.Series:
    c, h, l, v = df.close, df.high, df.low, df.volume
    bpd = bars_per_day(df)
    lb = max(2, round(cfg.lookback * bpd))
    efilter = ema(c, cfg.ema_filter) if cfg.ema_filter else None
    a = atr(df, 14)
    a_pct = a / c
    adxv = adx(df, 14)
    vol_ma = v.rolling(20 * bpd, min_periods=max(10, 10 * bpd)).mean()
    vol_ok = v > vol_ma if cfg.family in {"breakout", "sweep"} else pd.Series(True, index=df.index)

    if cfg.family == "tsm":
        mom = c / c.shift(lb) - 1
        sig = np.where(mom > cfg.band, 1, np.where(mom < -cfg.band, -1, 0))
        if cfg.exit_band > 0:
            out = np.zeros(len(df), dtype=int)
            state = 0
            for i, x in enumerate(mom.to_numpy()):
                if not np.isfinite(x):
                    out[i] = state
                    continue
                if state == 1:
                    if x < cfg.exit_band:
                        state = -1 if x < -cfg.band else 0
                elif state == -1:
                    if x > -cfg.exit_band:
                        state = 1 if x > cfg.band else 0
                else:
                    if x > cfg.band:
                        state = 1
                    elif x < -cfg.band:
                        state = -1
                out[i] = state
            sig = out
        if efilter is not None:
            sig = np.where((np.asarray(sig) > 0) & (c.to_numpy() <= efilter.to_numpy()), 0, sig)
            sig = np.where((np.asarray(sig) < 0) & (c.to_numpy() >= efilter.to_numpy()), 0, sig)

    elif cfg.family == "breakout":
        hh = h.shift(1).rolling(lb, min_periods=lb).max()
        ll = l.shift(1).rolling(lb, min_periods=lb).min()
        long = c > hh
        short = c < ll
        if cfg.adx_min > 0:
            long &= adxv > cfg.adx_min
            short &= adxv > cfg.adx_min
        long &= vol_ok
        short &= vol_ok
        state = np.zeros(len(df), dtype=int)
        st = 0
        for i in range(len(df)):
            if bool(long.iloc[i]):
                st = 1
            elif bool(short.iloc[i]):
                st = -1
            state[i] = st
        sig = state

    elif cfg.family == "sweep":
        n = max(5, round(20 * bpd / 6))
        hh = h.shift(1).rolling(n, min_periods=n).max()
        ll = l.shift(1).rolling(n, min_periods=n).min()
        long = (l < ll) & (c > ll) & vol_ok
        short = (h > hh) & (c < hh) & vol_ok
        if efilter is not None:
            long &= c > efilter
            short &= c < efilter
        if cfg.adx_min > 0:
            long &= adxv > cfg.adx_min
            short &= adxv > cfg.adx_min
        state = np.zeros(len(df), dtype=int)
        st = 0
        for i in range(len(df)):
            if bool(long.iloc[i]):
                st = 1
            elif bool(short.iloc[i]):
                st = -1
            state[i] = st
        sig = state

    elif cfg.family == "ema_pullback":
        e20, e50, e200 = ema(c, 20 * bpd // 6 + 2), ema(c, 50 * bpd // 6 + 2), ema(c, 200 * bpd // 6 + 2)
        long = (e20 > e50) & (e50 > e200) & (l <= e20) & (c > e20)
        short = (e20 < e50) & (e50 < e200) & (h >= e20) & (c < e20)
        if cfg.adx_min > 0:
            long &= adxv > cfg.adx_min
            short &= adxv > cfg.adx_min
        state = np.zeros(len(df), dtype=int)
        st = 0
        for i in range(len(df)):
            if bool(long.iloc[i]):
                st = 1
            elif bool(short.iloc[i]):
                st = -1
            elif st == 1 and c.iloc[i] < e50.iloc[i]:
                st = 0
            elif st == -1 and c.iloc[i] > e50.iloc[i]:
                st = 0
            state[i] = st
        sig = state

    elif cfg.family == "vol_break":
        mom = c / c.shift(lb) - 1
        vol_ok2 = a_pct > a_pct.rolling(50, min_periods=20).median()
        base = np.where(mom > cfg.band, 1, np.where(mom < -cfg.band, -1, 0))
        base = np.where(vol_ok2, base, 0)
        if cfg.adx_min > 0:
            base = np.where(adxv.to_numpy() > cfg.adx_min, base, 0)
        sig = pd.Series(base, index=df.index).replace(0, np.nan).ffill().fillna(0).to_numpy(dtype=int)

    else:
        raise ValueError(cfg.family)

    return pd.Series(sig, index=df.index, dtype=int)


def run(df: pd.DataFrame, signal: pd.Series, cfg: Config, fee: float, slip: float) -> Backtest:
    o = df.open.to_numpy(float)
    h = df.high.to_numpy(float)
    l = df.low.to_numpy(float)
    c = df.close.to_numpy(float)
    a = atr(df, 14).to_numpy(float)
    s = signal.to_numpy(int)
    n = len(df)
    equity = np.empty(n, dtype=float)
    equity[:] = np.nan
    realized = BASE_CAPITAL
    peak = BASE_CAPITAL
    max_dd = 0.0
    pos = 0
    entry = stop = target = 0.0
    entry_i = -1
    risk_cash = 0.0
    notional = 0.0
    highest = lowest = 0.0
    trades = wins = 0
    gw = gl = 0.0
    hold_bars = 0
    bars_market = 0
    rs: list[float] = []

    def close(px: float) -> None:
        nonlocal realized, trades, wins, gw, gl, pos
        if pos == 0:
            return
        raw = (px / entry - 1.0) if pos == 1 else (1.0 - px / entry)
        pnl = raw * notional - (fee + slip) * notional * 2
        realized += pnl
        r = pnl / risk_cash if risk_cash > 0 else 0.0
        rs.append(r)
        trades += 1
        if pnl > 0:
            wins += 1
            gw += pnl
        else:
            gl += -pnl
        pos = 0

    for i in range(1, n):
        desired = int(s[i - 1])
        if pos != 0 and (desired != pos):
            close(o[i])

        if pos == 0 and desired != 0 and np.isfinite(a[i - 1]) and a[i - 1] > 0:
            entry = o[i] * (1 + (fee + slip) * desired)
            dist = cfg.atr_stop * a[i - 1]
            stop = entry - dist if desired == 1 else entry + dist
            target = (entry + cfg.take_r * dist) if desired == 1 and cfg.take_r > 0 else ((entry - cfg.take_r * dist) if desired == -1 and cfg.take_r > 0 else 0.0)
            risk_cash = max(realized, 1.0) * RISK_PCT
            notional = min(MAX_NOTIONAL_FRAC * max(realized, 1.0), risk_cash / max(dist / entry, 1e-9))
            pos = desired
            entry_i = i
            hold_bars = 0
            highest = o[i]
            lowest = o[i]

        if pos != 0:
            bars_market += 1
            hold_bars += 1
            highest = max(highest, h[i])
            lowest = min(lowest, l[i])

            # Conservative same-bar rule: stop wins when stop and target are both hit.
            hit_stop = (pos == 1 and l[i] <= stop) or (pos == -1 and h[i] >= stop)
            hit_target = target > 0 and ((pos == 1 and h[i] >= target) or (pos == -1 and l[i] <= target))
            if hit_stop:
                close(stop)
            elif hit_target:
                close(target)
            else:
                if cfg.trail_atr > 0:
                    if pos == 1:
                        stop = max(stop, highest - cfg.trail_atr * a[i])
                    else:
                        stop = min(stop, lowest + cfg.trail_atr * a[i])
                if cfg.max_hold > 0 and hold_bars >= cfg.max_hold:
                    close(c[i])

        eq = realized
        if pos != 0:
            mark = (c[i] / entry - 1.0) if pos == 1 else (1.0 - c[i] / entry)
            eq += mark * notional
        equity[i] = eq
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)

    if pos != 0:
        close(c[-1])
        equity[-1] = realized
    equity[0] = BASE_CAPITAL
    eqs = pd.Series(equity, index=df.open_time)
    rets = eqs.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    step = np.median(np.diff(df.open_time.to_numpy())) if n > 2 else 4 * 3600 * 1000
    bpy = 365 * max(1, round(86_400_000 / step))
    sharpe = float(rets.mean() / rets.std() * math.sqrt(bpy)) if len(rets) > 2 and rets.std() > 0 else 0.0
    ret = (realized / BASE_CAPITAL - 1) * 100
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    avg_r = float(np.mean(rs)) if rs else 0.0
    med_r = float(np.median(rs)) if rs else 0.0
    calmar = ret / max_dd if max_dd > 0 else 0.0
    return Backtest(
        trades=trades, wins=wins, gross_win=gw, gross_loss=gl,
        ret_pct=ret, max_dd_pct=max_dd, sharpe=sharpe,
        exposure_pct=bars_market / max(n - 1, 1) * 100,
        avg_r=avg_r, median_r=med_r, profit_factor=pf,
        expectancy=avg_r, calmar=calmar, eq=eqs, trade_r=rs,
    )


def period(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    t1 = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    return df[(df.open_time >= t0) & (df.open_time < t1)].reset_index(drop=True)


def score(bt: Backtest, min_trades: int = 15) -> float:
    if bt.trades < min_trades or bt.profit_factor <= 0:
        return -999.0
    # Reward expectancy and PF, penalize drawdown and low sample sizes.
    return (0.55 * math.log(max(bt.profit_factor, 1e-6)) +
            0.30 * bt.avg_r - 0.10 * bt.max_dd_pct / 10.0 +
            0.05 * min(bt.trades / 50.0, 1.0))


def candidate_grid() -> list[Config]:
    out: list[Config] = []
    for lb, band, astop, trail in itertools.product((30, 45, 60, 90), (0.01, 0.02, 0.03), (3.0, 4.0, 5.0), (0.0, 2.0, 3.0)):
        out.append(Config("tsm", lb, band, astop, trail, 0, 20.0, 200, 0.0, 0.0))
    for lb, astop, trail, adxv in itertools.product((20, 40, 60, 100), (2.5, 3.5, 5.0), (0.0, 2.0, 3.0), (0.0, 20.0, 25.0)):
        out.append(Config("breakout", lb, 0.0, astop, trail, 180, adxv, 0, 0, 0.0))
    for astop, trail, adxv, emaf in itertools.product((2.0, 3.0, 4.0), (0.0, 2.0, 3.0), (0.0, 20.0, 25.0), (200, 400)):
        out.append(Config("ema_pullback", 30, 0.0, astop, trail, 120, adxv, emaf, 0, 0.0, 0.0))
    for lb, band, astop, trail, adxv in itertools.product((30, 45, 60), (0.01, 0.02, 0.03), (3.0, 5.0), (0.0, 2.0), (0.0, 20.0)):
        out.append(Config("vol_break", lb, band, astop, trail, 0, adxv, 0, 0, 0.0, 0.0))
    return out


def main() -> None:
    data = {(s, tf): load(s, tf) for s in SYMBOLS for tf in TIMEFRAMES}
    candidates = candidate_grid()
    rows: list[dict] = []

    # Final OOS is held out: 2025-01-01 -> data end.
    train_start, train_end = "2021-01-01", "2024-12-31"
    oos_start, oos_end = "2025-01-01", "2026-09-01"

    for tf in TIMEFRAMES:
        for sym in SYMBOLS:
            df = data[(sym, tf)]
            tr = period(df, train_start, train_end)
            oo = period(df, oos_start, oos_end)
            for cfg in candidates:
                sig_all = build_signals(df, cfg)
                sig_tr = sig_all.loc[tr.index].reset_index(drop=True)
                sig_oo = sig_all.loc[oo.index].reset_index(drop=True)
                btr = run(tr, sig_tr, cfg, DEFAULT_FEE, DEFAULT_SLIPPAGE)
                boo = run(oo, sig_oo, cfg, DEFAULT_FEE, DEFAULT_SLIPPAGE)
                stress = run(oo, sig_oo, cfg, 0.0010, 0.0020)
                rows.append({
                    "tf": tf, "symbol": sym, "cfg": repr(cfg),
                    "family": cfg.family, "lookback": cfg.lookback,
                    "band": cfg.band, "atr_stop": cfg.atr_stop, "trail": cfg.trail_atr,
                    "adx": cfg.adx_min, "ema": cfg.ema_filter,
                    "is_pf": btr.profit_factor, "is_ret": btr.ret_pct, "is_dd": btr.max_dd_pct, "is_trades": btr.trades,
                    "oos_pf": boo.profit_factor, "oos_ret": boo.ret_pct, "oos_dd": boo.max_dd_pct,
                    "oos_sharpe": boo.sharpe, "oos_trades": boo.trades, "oos_avg_r": boo.avg_r,
                    "stress_pf": stress.profit_factor, "stress_ret": stress.ret_pct, "stress_dd": stress.max_dd_pct,
                })

    r = pd.DataFrame(rows)
    r.to_csv(ROOT / "research_ultimate_all_results.csv", index=False)

    # Select universal configurations by pooled median across 3 symbols.
    grp_cols = ["tf", "cfg"]
    agg = r.groupby(grp_cols).agg(
        symbols=("symbol", "nunique"),
        median_oos_pf=("oos_pf", "median"),
        median_oos_ret=("oos_ret", "median"),
        median_dd=("oos_dd", "median"),
        median_avg_r=("oos_avg_r", "median"),
        min_oos_pf=("oos_pf", "min"),
        min_oos_ret=("oos_ret", "min"),
        stress_pf=("stress_pf", "median"),
        oos_trades=("oos_trades", "sum"),
        is_pf=("is_pf", "median"),
    ).reset_index()
    agg["score"] = (
        np.log(np.maximum(agg.median_oos_pf, 1e-6)) * 2
        + agg.median_avg_r * 2
        - agg.median_dd / 25
        + np.minimum(agg.oos_trades, 100) / 100
    )
    agg = agg.sort_values(["score", "median_oos_pf"], ascending=False)
    agg.to_csv(ROOT / "research_ultimate_ranked.csv", index=False)

    # Conservative promotion gate: positive pooled median + no catastrophic symbol + stress PF > 1.
    winners = agg[
        (agg.symbols >= 3) &
        (agg.median_oos_pf >= 1.15) &
        (agg.min_oos_pf >= 0.95) &
        (agg.min_oos_ret > -2.0) &
        (agg.median_dd < 25.0) &
        (agg.stress_pf >= 1.05) &
        (agg.oos_trades >= 30)
    ].head(12)
    winners.to_csv(ROOT / "research_ultimate_winners.csv", index=False)

    # Build a simple equal-weight top-3 portfolio for each timeframe from symbol-level OOS curves.
    lines = ["# Ultimate Strategy Research", "", "Costs: 0.05% fee + 0.10% slippage per side; stress 0.10% + 0.20%.",
             "Risk: 0.5% equity per trade, notional cap 75%.", "", "## Top candidates", ""]
    if winners.empty:
        lines.append("No candidate passed the conservative promotion gate.")
    else:
        for _, x in winners.iterrows():
            lines.append(f"- **{x.tf} / {x.cfg[:140]}** — median OOS PF {x.median_oos_pf:.2f}, "
                         f"median return {x.median_oos_ret:+.1f}%, median DD {x.median_dd:.1f}%, "
                         f"stress PF {x.stress_pf:.2f}, OOS trades {int(x.oos_trades)}")

    lines += ["", "## Interpretation", "",
              "Selection is based on pooled OOS performance, not the best single symbol.",
              "A configuration is rejected when one of BTC/ETH/SOL is materially negative,",
              "when drawdown is excessive, or when the edge disappears under doubled costs."]

    (ROOT / "research_ultimate_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("RESULTS", len(r), "configs x symbol/timeframe")
    print(winners.to_string(index=False))


if __name__ == "__main__":
    main()
