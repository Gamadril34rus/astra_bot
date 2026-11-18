#!/usr/bin/env python3
"""
SAR research backtest engine v4 — BingX USDT-M perps.

Fixes after v3 audit (Needs revision):
  - Full MTM equity: realized + unrealized on every bar for MaxDD
  - Exit timestamps: bar close time (open_ts + interval_ms) for close fills
  - Simultaneous entries: same entry_ts share one equity base + joint risk cap
  - fetch_klines: HTTP status, BingX code, min bars, expected span checks
  - SAR warm-up bars before scoring window
  - cost_buffer kept; actual stop loss vs planned risk logged in trades

Funding still 0. No param search until re-audit.

Usage:
  python sar_backtest_engine.py --start 5000 --out sar_repro_out
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from sar_funding import apply_funding_to_trades, fetch_funding_rates

TAKER = 0.0005
SLIP = 0.001
COST_BUFFER = 2 * (TAKER + SLIP)
INTERVAL_MS = 3_600_000  # default 1h
WARMUP_BARS = 50


def interval_to_ms(interval: str) -> int:
    """Convert supported BingX candle intervals to milliseconds."""
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    value = interval.strip().lower()
    if len(value) < 2 or value[-1] not in units or not value[:-1].isdigit():
        raise ValueError(f"Unsupported candle interval: {interval!r}")
    amount = int(value[:-1])
    if amount <= 0:
        raise ValueError(f"Invalid candle interval: {interval!r}")
    return amount * units[value[-1]]


class FetchError(RuntimeError):
    pass


def fetch_klines(
    symbol: str,
    interval: str = "1h",
    limit_total: int = 9000,
    min_bars: int = 500,
) -> pd.DataFrame:
    interval_ms = interval_to_ms(interval)
    url = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
    rows_all: list = []
    end_time = None
    empty_streak = 0
    while len(rows_all) < limit_total:
        params: dict = {"symbol": symbol, "interval": interval, "limit": 1000}
        if end_time is not None:
            params["endTime"] = end_time
        batch = None
        last_err = None
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                if r.status_code != 200:
                    raise FetchError(f"{symbol} HTTP {r.status_code}: {r.text[:200]}")
                data = r.json()
                code = data.get("code")
                if code is not None and str(code) not in ("0", "0.0"):
                    if str(code) == "100410":
                        time.sleep(1.5 * (attempt + 1))
                        last_err = FetchError(f"{symbol} API code={code}")
                        continue
                    raise FetchError(f"{symbol} API code={code} msg={data.get('msg')}")
                batch = data.get("data")
                if batch is None:
                    raise FetchError(f"{symbol} missing data field: {str(data)[:200]}")
                break
            except FetchError:
                raise
            except Exception as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
        if batch is None:
            raise FetchError(f"{symbol} fetch failed: {last_err}")
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
        time.sleep(0.12)

    df = pd.DataFrame(rows_all, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    if len(df) < min_bars:
        raise FetchError(f"{symbol} only {len(df)} bars, need >= {min_bars}")
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["close_ts"] = df["timestamp"] + interval_ms
    return df


def check_candle_gaps(df: pd.DataFrame, interval_ms: int = INTERVAL_MS) -> dict:
    if len(df) < 2:
        return {"gaps": 0, "max_gap_intervals": 0.0, "median_intervals": 0.0}
    deltas = np.diff(df["timestamp"].values) / float(interval_ms)
    return {
        "gaps": int(np.sum(deltas > 1.5)),
        "max_gap_intervals": float(deltas.max()),
        "median_intervals": float(np.median(deltas)),
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
    warmup: int = WARMUP_BARS,
    interval_ms: int = INTERVAL_MS,
) -> list[dict]:
    if len(df) < warmup + 5:
        return []
    h, l, c, o = df["high"].values, df["low"].values, df["close"].values, df["open"].values
    ts = df["timestamp"].values
    close_ts = df["close_ts"].values if "close_ts" in df.columns else ts + interval_ms
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

    def close_trade(exit_i: int, exit_mid: float, reason: str, use_close_time: bool = True) -> None:
        nonlocal pos
        if pos == 1:
            ef, xf, gross, net = _pnl_long(entry_mid, exit_mid)
        else:
            ef, xf, gross, net = _pnl_short(entry_mid, exit_mid)
        planned_dist = abs(entry_mid - stop0) / entry_mid if entry_mid else 0.0
        xts = int(close_ts[exit_i]) if use_close_time else int(ts[exit_i])
        trades.append(
            {
                "symbol": symbol,
                "side": "long" if pos == 1 else "short",
                "entry_ts": int(ets),
                "exit_ts": xts,
                "entry_bar_open_ts": int(ets),
                "exit_bar_open_ts": int(ts[exit_i]),
                "entry_time": pd.to_datetime(ets, unit="ms", utc=True).isoformat(),
                "exit_time": pd.to_datetime(xts, unit="ms", utc=True).isoformat(),
                "entry_mid": float(entry_mid),
                "exit_mid": float(exit_mid),
                "entry_fill": float(ef),
                "exit_fill": float(xf),
                "stop_at_entry": float(stop0),
                "risk_dist": float(planned_dist),
                "gross_pct": float(gross),
                "net_pnl_pct": float(net),
                "exit_reason": reason,
            }
        )
        pos = 0

    start_i = max(2, warmup)
    for i in range(start_i, len(df)):
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
                if (pos == 1 and o[i] <= stop) or (pos == -1 and o[i] >= stop):
                    use_close = False
                else:
                    use_close = True
                close_trade(i, exit_mid, "sar_stop", use_close_time=use_close)

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
                use_close = not (o[i] <= stop)
                close_trade(i, exit_mid, "sar_stop_same_bar", use_close_time=use_close)
            elif pos == -1 and h[i] >= stop:
                exit_mid = float(max(o[i], stop)) if o[i] >= stop else float(stop)
                use_close = not (o[i] >= stop)
                close_trade(i, exit_mid, "sar_stop_same_bar", use_close_time=use_close)

    if force_close_end and pos != 0:
        close_trade(len(df) - 1, float(c[-1]), "eod_force_close", use_close_time=True)
    return trades


def _unrealized_pct(side: str, entry_fill: float, mark_mid: float) -> float:
    if side == "long":
        mark_fill = mark_mid * (1 - SLIP)
        return (mark_fill - entry_fill) / entry_fill
    mark_fill = mark_mid * (1 + SLIP)
    return (entry_fill - mark_fill) / entry_fill


def portfolio_run_mtm(
    trades: list[dict],
    bars_by_symbol: dict[str, pd.DataFrame],
    risk: float,
    cap: float,
    start: float = 5000.0,
) -> dict:
    if not trades:
        return {
            "rows": [],
            "equity_curve": [{"ts": 0, "equity": start, "realized": start, "unrealized": 0.0}],
            "summary": {
                "start_equity": start,
                "final_equity": start,
                "return_pct": 0.0,
                "max_dd_pct": 0.0,
                "max_dd_realized_only_pct": 0.0,
                "trades": 0,
                "skipped": 0,
                "winrate_pct": 0.0,
                "profit_factor_usd": None,
                "engine_version": "v4_mtm_batch_entries_interval_aware",
            },
        }

    trades = sorted(trades, key=lambda x: (x["entry_ts"], x["symbol"], x["exit_ts"]))
    groups: dict[int, list[dict]] = defaultdict(list)
    for t in trades:
        groups[t["entry_ts"]].append(t)
    entry_times = sorted(groups.keys())

    eq_realized = start
    open_pos: list[dict] = []
    rows: list[dict] = []
    skipped = 0
    wins = 0

    all_close_ts = set()
    close_px = {}
    for sym, df in bars_by_symbol.items():
        for _, row in df.iterrows():
            cts = int(row["close_ts"]) if "close_ts" in df.columns else int(row["timestamp"]) + INTERVAL_MS
            all_close_ts.add(cts)
            close_px[(sym, cts)] = float(row["close"])
    timeline = sorted(all_close_ts)

    def close_finished(asof_ts: int) -> None:
        nonlocal eq_realized, wins
        still = []
        for p in open_pos:
            if p["exit_ts"] <= asof_ts:
                pnl_usd = p["notional"] * p["net_pnl_pct"]
                eq_before = eq_realized
                eq_realized += pnl_usd
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
                        "equity_after": eq_realized,
                    }
                )
            else:
                still.append(p)
        open_pos[:] = still

    for et in entry_times:
        close_finished(et)
        batch = groups[et]
        open_risk = sum(p["risk_frac"] for p in open_pos)
        remaining = cap - open_risk
        if remaining < risk - 1e-15:
            skipped += len(batch)
            continue
        max_n = int(remaining / risk + 1e-12)
        batch_sorted = sorted(batch, key=lambda x: x["symbol"])
        accepted = batch_sorted[:max_n]
        skipped += len(batch_sorted) - len(accepted)
        base_eq = eq_realized
        for t in accepted:
            rd = max(t["risk_dist"] + COST_BUFFER, 1e-6)
            notional = (risk * base_eq) / rd
            open_pos.append(
                {
                    **t,
                    "notional": notional,
                    "risk_frac": risk,
                    "net_pnl_pct": t["net_pnl_pct"],
                    "entry_fill": t["entry_fill"],
                }
            )

    if open_pos:
        last_exit = max(p["exit_ts"] for p in open_pos)
        close_finished(last_exit + 1)

    pos_specs = []
    for r in rows:
        pos_specs.append(
            {
                "symbol": r["symbol"],
                "side": r["side"],
                "entry_ts": r["entry_ts"],
                "exit_ts": r["exit_ts"],
                "entry_fill": r["entry_fill"],
                "notional": r["notional"],
                "net_pnl_pct": r["net_pnl_pct"],
                "pnl_usd": r["pnl_usd"],
            }
        )

    realized_at = {0: start}
    eq_r = start
    for r in sorted(rows, key=lambda x: (x["exit_ts"], x["entry_ts"])):
        eq_r += r["pnl_usd"]
        realized_at[r["exit_ts"]] = eq_r

    def realized_asof(ts: int) -> float:
        val = start
        for xt, e in sorted(realized_at.items()):
            if xt <= ts:
                val = e
            else:
                break
        return val

    curve = []
    peak = start
    mdd = 0.0
    peak_r = start
    mdd_r = 0.0
    for cts in timeline:
        realized = realized_asof(cts)
        unreal = 0.0
        for p in pos_specs:
            if p["entry_ts"] <= cts < p["exit_ts"]:
                mark = close_px.get((p["symbol"], cts))
                if mark is None:
                    continue
                upct = _unrealized_pct(p["side"], p["entry_fill"], mark)
                unreal += p["notional"] * upct
        equity = realized + unreal
        curve.append({"ts": cts, "equity": equity, "realized": realized, "unrealized": unreal})
        peak = max(peak, equity)
        mdd = min(mdd, (equity - peak) / peak if peak else 0.0)
        peak_r = max(peak_r, realized)
        mdd_r = min(mdd_r, (realized - peak_r) / peak_r if peak_r else 0.0)

    final = curve[-1]["equity"] if curve else start
    usd = np.array([r["pnl_usd"] for r in rows]) if rows else np.array([0.0])
    gp = float(usd[usd > 0].sum())
    gl = float(-usd[usd <= 0].sum())
    pf_usd = (gp / gl) if gl > 0 else None
    pct = np.array([r["net_pnl_pct"] for r in rows]) if rows else np.array([0.0])

    return {
        "rows": rows,
        "equity_curve": curve,
        "summary": {
            "start_equity": start,
            "final_equity": final,
            "final_realized": eq_r,
            "return_pct": 100 * (final / start - 1),
            "max_dd_pct": 100 * mdd,
            "max_dd_realized_only_pct": 100 * mdd_r,
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
            "engine_version": "v4_mtm_batch_entries_interval_aware",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--out", type=str, default="sar_repro_out")
    ap.add_argument("--init-trend", type=int, default=1)
    ap.add_argument("--warmup", type=int, default=WARMUP_BARS)
    ap.add_argument("--interval", type=str, default="1h", help="BingX candle interval; video baseline is 15m")
    ap.add_argument("--limit-total", type=int, default=None, help="number of candles per symbol; defaults to ~1y for interval")
    args = ap.parse_args()
    interval_ms = interval_to_ms(args.interval)
    limit_total = args.limit_total or (36_000 if args.interval == "15m" else 9_000)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    all_trades: list[dict] = []
    bars_meta = {}
    for sym in symbols:
        df = fetch_klines(sym, interval=args.interval, limit_total=limit_total, min_bars=max(500, int(limit_total * 0.9)))
        if len(df) > 10:
            df = df.iloc[:-1].reset_index(drop=True)
        gaps = check_candle_gaps(df, interval_ms=interval_ms)
        bars_meta[sym] = {
            "bars": len(df),
            "start": df["datetime"].iloc[0].isoformat() if len(df) else None,
            "end": df["datetime"].iloc[-1].isoformat() if len(df) else None,
            "gaps": gaps,
        }
        print(sym, bars_meta[sym])
        bars_by_symbol[sym] = df
        all_trades.extend(
            extract_trades(
                df,
                sym,
                min_prev=0,
                init_trend=args.init_trend,
                force_close_end=True,
                warmup=args.warmup,
                interval_ms=interval_ms,
            )
        )
        time.sleep(0.3)

    funding_by_symbol: dict[str, list[dict]] = {}
    funding_meta = {}
    for sym, df in bars_by_symbol.items():
        start_ms = int(df["timestamp"].iloc[0])
        end_ms = int(df["close_ts"].iloc[-1])
        events = fetch_funding_rates(sym, start_ms, end_ms)
        funding_by_symbol[sym] = events
        funding_meta[sym] = {
            "events": len(events),
            "first_funding_time": events[0]["fundingTime"] if events else None,
            "last_funding_time": events[-1]["fundingTime"] if events else None,
        }
        time.sleep(1.05)
    all_trades = apply_funding_to_trades(all_trades, funding_by_symbol)

    pd.DataFrame(all_trades).to_csv(out / "trades_raw_signals.csv", index=False)
    print("raw trades", len(all_trades))

    profiles = [
        ("risk_1pct_cap3", 0.01, 0.03),
        ("risk_1_5pct_cap6", 0.015, 0.06),
        ("risk_2pct_cap8", 0.02, 0.08),
    ]
    summaries = {}
    for name, risk, cap in profiles:
        res = portfolio_run_mtm(all_trades, bars_by_symbol, risk=risk, cap=cap, start=args.start)
        summaries[name] = res["summary"]
        pd.DataFrame(res["rows"]).to_csv(out / f"trades_{name}.csv", index=False)
        eq = res["equity_curve"]
        if len(eq) > 5000:
            step = max(1, len(eq) // 5000)
            eq = eq[::step]
        pd.DataFrame(eq).to_csv(out / f"equity_{name}.csv", index=False)
        s = res["summary"]
        print(
            f"{name}: final={s['final_equity']:.2f} ret={s['return_pct']:.1f}% "
            f"dd_mtm={s['max_dd_pct']:.1f}% dd_real={s['max_dd_realized_only_pct']:.1f}% "
            f"n={s['trades']} PF_usd={s['profit_factor_usd']}"
        )

    sens = {}
    for it in (1, -1):
        trs = []
        for sym, df in bars_by_symbol.items():
            trs.extend(extract_trades(df, sym, init_trend=it, warmup=args.warmup, interval_ms=interval_ms))
        trs = apply_funding_to_trades(trs, funding_by_symbol)
        r = portfolio_run_mtm(trs, bars_by_symbol, risk=0.01, cap=0.03, start=args.start)
        sens[str(it)] = {
            "final": r["summary"]["final_equity"],
            "dd_mtm": r["summary"]["max_dd_pct"],
            "max_dd_realized_only_pct": r["summary"]["max_dd_realized_only_pct"],
            "pf": r["summary"]["profit_factor_usd"],
            "n": r["summary"]["trades"],
        }
    print("init_trend sensitivity", sens)

    meta = {
        "engine_version": "v4_mtm_batch_entries_interval_aware",
        "audit_status": "v4 source delivered; re-audit required; no param search",
        "symbols": symbols,
        "interval": args.interval,
        "warmup_bars": args.warmup,
        "sar": {"start": 0.02, "increment": 0.02, "max": 0.2, "init_trend": args.init_trend},
        "cost_model": {
            "taker_fee_per_side": TAKER,
            "slippage_per_side": SLIP,
            "cost_buffer": COST_BUFFER,
            "funding": "historical BingX settlement cashflows; mark-price adjusted by side",
        },
        "start_equity": args.start,
        "bars": bars_meta,
        "funding": funding_meta,
        "raw_trade_count": len(all_trades),
        "profiles": summaries,
        "init_trend_sensitivity": sens,
    }
    (out / "summary.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print("Wrote", out)


if __name__ == "__main__":
    main()
