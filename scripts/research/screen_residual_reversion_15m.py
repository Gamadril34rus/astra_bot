#!/usr/bin/env python3
"""Research-only 15m residual reversion screen with closed 1h context.

BingX USDT-M perpetuals only. No live/production integration. OOS is deliberately
not evaluated: the 2026-09-01..2026-11-13 holdout is retired.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "INJUSDT")
ALTS = SYMBOLS[1:]
BAR_MS = 15 * 60_000
H1_MS = 60 * 60_000
MAX_HOLD_BARS = 36
IS_START = "2025-11-13T00:00:00Z"
IS_END = "2026-06-20T00:00:00Z"
VAL_END = "2026-09-01T00:00:00Z"
RETIRED_END = "2026-11-13T00:00:00Z"
BASE_COST_BPS = {"fee": 5.0, "spread": 2.0, "slippage": 3.0}


def _ms(value: str) -> int:
    return int(pd.Timestamp(value).timestamp() * 1000)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_bars(path: Path, interval_ms: int) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = ("open_time", "open", "high", "low", "close", "volume")
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing {sorted(missing)}")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["open_time"] = frame["open_time"].astype("int64")
    if frame.open_time.duplicated().any():
        raise ValueError(f"{path}: duplicate candle timestamps")
    frame = frame.sort_values("open_time").reset_index(drop=True)
    prices = frame[["open", "high", "low", "close"]]
    invalid = (prices <= 0).any(axis=1)
    invalid |= frame.high < prices[["open", "low", "close"]].max(axis=1)
    invalid |= frame.low > prices[["open", "high", "close"]].min(axis=1)
    invalid |= frame.volume < 0
    if invalid.any():
        raise ValueError(f"{path}: {int(invalid.sum())} invalid OHLCV rows")
    if not (frame.open_time.diff().dropna() == interval_ms).all():
        raise ValueError(f"{path}: gaps in {interval_ms}ms candles")
    frame["decision_time"] = frame.open_time + interval_ms
    return frame


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame.close.shift(1)
    true_range = pd.concat(
        [frame.high - frame.low, (frame.high - previous).abs(),
         (frame.low - previous).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def wilder_adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up = frame.high.diff()
    down = -frame.low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    previous = frame.close.shift(1)
    true_range = pd.concat(
        [frame.high - frame.low, (frame.high - previous).abs(),
         (frame.low - previous).abs()], axis=1
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_closed_h1_context(m15: pd.DataFrame, h1: pd.DataFrame) -> pd.DataFrame:
    hourly = h1.copy()
    hourly["h1_ema20"] = hourly.close.ewm(span=20, adjust=False).mean()
    hourly["h1_ema50"] = hourly.close.ewm(span=50, adjust=False).mean()
    hourly["h1_adx14"] = wilder_adx(hourly)
    hourly["h1_ema20_rising"] = hourly.h1_ema20 > hourly.h1_ema20.shift(3)
    hourly["h1_ema20_falling"] = hourly.h1_ema20 < hourly.h1_ema20.shift(3)
    hourly["h1_closed_at"] = hourly.decision_time
    result = pd.merge_asof(
        m15.sort_values("decision_time"),
        hourly[["decision_time", "h1_ema20", "h1_ema50", "h1_adx14",
                "h1_ema20_rising", "h1_ema20_falling", "h1_closed_at"]]
        .sort_values("decision_time"),
        on="decision_time", direction="backward", allow_exact_matches=True,
    )
    valid = result.h1_closed_at.notna()
    if not (result.loc[valid, "h1_closed_at"] <= result.loc[valid, "decision_time"]).all():
        raise ValueError("1h context contains an unclosed/future candle")
    return result


def add_residual_features(alt: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    if not np.array_equal(alt.open_time.to_numpy(), btc.open_time.to_numpy()):
        raise ValueError("alt and BTC 15m timestamps do not align")
    out = alt.copy()
    out["ret1"] = out.close.pct_change()
    out["btc_ret1"] = btc.close.pct_change().to_numpy()
    out["resid_sum6"] = (out.ret1 - out.btc_ret1).rolling(6, min_periods=6).sum()
    candle_range = (out.high - out.low).replace(0, np.nan)
    out["clv"] = ((out.close - out.low) / candle_range).fillna(0.5)
    out["atr14"] = wilder_atr(out)
    out["atr_ratio"] = out.atr14 / out.atr14.rolling(48, min_periods=48).mean()
    return out


def load_funding(path: Path, symbol: str, max_gap_hours: float) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["funding_time"] = pd.to_numeric(frame.funding_time, errors="raise").astype("int64")
    frame["rate"] = pd.to_numeric(frame.rate, errors="raise")
    if frame.funding_time.duplicated().any():
        raise ValueError(f"{path}: duplicate funding timestamps")
    frame = frame.sort_values("funding_time")
    frame = frame[(frame.funding_time >= _ms(IS_START)) &
                  (frame.funding_time < _ms(RETIRED_END))].reset_index(drop=True)
    gaps = frame.funding_time.diff().dropna() / 3_600_000
    if frame.empty or (len(gaps) and float(gaps.max()) > max_gap_hours):
        raise ValueError(f"{symbol}: incomplete funding coverage/gap")
    return frame


def raw_signal_side(row: pd.Series) -> int:
    if any(pd.isna(row.get(key)) for key in
           ("resid_sum6", "clv", "atr14", "atr_ratio", "h1_adx14", "ret1",
            "h1_ema20", "h1_ema50")):
        return 0
    if (row.resid_sum6 < -0.03 and row.clv > 0.55 and
            row.atr_ratio < 2.0 and row.ret1 > -0.008):
        return 1
    if (row.resid_sum6 > 0.03 and row.clv < 0.45 and
            row.atr_ratio < 2.0 and row.ret1 < 0.008):
        return -1
    return 0


def signal_side(row: pd.Series, mode: str) -> int:
    side = raw_signal_side(row)
    if not side:
        return 0
    if mode == "adx_range":
        return side if row.h1_adx14 <= 22.0 else 0
    if mode == "trend_aligned":
        if side == 1:
            return side if (row.h1_ema20 > row.h1_ema50 and
                            row.h1_ema20_rising and row.h1_adx14 >= 20.0) else 0
        return side if (row.h1_ema20 < row.h1_ema50 and
                        row.h1_ema20_falling and row.h1_adx14 >= 20.0) else 0
    raise ValueError(f"unknown mode: {mode}")


def funding_cost_pct(funding: pd.DataFrame, entry_ms: int, exit_ms: int, side: int) -> float:
    times = funding.funding_time.to_numpy(dtype="int64")
    rates = funding.rate.to_numpy(dtype=float)
    left = np.searchsorted(times, entry_ms, side="right")
    right = np.searchsorted(times, exit_ms, side="right")
    # Positive rate is paid by longs and received by shorts.
    return float(rates[left:right].sum()) * side * 100.0


def simulate_period(
    symbol: str, frame: pd.DataFrame, funding: pd.DataFrame,
    start_ms: int, end_ms: int, mode: str,
) -> tuple[list[dict], dict]:
    trades: list[dict] = []
    signals = rejected = 0
    i = 120
    while i < len(frame) - MAX_HOLD_BARS - 1:
        entry_i = i + 1
        entry_ms = int(frame.iloc[entry_i].open_time)
        if not (start_ms <= entry_ms and entry_ms + MAX_HOLD_BARS * BAR_MS < end_ms):
            i += 1
            continue
        row = frame.iloc[i]
        raw_side = raw_signal_side(row)
        side = signal_side(row, mode)
        if raw_side and not side:
            rejected += 1
        if not side:
            i += 1
            continue
        signals += 1
        entry = float(frame.iloc[entry_i].open)
        risk = 2.0 * float(row.atr14)
        if not np.isfinite(risk) or risk <= 0:
            i += 1
            continue
        stop = entry - side * risk
        target = entry + side * 2.5 * risk
        exit_i = None
        exit_price = None
        reason = ""
        best_mfe_r = 0.0
        for j in range(entry_i, min(entry_i + MAX_HOLD_BARS, len(frame))):
            bar = frame.iloc[j]
            op, hi, lo, close = map(float, (bar.open, bar.high, bar.low, bar.close))
            mfe = (hi - entry if side == 1 else entry - lo) / risk
            best_mfe_r = max(best_mfe_r, mfe)
            if side == 1:
                if lo <= stop:
                    exit_i, exit_price, reason = j, min(op, stop) if op < stop else stop, "stop"
                    break
                if hi >= target:
                    exit_i, exit_price, reason = j, target, "target"
                    break
            else:
                if hi >= stop:
                    exit_i, exit_price, reason = j, max(op, stop) if op > stop else stop, "stop"
                    break
                if lo <= target:
                    exit_i, exit_price, reason = j, target, "target"
                    break
            held = j - entry_i + 1
            if held >= 2 and best_mfe_r < 0.35:
                exit_i, exit_price, reason = j, close, "no_progress_30m"
                break
            if held >= 6 and best_mfe_r < 0.75:
                exit_i, exit_price, reason = j, close, "no_progress_90m"
                break
        if exit_i is None:
            exit_i = min(entry_i + MAX_HOLD_BARS - 1, len(frame) - 1)
            exit_price, reason = float(frame.iloc[exit_i].close), "max_hold_9h"
        exit_ms = int(frame.iloc[exit_i].decision_time)
        if exit_ms >= end_ms:
            i += 1
            continue
        gross = side * (exit_price / entry - 1.0) * 100.0
        funding_pct = funding_cost_pct(funding, entry_ms, exit_ms, side)
        fee = 2 * BASE_COST_BPS["fee"] / 100
        spread = 2 * BASE_COST_BPS["spread"] / 100
        slippage = 2 * BASE_COST_BPS["slippage"] / 100
        fee_stress, spread_stress, slip_stress = fee * 1.5, spread * 1.5, slippage * 1.5
        net = gross - fee - spread - slippage - funding_pct
        stress_net = gross - fee_stress - spread_stress - slip_stress - funding_pct
        trades.append({
            "symbol": symbol, "mode": mode, "side": "LONG" if side == 1 else "SHORT",
            "entry_time": entry_ms, "exit_time": exit_ms,
            "hold_min": (exit_ms - entry_ms) / 60_000, "exit_reason": reason,
            "gross_pct": gross, "fee_pct": fee, "spread_pct": spread,
            "slippage_pct": slippage, "funding_pct": funding_pct,
            "net_base_pct": net, "fee_stress_pct": fee_stress,
            "spread_stress_pct": spread_stress, "slippage_stress_pct": slip_stress,
            "net_stress_pct": stress_net,
        })
        # One open trade per symbol; next decision follows the exit bar.
        i = exit_i + 1
    return trades, {"signals": signals, "regime_rejected": rejected}


def summarize(trades: list[dict], start_ms: int, end_ms: int, stress: bool = False) -> dict:
    if not trades:
        return {"trades": 0, "pf": 0.0, "expectancy_pct": 0.0,
                "win_rate_pct": 0.0, "positive_month_share_pct": 0.0}
    data = pd.DataFrame(trades).sort_values(["exit_time", "entry_time"])
    net = data.net_stress_pct if stress else data.net_base_pct
    gross_win = float(net[net > 0].sum())
    gross_loss = float(-net[net <= 0].sum())
    pf = gross_win / gross_loss if gross_loss else (float("inf") if gross_win else 0.0)
    month = pd.to_datetime(data.exit_time, unit="ms", utc=True).dt.tz_localize(None).dt.to_period("M")
    monthly = pd.DataFrame({"month": month, "net": net}).groupby("month").net.sum()
    first = pd.to_datetime(start_ms, unit="ms", utc=True).tz_localize(None).to_period("M")
    last = pd.to_datetime(end_ms - 1, unit="ms", utc=True).tz_localize(None).to_period("M")
    monthly = monthly.reindex(pd.period_range(first, last, freq="M"), fill_value=0.0)
    equity = (1 + net.to_numpy() / 100).cumprod()
    drawdown = equity / np.maximum.accumulate(np.r_[1.0, equity])[1:] - 1
    pnl_usd = 1000 * net.to_numpy() / 100
    portfolio = 10_000 + np.cumsum(pnl_usd)
    peak = np.maximum.accumulate(np.r_[10_000, portfolio])[1:]
    events = []
    for trade in data.itertuples():
        events.extend(((int(trade.entry_time), 1), (int(trade.exit_time), -1)))
    active = max_active = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        max_active = max(max_active, active)
    return {
        "trades": len(data), "long_trades": int((data.side == "LONG").sum()),
        "short_trades": int((data.side == "SHORT").sum()), "pf": round(float(pf), 4),
        "expectancy_pct": round(float(net.mean()), 5),
        "win_rate_pct": round(float((net > 0).mean() * 100), 2),
        "positive_month_share_pct": round(float((monthly > 0).mean() * 100), 2),
        "median_hold_min": round(float(data.hold_min.median()), 1),
        "mean_hold_min": round(float(data.hold_min.mean()), 1),
        "p90_hold_min": round(float(data.hold_min.quantile(.9)), 1),
        "max_hold_min": round(float(data.hold_min.max()), 1),
        "gross_pct_sum": round(float(data.gross_pct.sum()), 4),
        "fee_pct_sum": round(float((data.fee_stress_pct if stress else data.fee_pct).sum()), 4),
        "spread_pct_sum": round(float((data.spread_stress_pct if stress else data.spread_pct).sum()), 4),
        "slippage_pct_sum": round(float((data.slippage_stress_pct if stress else data.slippage_pct).sum()), 4),
        "funding_pct_sum": round(float(data.funding_pct.sum()), 4),
        "net_pct_sum": round(float(net.sum()), 4),
        "trade_sequence_dd_pct": round(float(drawdown.min() * 100), 2),
        "fixed_1k_notional_dd_usdt": round(float((portfolio - peak).min()), 2),
        "max_concurrent": max_active,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/bingx_usdm"))
    parser.add_argument("--funding-dir", type=Path, default=Path("data/bingx_funding_merged"))
    parser.add_argument("--out-dir", type=Path, default=Path("reports/research/residual_15m"))
    args = parser.parse_args()
    candle_manifest = json.loads((args.data_dir / "manifest.json").read_text())
    funding_manifest = json.loads((args.funding_dir / "funding_manifest.json").read_text())
    if candle_manifest.get("exchange") != "BingX" or funding_manifest.get("exchange") != "BingX":
        raise SystemExit("Only BingX source manifests are accepted")
    if (candle_manifest.get("market") != "USDT-M perpetual swaps" or
            funding_manifest.get("market") != "USDT-M perpetual swaps"):
        raise SystemExit("Non-USDT-M source data is not accepted")
    if candle_manifest.get("requested_start_utc") != "2025-11-13T00:00:00+00:00":
        raise SystemExit("Unexpected candle archive start")
    if candle_manifest.get("exclusive_end_utc") != "2026-11-13T00:00:00+00:00":
        raise SystemExit("Unexpected candle archive end")

    btc = load_bars(args.data_dir / "BTCUSDT_15m.csv", BAR_MS)
    prepared, hashes, coverage = [], {}, {}
    hashes["manifest.json"] = sha256_file(args.data_dir / "manifest.json")
    hashes["funding_manifest.json"] = sha256_file(args.funding_dir / "funding_manifest.json")
    for symbol in SYMBOLS:
        meta = candle_manifest["symbols"][symbol.replace("USDT", "-USDT")]
        for tf, _interval, field in (
            ("1m", 60_000, "one_minute_sha256"),
            ("15m", BAR_MS, "merged_15m_sha256"),
            ("1h", H1_MS, "merged_1h_sha256"),
        ):
            path = args.data_dir / f"{symbol}_{tf}.csv"
            if sha256_file(path) != meta[field]:
                raise SystemExit(f"SHA-256 mismatch: {path}")
            hashes[path.name] = meta[field]
        if int(meta.get("missing_minute_bars", 0)) != 0:
            raise SystemExit(f"{symbol}: minute archive has missing bars")
        if int(meta.get("duplicate_minute_bars", 0)) != 0:
            raise SystemExit(f"{symbol}: minute archive has duplicates")

    for symbol in ALTS:
        m15 = load_bars(args.data_dir / f"{symbol}_15m.csv", BAR_MS)
        h1 = load_bars(args.data_dir / f"{symbol}_1h.csv", H1_MS)
        funding_meta = next(item for item in funding_manifest["reports"]
                            if item["symbol"] == symbol.replace("USDT", "-USDT"))
        funding_path = args.funding_dir / f"funding_{symbol}.csv"
        if sha256_file(funding_path) != funding_meta["sha256"]:
            raise SystemExit(f"funding SHA-256 mismatch: {funding_path}")
        if (not funding_meta.get("cadence_ok") or
                not funding_meta.get("requested_window_edges_ok") or
                funding_meta.get("coverage_start_utc") != IS_START or
                funding_meta.get("coverage_end_utc") != RETIRED_END):
            raise SystemExit(f"{symbol}: funding manifest coverage is not complete")
        hashes[funding_path.name] = funding_meta["sha256"]
        funding = load_funding(
            funding_path, symbol, float(funding_meta["max_gap_hours_latest_segment"])
        )
        features = add_closed_h1_context(m15, h1)
        features = add_residual_features(features, btc)
        prepared.append((symbol, features, funding))
        coverage[symbol] = {
            "15m_rows": len(m15), "1h_rows": len(h1),
            "funding_events": len(funding),
            "funding_first": int(funding.funding_time.iloc[0]),
            "funding_last": int(funding.funding_time.iloc[-1]),
            "funding_max_gap_hours": float(funding.funding_time.diff().max() / 3_600_000),
        }

    is_start, is_end, val_end = map(_ms, (IS_START, IS_END, VAL_END))
    report = {
        "status": "IS_VALIDATION_ONLY_NO_OOS",
        "strategy": "15m residual mean reversion vs BTC with closed 1h regime filters",
        "rules": {
            "residual_window_bars": 6, "residual_threshold": 0.03,
            "clv_long": 0.55, "clv_short": 0.45, "atr_ratio_cap": 2.0,
            "stop_atr": 2.0, "target_r": 2.5,
            "no_progress_30m_mfe_r": 0.35, "no_progress_90m_mfe_r": 0.75,
            "max_hold_minutes": 540, "entry": "next 15m open",
        },
        "costs": {"base_per_side_bps": BASE_COST_BPS, "stress_multiplier": 1.5},
        "data_sha256": hashes, "coverage": coverage,
        "splits_utc": {"IS": [IS_START, IS_END], "VALIDATION": [IS_END, VAL_END],
                       "RETIRED_HOLDOUT": [VAL_END, RETIRED_END],
                       "FRESH_OOS_START": RETIRED_END},
        "variants": {}, "by_symbol_side": {},
        "oos_status": "Not evaluated: fresh OOS archive begins 2026-11-13 and requires >=90 complete days.",
    }
    all_trades = []
    for mode in ("adx_range", "trend_aligned"):
        report["variants"][mode] = {}
        for name, start, end in (("IS", is_start, is_end),
                                 ("VALIDATION", is_end, val_end)):
            trades, diagnostics = [], {}
            for symbol, frame, funding in prepared:
                rows, diag = simulate_period(symbol, frame, funding, start, end, mode)
                trades.extend(rows)
                diagnostics[symbol] = diag
            report["variants"][mode][name] = {
                "base": summarize(trades, start, end),
                "stress": summarize(trades, start, end, stress=True),
                "diagnostics": diagnostics,
            }
            for (symbol, side) in sorted({(t["symbol"], t["side"]) for t in trades}):
                group = [t for t in trades if t["symbol"] == symbol and t["side"] == side]
                report["by_symbol_side"][f"{mode}:{name}:{symbol}:{side}"] = {
                    "base": summarize(group, start, end),
                    "stress": summarize(group, start, end, stress=True),
                }
            all_trades.extend({**trade, "period": name} for trade in trades)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "summary.json").write_text(json.dumps(report, indent=2))
    pd.DataFrame(all_trades).to_csv(args.out_dir / "trades.csv", index=False)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
