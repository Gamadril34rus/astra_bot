#!/usr/bin/env python3
"""Research-only regime-routed backtest for BingX USDT-M perpetuals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000}
MAX_HOLD_BARS = 36
WARMUP_BARS = 120


def load_bars(path: Path, interval: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"open_time", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "quote_volume" in frame:
        frame["quote_volume"] = pd.to_numeric(frame["quote_volume"], errors="coerce")
    else:
        frame["quote_volume"] = frame["volume"]
    if frame[list(required)].isna().any().any():
        raise ValueError(f"{path}: null/non-numeric OHLCV")
    if frame["open_time"].duplicated().any():
        raise ValueError(f"{path}: duplicate candle timestamps")
    frame = frame.sort_values("open_time").reset_index(drop=True)
    invalid = (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
    invalid |= frame["high"] < frame[["open", "low", "close"]].max(axis=1)
    invalid |= frame["low"] > frame[["open", "high", "close"]].min(axis=1)
    invalid |= frame["volume"] < 0
    if invalid.any():
        raise ValueError(f"{path}: {int(invalid.sum())} invalid OHLCV rows")
    gaps = frame["open_time"].diff().dropna()
    if (gaps != INTERVAL_MS[interval]).any():
        raise ValueError(f"{path}: gaps found; repair/segment the source before backtest")
    frame["decision_time"] = frame["open_time"] + INTERVAL_MS[interval]
    return frame


def load_funding(path: Path, symbol: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"funding_time", "rate"}
    if required - set(frame.columns):
        raise ValueError(f"{path}: funding file requires {sorted(required)}")
    if "symbol" in frame:
        frame = frame[frame["symbol"] == symbol].copy()
    frame["funding_time"] = pd.to_numeric(frame["funding_time"], errors="raise").astype("int64")
    frame["rate"] = pd.to_numeric(frame["rate"], errors="raise")
    if frame["funding_time"].duplicated().any():
        raise ValueError(f"{path}: duplicate funding timestamps for {symbol}")
    return frame.sort_values("funding_time").reset_index(drop=True)


def latest_contiguous_funding_segment(
    funding: pd.DataFrame, max_gap_hours: float = 12.0
) -> tuple[pd.DataFrame, int]:
    """Keep the latest uninterrupted funding segment; never impute missing rates."""
    if funding.empty:
        return funding.copy(), 0
    times = funding["funding_time"].to_numpy(dtype="int64")
    gap_indices = np.flatnonzero(np.diff(times) > max_gap_hours * 60 * 60 * 1000)
    start = int(gap_indices[-1] + 1) if len(gap_indices) else 0
    return funding.iloc[start:].reset_index(drop=True), start


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    previous = frame["close"].shift()
    tr = pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - previous).abs(),
         (frame["low"] - previous).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_features(m15: pd.DataFrame, h1: pd.DataFrame) -> pd.DataFrame:
    x = m15.copy()
    close = x["close"]
    x["ema20"] = close.ewm(span=20, adjust=False).mean()
    x["ema50"] = close.ewm(span=50, adjust=False).mean()
    previous = close.shift()
    tr = pd.concat(
        [x["high"] - x["low"], (x["high"] - previous).abs(),
         (x["low"] - previous).abs()], axis=1
    ).max(axis=1)
    x["atr14"] = tr.rolling(14, min_periods=14).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi14"] = 100 - 100 / (1 + rs)
    mid = close.rolling(20, min_periods=20).mean()
    std = close.rolling(20, min_periods=20).std(ddof=0)
    x["bb_upper"] = mid + 2 * std
    x["bb_lower"] = mid - 2 * std
    x["bb_width"] = (4 * std) / mid.replace(0, np.nan)
    x["bb_width_pct"] = x["bb_width"].rolling(96, min_periods=48).apply(
        lambda values: float(np.mean(values <= values[-1])), raw=True
    ).shift(1)
    volume = x["quote_volume"].where(x["quote_volume"] > 0, x["volume"])
    x["volume_ratio"] = volume / volume.rolling(20, min_periods=20).mean()

    h = h1.copy()
    hc = h["close"]
    h["h1_ema20"] = hc.ewm(span=20, adjust=False).mean()
    h["h1_ema50"] = hc.ewm(span=50, adjust=False).mean()
    h["h1_ema20_rising"] = h["h1_ema20"] > h["h1_ema20"].shift(3)
    h["h1_adx14"] = _adx(h)
    h = h[["decision_time", "h1_ema20", "h1_ema50", "h1_ema20_rising", "h1_adx14"]]
    return pd.merge_asof(
        x.sort_values("decision_time"), h.sort_values("decision_time"),
        on="decision_time", direction="backward", allow_exact_matches=True
    )


def choose_signal(x: pd.DataFrame, i: int) -> dict | None:
    row = x.iloc[i]
    if pd.isna(row["atr14"]) or pd.isna(row["h1_adx14"]) or pd.isna(row["bb_width_pct"]):
        return None
    atr = float(row["atr14"])
    if atr <= 0:
        return None
    prior = x.iloc[i - 20:i]
    prior4 = x.iloc[i - 4:i]
    prior3 = x.iloc[i - 3:i]
    previous = x.iloc[i - 1]
    long_bias = row["h1_ema20"] > row["h1_ema50"]
    short_bias = row["h1_ema20"] < row["h1_ema50"]

    # Compression breakout has first priority; it is a regime transition, not scalping.
    if row["bb_width_pct"] <= 0.25 and row["volume_ratio"] >= 1.5:
        high = float(prior["high"].max())
        low = float(prior["low"].min())
        if row["close"] > high and not short_bias:
            return {"family": "COMPRESSION_BREAKOUT", "side": 1,
                    "stop": float(row["close"]) - 1.2 * atr, "target_rr": 1.5}
        if row["close"] < low and not long_bias:
            return {"family": "COMPRESSION_BREAKOUT", "side": -1,
                    "stop": float(row["close"]) + 1.2 * atr, "target_rr": 1.5}

    # False-break reversal: fade a failed 20-bar range escape only in a
    # non-trending hourly regime. The breakout bar is prior; current close must
    # return inside the same pre-break range.
    if row["h1_adx14"] <= 22 and previous["volume_ratio"] >= 1.5:
        pre_break = x.iloc[i - 21:i - 1]
        range_high = float(pre_break["high"].max())
        range_low = float(pre_break["low"].min())
        range_mid = (range_high + range_low) / 2
        if previous["close"] > range_high and row["close"] < range_high:
            stop = max(float(previous["high"]), float(row["high"])) + 0.15 * atr
            return {
                "family": "FALSE_BREAK_REVERSAL", "side": -1,
                "stop": stop, "target_price": range_mid,
            }
        if previous["close"] < range_low and row["close"] > range_low:
            stop = min(float(previous["low"]), float(row["low"])) - 0.15 * atr
            return {
                "family": "FALSE_BREAK_REVERSAL", "side": 1,
                "stop": stop, "target_price": range_mid,
            }

    # Directional trend continuation: pullback and confirmed reclaim.
    if row["h1_adx14"] >= 20 and row["volume_ratio"] >= 1.1:
        if long_bias and bool(row["h1_ema20_rising"]):
            touched = (prior4["low"] <= prior4["ema20"]).any()
            held = (prior4["close"] > prior4["ema50"]).all()
            if touched and held and row["close"] > row["ema20"] and row["close"] > previous["high"]:
                stop = float(prior4["low"].min()) - 0.15 * atr
                return {"family": "TREND_PULLBACK", "side": 1, "stop": stop, "target_rr": 1.5}
        if short_bias and not bool(row["h1_ema20_rising"]):
            touched = (prior4["high"] >= prior4["ema20"]).any()
            held = (prior4["close"] < prior4["ema50"]).all()
            if touched and held and row["close"] < row["ema20"] and row["close"] < previous["low"]:
                stop = float(prior4["high"].max()) + 0.15 * atr
                return {"family": "TREND_PULLBACK", "side": -1, "stop": stop, "target_rr": 1.5}

    # Range reversion is allowed only when the hourly trend is weak.
    if row["h1_adx14"] <= 18:
        if previous["close"] < previous["bb_lower"] and row["close"] > row["bb_lower"] and row["rsi14"] <= 40:
            stop = float(prior3["low"].min()) - 0.15 * atr
            return {"family": "RANGE_REVERSION", "side": 1, "stop": stop, "target_price": float(row["ema20"])}
        if previous["close"] > previous["bb_upper"] and row["close"] < row["bb_upper"] and row["rsi14"] >= 60:
            stop = float(prior3["high"].max()) + 0.15 * atr
            return {"family": "RANGE_REVERSION", "side": -1, "stop": stop, "target_price": float(row["ema20"])}
    return None


def funding_cost_fraction(side: int, rates: pd.Series) -> float:
    """Positive funding rate is paid by LONG and received by SHORT."""
    return float(rates.sum()) * side


def simulate_symbol(
    symbol: str, x: pd.DataFrame, funding: pd.DataFrame,
    fee_bps: float, spread_bps: float, slippage_bps: float,
) -> list[dict]:
    trades: list[dict] = []
    if funding.empty:
        raise ValueError(f"{symbol}: no funding history; cannot assume zero")
    first_bar, last_bar = int(x.open_time.iloc[0]), int(x.decision_time.iloc[-1])
    funding_times = funding["funding_time"].to_numpy()
    if funding_times[0] > first_bar:
        raise ValueError(f"{symbol}: candle history begins before available funding coverage")
    if last_bar - funding_times[-1] > 12 * 60 * 60 * 1000:
        raise ValueError(f"{symbol}: latest funding observation is stale versus candle coverage")
    if len(funding_times) > 1 and np.diff(funding_times).max() > 12 * 60 * 60 * 1000:
        raise ValueError(f"{symbol}: funding history gap exceeds 12 hours")

    i = WARMUP_BARS
    while i < len(x) - MAX_HOLD_BARS - 1:
        signal = choose_signal(x, i)
        if signal is None:
            i += 1
            continue
        side = int(signal["side"])
        raw_entry = float(x.iloc[i + 1]["open"])
        stop = float(signal["stop"])
        risk = raw_entry - stop if side == 1 else stop - raw_entry
        atr = float(x.iloc[i]["atr14"])
        if risk <= 0 or risk < 0.35 * atr or risk > 1.8 * atr:
            i += 1
            continue
        if "target_price" in signal:
            target = float(signal["target_price"])
            reward = target - raw_entry if side == 1 else raw_entry - target
            if reward <= 0 or reward / risk < 1.0:
                i += 1
                continue
        else:
            target = raw_entry + side * float(signal["target_rr"]) * risk
        entry_time = int(x.iloc[i + 1]["open_time"])
        exit_i = i + MAX_HOLD_BARS
        exit_ref = float(x.iloc[exit_i]["close"])
        reason = "time"
        for j in range(i + 1, i + 1 + MAX_HOLD_BARS):
            bar = x.iloc[j]
            open_px, high, low = float(bar["open"]), float(bar["high"]), float(bar["low"])
            if side == 1:
                stop_hit, target_hit = low <= stop, high >= target
                if stop_hit:
                    exit_i, exit_ref = j, min(open_px, stop) if open_px < stop else stop
                    reason = "stop"
                    break
                if target_hit:
                    exit_i, exit_ref, reason = j, target, "target"
                    break
            else:
                stop_hit, target_hit = high >= stop, low <= target
                if stop_hit:
                    exit_i, exit_ref = j, max(open_px, stop) if open_px > stop else stop
                    reason = "stop"
                    break
                if target_hit:
                    exit_i, exit_ref, reason = j, target, "target"
                    break
        exit_time = int(x.iloc[exit_i]["decision_time"])
        gross = side * (exit_ref / raw_entry - 1.0)
        fee_cost = 2 * fee_bps / 10_000
        spread_cost = 2 * spread_bps / 10_000
        slippage_cost = 2 * slippage_bps / 10_000
        crossed = funding[
            (funding["funding_time"] >= entry_time)
            & (funding["funding_time"] <= exit_time)
        ]
        funding_cost = funding_cost_fraction(side, crossed["rate"])
        net = gross - fee_cost - spread_cost - slippage_cost - funding_cost
        trades.append({
            "symbol": symbol, "family": signal["family"],
            "side": "LONG" if side == 1 else "SHORT",
            "entry_time": entry_time, "exit_time": exit_time,
            "hold_minutes": (exit_time - entry_time) / 60_000,
            "exit_reason": reason, "gross_pct": gross * 100,
            "fee_pct": fee_cost * 100, "spread_pct": spread_cost * 100,
            "slippage_pct": slippage_cost * 100,
            "funding_pct": funding_cost * 100, "net_pct": net * 100,
            "net_r": net / (risk / raw_entry),
        })
        i = exit_i + 1
    return trades


def summarize(
    frame: pd.DataFrame,
    calendar_start: pd.Period | None = None,
    calendar_end: pd.Period | None = None,
) -> dict:
    if frame.empty:
        return {"trades": 0, "pf": 0.0, "net_expectancy_pct": 0.0}
    wins = frame.loc[frame.net_pct > 0, "net_pct"].sum()
    losses = -frame.loc[frame.net_pct <= 0, "net_pct"].sum()
    monthly = frame.assign(
        month=pd.to_datetime(frame.exit_time, unit="ms", utc=True).dt.to_period("M")
    )
    month_net = monthly.groupby("month")["net_pct"].sum()
    if calendar_start is not None and calendar_end is not None:
        months = pd.period_range(calendar_start, calendar_end, freq="M")
        month_net = month_net.reindex(months, fill_value=0.0)
    ordered = frame.sort_values("exit_time")
    equity = (1 + ordered["net_pct"] / 100).cumprod()
    trade_sequence_drawdown = equity / equity.cummax() - 1
    events = []
    for row in frame.itertuples():
        events.append((int(row.entry_time), 1))
        events.append((int(row.exit_time), -1))
    active = 0
    max_concurrent = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        max_concurrent = max(max_concurrent, active)
    # Equal-notional portfolio proxy: each trade gets 1/max-concurrency of
    # starting equity, so peak gross exposure is capped at 1x. PnL is realized
    # at exit; this is not an intratrade mark-to-market or margin liquidation model.
    trade_weight = 1.0 / max(max_concurrent, 1)
    portfolio_pnl = ordered["net_pct"].to_numpy(dtype=float) * trade_weight / 100
    portfolio_equity = 1.0 + np.cumsum(portfolio_pnl)
    portfolio_peak = np.maximum.accumulate(np.r_[1.0, portfolio_equity])[1:]
    portfolio_drawdown = portfolio_equity / portfolio_peak - 1
    return {
        "trades": len(frame),
        "pf": round(float(wins / losses), 4) if losses else ("inf" if wins else 0.0),
        "win_rate_pct": round(float((frame.net_pct > 0).mean() * 100), 2),
        "net_expectancy_pct": round(float(frame.net_pct.mean()), 5),
        "net_expectancy_r": round(float(frame.net_r.mean()), 5),
        "sum_trade_net_pct": round(float(frame.net_pct.sum()), 4),
        "median_hold_min": round(float(frame.hold_minutes.median()), 1),
        "mean_hold_min": round(float(frame.hold_minutes.mean()), 1),
        "p90_hold_min": round(float(frame.hold_minutes.quantile(.9)), 1),
        "max_hold_min": round(float(frame.hold_minutes.max()), 1),
        "positive_month_share_pct": round(float((month_net > 0).mean() * 100), 2),
        "trade_sequence_drawdown_pct": round(float(trade_sequence_drawdown.min() * 100), 2),
        "realized_equal_notional_drawdown_pct": round(
            float(min(0.0, portfolio_drawdown.min(initial=0.0)) * 100), 2
        ),
        "equal_notional_weight_per_trade_pct": round(trade_weight * 100, 2),
        "max_concurrent_positions": max_concurrent,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/bingx_usdm"))
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--spread-bps", type=float, default=2.0)
    parser.add_argument("--slippage-bps", type=float, default=3.0)
    parser.add_argument("--out", type=Path, default=Path("reports/adaptive_perp"))
    args = parser.parse_args()
    all_trades = []
    coverage = {}
    for symbol in SYMBOLS:
        stem = symbol.replace("-", "")
        m15_path = args.data_dir / f"{stem}_15m.csv"
        h1_path = args.data_dir / f"{stem}_1h.csv"
        funding_path = args.data_dir / f"funding_{stem}.csv"
        for path in (m15_path, h1_path, funding_path):
            if not path.exists():
                raise SystemExit(f"INCOMPLETE: required BingX source is missing: {path}")
        m15, h1 = load_bars(m15_path, "15m"), load_bars(h1_path, "1h")
        funding = load_funding(funding_path, symbol)
        if funding.empty:
            raise SystemExit(f"INCOMPLETE: {symbol} has no BingX funding history")
        funding, excluded_funding_events = latest_contiguous_funding_segment(funding)
        if funding.empty:
            raise SystemExit(f"INCOMPLETE: {symbol} has no contiguous BingX funding segment")
        # Earlier funding history contains multi-day/month holes. Use only the
        # latest uninterrupted segment, selected solely on data integrity, never PnL.
        # Trim candles to that segment; do not impute missing funding as zero.
        m15 = m15[m15.open_time >= int(funding.funding_time.iloc[0])].reset_index(drop=True)
        if m15.empty:
            raise SystemExit(f"INCOMPLETE: {symbol} has no candles inside funding coverage")
        x = add_features(m15, h1)
        coverage[symbol] = {
            "15m_rows": len(m15), "15m_first": int(m15.open_time.iloc[0]),
            "15m_last": int(m15.open_time.iloc[-1]), "1h_rows": len(h1),
            "1h_first": int(h1.open_time.iloc[0]), "1h_last": int(h1.open_time.iloc[-1]),
            "funding_events": len(funding),
            "funding_events_excluded_before_latest_contiguous_segment": excluded_funding_events,
            "funding_first": int(funding.funding_time.iloc[0]) if not funding.empty else None,
            "funding_last": int(funding.funding_time.iloc[-1]) if not funding.empty else None,
        }
        all_trades.extend(simulate_symbol(
            symbol, x, funding, args.fee_bps, args.spread_bps, args.slippage_bps
        ))
    trades = pd.DataFrame(all_trades)
    if trades.empty:
        raise SystemExit("NO_TRADES: frozen router produced no completed trades")
    trades["period"] = np.select(
        [
            trades.entry_time < pd.Timestamp("2024-01-01", tz="UTC").timestamp() * 1000,
            trades.entry_time < pd.Timestamp("2025-01-01", tz="UTC").timestamp() * 1000,
        ],
        ["IS", "VALIDATION"], default="OOS"
    )
    calendar_start = pd.to_datetime(
        min(item["15m_first"] for item in coverage.values()), unit="ms", utc=True
    ).to_period("M")
    calendar_end = pd.to_datetime(
        max(item["15m_last"] for item in coverage.values()), unit="ms", utc=True
    ).to_period("M")
    args.out.mkdir(parents=True, exist_ok=True)
    trades.to_csv(args.out / "trades.csv", index=False)
    report = {
        "status": "RESEARCH_ONLY_OHLC_PROXY",
        "costs": {
            "fee_bps_per_side": args.fee_bps,
            "spread_bps_per_side": args.spread_bps,
            "slippage_bps_per_side": args.slippage_bps,
        },
        "coverage": coverage,
        "by_period": {
            period: summarize(
                group,
                calendar_start if period == "IS" else pd.Period("2024-01", freq="M") if period == "VALIDATION" else pd.Period("2025-01", freq="M"),
                pd.Period("2023-12", freq="M") if period == "IS" else pd.Period("2024-12", freq="M") if period == "VALIDATION" else calendar_end,
            )
            for period, group in trades.groupby("period")
        },
        "by_family": {
            family: summarize(group, calendar_start, calendar_end)
            for family, group in trades.groupby("family")
        },
        "by_symbol_side": {
            f"{symbol}_{side}": summarize(group, calendar_start, calendar_end)
            for (symbol, side), group in trades.groupby(["symbol", "side"])
        },
        "oos_by_family": {
            family: summarize(
                group, pd.Period("2025-01", freq="M"), calendar_end
            )
            for family, group in trades[trades.period == "OOS"].groupby("family")
        },
        "oos_by_symbol_side": {
            f"{symbol}_{side}": summarize(
                group, pd.Period("2025-01", freq="M"), calendar_end
            )
            for (symbol, side), group in trades[trades.period == "OOS"].groupby(
                ["symbol", "side"]
            )
        },
        "combined": summarize(trades, calendar_start, calendar_end),
        "warning": "Equal-notional drawdown is a realized-PnL proxy with each trade weighted at 1/max-concurrency of starting equity (peak gross exposure 1x); it omits intratrade mark-to-market, margin and liquidation. Trade-sequence drawdown is also shown. Funding uses entry-notional approximation. OHLC fills are proxies, not confirmed exchange fills. No live/paper activation."
    }
    (args.out / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
