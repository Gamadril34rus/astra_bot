#!/usr/bin/env python3
"""Research-only 30m intraday strategies for verified USDⓈ-M perpetual futures.

Signals use closed 30m bars and completed 1h context. Entries are next-bar opens.
The runner models taker fees, adverse slippage, realized funding, conservative
same-bar stop-first handling, a 30m no-progress exit, and a hard 9h time stop.
It never connects to BingX or any trading endpoint.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_URL = "https://fapi.binance.com"
INTERVAL = "30m"
INTERVAL_MS = 30 * 60 * 1000
FEE_PER_SIDE = 0.0005
BASE_SLIPPAGE_PER_SIDE = 0.0010
STRESS_SLIPPAGE_PER_SIDE = 0.0015
MAX_HOLD_BARS = 18  # 18 x 30m = 9h
NO_PROGRESS_BARS = 1
NO_PROGRESS_MFE_R = 0.35
MAX_COST_TO_R = 0.40

# A common lower bound is used for every contract. Actual history starts at
# max(2021-01-01, exchangeInfo.onboardDate), never at a guessed listing date.
DATA_START_FLOOR = "2021-01-01"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "INJUSDT")

STRATEGIES = {
    "trend_pullback": {"stop_atr": 1.35, "target_r": 1.50, "target_mode": "fixed"},
    "trend_expansion": {"stop_atr": 1.60, "target_r": 1.60, "target_mode": "fixed"},
    "range_reversion": {"stop_atr": 1.25, "target_r": 1.00, "target_mode": "band_mid"},
}
COST_SCENARIOS = {
    "base_30bps_rt": BASE_SLIPPAGE_PER_SIDE,
    "stress_40bps_rt": STRESS_SLIPPAGE_PER_SIDE,
}


def utc_ms(value: str) -> int:
    stamp = pd.Timestamp(value, tz="UTC")
    return int(stamp.timestamp() * 1000)


def request_json(path: str, params: dict[str, Any], retries: int = 5) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{path}?{query}"
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "astra-bot-perp-research/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in (418, 429, 500, 502, 503, 504):
                raise RuntimeError(f"{path} returned HTTP {exc.code}: {exc.read()[:300]!r}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
        wait = min(2 ** attempt, 20)
        time.sleep(wait)
    raise RuntimeError(f"request failed after {retries} attempts: {path}: {last_error}")


def get_perpetual_contracts() -> dict[str, dict[str, Any]]:
    payload = request_json("/fapi/v1/exchangeInfo", {})
    return {
        row["symbol"]: row
        for row in payload.get("symbols", [])
        if row.get("contractType") == "PERPETUAL" and row.get("quoteAsset") == "USDT"
    }


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list[list[Any]]:
    rows: list[list[Any]] = []
    cursor = start_ms
    while cursor < end_ms:
        page = request_json(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": INTERVAL, "startTime": cursor, "endTime": end_ms - 1, "limit": 1500},
        )
        if not page:
            break
        rows.extend(page)
        next_cursor = int(page[-1][0]) + INTERVAL_MS
        if next_cursor <= cursor:
            raise RuntimeError(f"non-advancing kline cursor for {symbol}: {cursor} -> {next_cursor}")
        cursor = next_cursor
        if len(page) < 1500:
            break
        time.sleep(0.08)
    return rows


def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = start_ms
    while cursor < end_ms:
        page = request_json(
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": cursor, "endTime": end_ms - 1, "limit": 1000},
        )
        if not page:
            break
        rows.extend(page)
        next_cursor = int(page[-1]["fundingTime"]) + 1
        if next_cursor <= cursor:
            raise RuntimeError(f"non-advancing funding cursor for {symbol}")
        cursor = next_cursor
        if len(page) < 1000:
            break
        time.sleep(0.08)
    return rows


def validate_market_data(
    symbol: str,
    contract: dict[str, Any],
    klines: list[list[Any]],
    funding: list[dict[str, Any]],
    end_ms: int,
) -> None:
    if contract.get("contractType") != "PERPETUAL" or contract.get("quoteAsset") != "USDT":
        raise ValueError(f"{symbol}: instrument is not a USDT perpetual")
    if contract.get("status") != "TRADING":
        raise ValueError(f"{symbol}: contract is not currently TRADING")
    onboard_ms = int(contract.get("onboardDate", 0) or 0)
    if not klines or len(klines) < 500:
        raise ValueError(f"{symbol}: insufficient 30m kline history ({len(klines)})")
    opens = [int(row[0]) for row in klines]
    if any(value % INTERVAL_MS for value in opens):
        raise ValueError(f"{symbol}: kline timestamps are not aligned to 30m UTC boundaries")
    if len(opens) != len(set(opens)):
        raise ValueError(f"{symbol}: duplicate kline open times")
    if opens != sorted(opens):
        raise ValueError(f"{symbol}: klines are not ordered")
    if any((right - left) != INTERVAL_MS for left, right in zip(opens, opens[1:])):
        raise ValueError(f"{symbol}: missing or irregular 30m intervals")
    if opens[-1] + INTERVAL_MS > end_ms:
        raise ValueError(f"{symbol}: latest bar is not fully closed")
    if onboard_ms and opens[0] < onboard_ms:
        raise ValueError(f"{symbol}: candle history predates contract onboardDate")
    for row in klines:
        open_p, high, low, close, volume = map(float, (row[1], row[2], row[3], row[4], row[5]))
        if min(open_p, high, low, close) <= 0 or volume < 0:
            raise ValueError(f"{symbol}: non-positive OHLC or negative volume")
        if high < max(open_p, close, low) or low > min(open_p, close, high):
            raise ValueError(f"{symbol}: invalid OHLC geometry")
    if not funding:
        raise ValueError(f"{symbol}: no funding history returned")
    times = [int(row["fundingTime"]) for row in funding]
    if times != sorted(set(times)):
        raise ValueError(f"{symbol}: duplicate or unordered funding events")
    if any(not math.isfinite(float(row["fundingRate"])) for row in funding):
        raise ValueError(f"{symbol}: non-finite funding rate")
    if any(float(row.get("markPrice", 0) or 0) <= 0 for row in funding):
        raise ValueError(f"{symbol}: funding event missing its mark price")
    # Funding cadence is normally 8h, but tolerate up to one day to avoid
    # inventing zero funding across an unobserved coverage gap.
    if times[0] > opens[0] + 24 * 60 * 60 * 1000:
        raise ValueError(f"{symbol}: funding coverage starts more than 24h after candles")
    if times[-1] < opens[-1] - 24 * 60 * 60 * 1000:
        raise ValueError(f"{symbol}: funding coverage ends more than 24h before candles")
    if any(right - left > 24 * 60 * 60 * 1000 for left, right in zip(times, times[1:])):
        raise ValueError(f"{symbol}: funding history contains a gap larger than 24h")


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    previous = frame["close"].shift(1)
    tr = pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - previous).abs(), (frame["low"] - previous).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def build_frame(klines: list[list[Any]]) -> pd.DataFrame:
    columns = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
    ]
    frame = pd.DataFrame(klines, columns=columns)
    frame["bar_open"] = pd.to_datetime(frame["open_time"].astype("int64"), unit="ms", utc=True)
    frame = frame.set_index("bar_open")
    numeric = ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote_volume"]
    frame[numeric] = frame[numeric].astype(float)
    close = frame["close"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - previous_close).abs(), (frame["low"] - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    frame["atr"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    frame["ema20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    frame["ema50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    avg_quote_volume = frame["quote_volume"].shift(1).rolling(20, min_periods=20).mean()
    frame["volume_ratio"] = frame["quote_volume"] / avg_quote_volume
    frame["taker_buy_ratio"] = frame["taker_buy_quote_volume"] / frame["quote_volume"].replace(0, np.nan)
    delta = close.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    frame["rsi"] = 100 - 100 / (1 + rs)
    middle = close.rolling(20, min_periods=20).mean()
    deviation = close.rolling(20, min_periods=20).std(ddof=0)
    frame["bb_mid"] = middle
    frame["bb_upper"] = middle + 2.0 * deviation
    frame["bb_lower"] = middle - 2.0 * deviation

    hourly = frame.resample("1h", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    hourly["ema20"] = hourly["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    hourly["ema50"] = hourly["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    hourly["adx"] = _adx(hourly)
    htf = hourly[["close", "ema20", "ema50", "adx"]].rename(
        columns={"close": "htf_close", "ema20": "htf_ema20", "ema50": "htf_ema50", "adx": "htf_adx"}
    )
    htf["available_at"] = htf.index
    base = frame.reset_index()
    base["decision_at"] = base["bar_open"] + pd.Timedelta(minutes=30)
    merged = pd.merge_asof(
        base.sort_values("decision_at"),
        htf.reset_index(drop=True).sort_values("available_at"),
        left_on="decision_at", right_on="available_at", direction="backward", allow_exact_matches=True,
    )
    return merged.set_index("bar_open").sort_index()


def signal_masks(frame: pd.DataFrame) -> dict[tuple[str, int], pd.Series]:
    uptrend = (frame["htf_ema20"] > frame["htf_ema50"]) & (frame["htf_close"] > frame["htf_ema20"])
    downtrend = (frame["htf_ema20"] < frame["htf_ema50"]) & (frame["htf_close"] < frame["htf_ema20"])
    neutral = (
        ((frame["htf_ema20"] / frame["htf_ema50"] - 1).abs() <= 0.003)
        & (frame["htf_adx"] < 22)
    )
    touch_long = (
        (frame["low"].shift(1) <= frame["ema20"].shift(1) + 0.15 * frame["atr"].shift(1))
        & (frame["low"].shift(1) >= frame["ema20"].shift(1) - 0.60 * frame["atr"].shift(1))
    )
    touch_short = (
        (frame["high"].shift(1) >= frame["ema20"].shift(1) - 0.15 * frame["atr"].shift(1))
        & (frame["high"].shift(1) <= frame["ema20"].shift(1) + 0.60 * frame["atr"].shift(1))
    )
    pullback_long = (
        uptrend & (frame["ema20"] > frame["ema50"]) & touch_long
        & (frame["close"] > frame["high"].shift(1)) & (frame["close"] > frame["ema20"])
        & frame["rsi"].between(50, 68) & (frame["volume_ratio"] >= 0.85)
    )
    pullback_short = (
        downtrend & (frame["ema20"] < frame["ema50"]) & touch_short
        & (frame["close"] < frame["low"].shift(1)) & (frame["close"] < frame["ema20"])
        & frame["rsi"].between(32, 50) & (frame["volume_ratio"] >= 0.85)
    )
    prior_high = frame["high"].shift(1).rolling(12, min_periods=12).max()
    prior_low = frame["low"].shift(1).rolling(12, min_periods=12).min()
    expansion_long = (
        uptrend & (frame["close"] > prior_high) & (frame["volume_ratio"] >= 1.35)
        & (frame["taker_buy_ratio"] >= 0.52) & frame["rsi"].between(54, 75)
    )
    expansion_short = (
        downtrend & (frame["close"] < prior_low) & (frame["volume_ratio"] >= 1.35)
        & (frame["taker_buy_ratio"] <= 0.48) & frame["rsi"].between(25, 46)
    )
    fade_long = (
        neutral & (frame["close"].shift(1) < frame["bb_lower"].shift(1))
        & (frame["close"] > frame["bb_lower"]) & (frame["close"] > frame["open"])
        & frame["rsi"].between(25, 45) & (frame["volume_ratio"] >= 0.75)
    )
    fade_short = (
        neutral & (frame["close"].shift(1) > frame["bb_upper"].shift(1))
        & (frame["close"] < frame["bb_upper"]) & (frame["close"] < frame["open"])
        & frame["rsi"].between(55, 75) & (frame["volume_ratio"] >= 0.75)
    )
    masks = {
        ("trend_pullback", 1): pullback_long,
        ("trend_pullback", -1): pullback_short,
        ("trend_expansion", 1): expansion_long,
        ("trend_expansion", -1): expansion_short,
        ("range_reversion", 1): fade_long,
        ("range_reversion", -1): fade_short,
    }
    return {key: value.fillna(False).astype(bool) for key, value in masks.items()}


def funding_cashflow(side: int, funding_rate: float, mark_price: float, entry_price: float) -> float:
    """Funding return on initial notional: positive rates are paid by longs."""
    return -side * funding_rate * mark_price / entry_price


def adverse_fill(price: float, side: int, is_entry: bool, slippage: float) -> float:
    direction = side if is_entry else -side
    return price * (1 + direction * slippage)


def net_trade_return(
    side: int,
    entry_raw: float,
    exit_raw: float,
    slippage: float,
    fee_per_side: float,
    funding_return: float,
) -> tuple[float, float, float]:
    entry_fill = adverse_fill(entry_raw, side, True, slippage)
    exit_fill = adverse_fill(exit_raw, side, False, slippage)
    price_return = side * (exit_fill - entry_fill) / entry_raw
    fee_return = fee_per_side * (entry_fill + exit_fill) / entry_raw
    return price_return - fee_return + funding_return, fee_return, price_return


def resolve_barrier_exit(side: int, open_p: float, high: float, low: float, stop: float, take: float) -> tuple[float, str] | None:
    """Stop-first adverse OHLC resolution. An OHLC touch is only a proxy fill."""
    if side > 0:
        if open_p <= stop or low <= stop:
            return min(open_p, stop), "stop"
        if open_p >= take or high >= take:
            return take, "take"
    else:
        if open_p >= stop or high >= stop:
            return max(open_p, stop), "stop"
        if open_p <= take or low <= take:
            return take, "take"
    return None


@dataclass(frozen=True)
class Trade:
    symbol: str
    strategy: str
    side: str
    entry_time: str
    exit_time: str
    hold_minutes: int
    entry_price: float
    exit_price: float
    stop_price: float
    take_price: float
    exit_reason: str
    risk_pct: float
    gross_price_return_pct: float
    fee_pct: float
    slippage_pct: float
    funding_pct: float
    net_return_pct: float
    net_r: float
    cost_to_r: float


def run_backtest(
    symbol: str,
    frame: pd.DataFrame,
    funding_rows: list[dict[str, Any]],
    strategy: str,
    side: int,
    slippage: float,
) -> list[Trade]:
    config = STRATEGIES[strategy]
    signals = signal_masks(frame)[(strategy, side)].to_numpy(dtype=bool)
    funding_times = [int(row["fundingTime"]) for row in funding_rows]
    funding_rates = [float(row["fundingRate"]) for row in funding_rows]
    funding_marks = [float(row["markPrice"]) for row in funding_rows]
    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    bb_mid = frame["bb_mid"].to_numpy(dtype=float)
    stamps = frame.index
    results: list[Trade] = []
    signal_idx = 0
    while signal_idx < len(frame) - 1:
        if not signals[signal_idx] or not math.isfinite(atr[signal_idx]) or atr[signal_idx] <= 0:
            signal_idx += 1
            continue
        entry_idx = signal_idx + 1
        entry_raw = opens[entry_idx]
        stop_distance = atr[signal_idx] * float(config["stop_atr"])
        risk_pct = stop_distance / entry_raw
        # Eligibility is frozen to the stress cost model so base and stress
        # results use the exact same trades. Only net PnL changes by scenario.
        estimated_round_trip_cost = 2 * (FEE_PER_SIDE + STRESS_SLIPPAGE_PER_SIDE)
        cost_to_r = estimated_round_trip_cost / risk_pct if risk_pct > 0 else math.inf
        if risk_pct <= 0 or cost_to_r > MAX_COST_TO_R:
            signal_idx += 1
            continue
        stop = entry_raw - side * stop_distance
        if config["target_mode"] == "band_mid":
            take = float(bb_mid[signal_idx])
            target_distance = side * (take - entry_raw)
            if not math.isfinite(take) or target_distance < 0.85 * stop_distance:
                signal_idx += 1
                continue
        else:
            take = entry_raw + side * stop_distance * float(config["target_r"])

        exit_idx = None
        exit_raw = None
        exit_reason = ""
        max_exit_idx = min(entry_idx + MAX_HOLD_BARS - 1, len(frame) - 1)
        best_mfe = 0.0
        for bar_idx in range(entry_idx, max_exit_idx + 1):
            barrier = resolve_barrier_exit(side, opens[bar_idx], highs[bar_idx], lows[bar_idx], stop, take)
            favourable = (highs[bar_idx] - entry_raw) if side > 0 else (entry_raw - lows[bar_idx])
            best_mfe = max(best_mfe, favourable / stop_distance)
            if barrier is not None:
                exit_raw, exit_reason = barrier
                exit_idx = bar_idx
                break
            held_bars = bar_idx - entry_idx + 1
            if held_bars >= NO_PROGRESS_BARS and best_mfe < NO_PROGRESS_MFE_R:
                exit_raw = closes[bar_idx]
                exit_idx = bar_idx
                exit_reason = "no_progress_30m"
                break
            if bar_idx == max_exit_idx:
                exit_raw = closes[bar_idx]
                exit_idx = bar_idx
                exit_reason = "max_hold_9h"
                break
        if exit_idx is None or exit_raw is None:
            break

        entry_ms = int(stamps[entry_idx].timestamp() * 1000)
        exit_ms = int((stamps[exit_idx] + pd.Timedelta(minutes=30)).timestamp() * 1000)
        left = bisect.bisect_right(funding_times, entry_ms)
        right = bisect.bisect_right(funding_times, exit_ms)
        funding_return = sum(
            funding_cashflow(side, funding_rates[pos], funding_marks[pos], entry_raw)
            for pos in range(left, right)
        )
        net_return, fee_return, slipped_price_return = net_trade_return(
            side, entry_raw, float(exit_raw), slippage, FEE_PER_SIDE, funding_return
        )
        gross_return = side * (float(exit_raw) - entry_raw) / entry_raw
        slippage_return = gross_return - slipped_price_return
        hold_minutes = (exit_idx - entry_idx + 1) * 30
        results.append(
            Trade(
                symbol=symbol,
                strategy=strategy,
                side="LONG" if side > 0 else "SHORT",
                entry_time=stamps[entry_idx].isoformat(),
                exit_time=(stamps[exit_idx] + pd.Timedelta(minutes=30)).isoformat(),
                hold_minutes=hold_minutes,
                entry_price=round(entry_raw, 10),
                exit_price=round(float(exit_raw), 10),
                stop_price=round(stop, 10),
                take_price=round(take, 10),
                exit_reason=exit_reason,
                risk_pct=risk_pct * 100,
                gross_price_return_pct=gross_return * 100,
                fee_pct=fee_return * 100,
                slippage_pct=slippage_return * 100,
                funding_pct=funding_return * 100,
                net_return_pct=net_return * 100,
                net_r=net_return / risk_pct,
                cost_to_r=cost_to_r,
            )
        )
        # The exit is known at this bar's close; the next eligible signal can
        # only be evaluated on this close and filled at the following bar open.
        signal_idx = exit_idx
    return results


def metrics(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {"n": 0, "pf": None, "mean_net_r": None, "win_rate_pct": None, "mean_hold_min": None, "median_hold_min": None, "max_hold_min": None, "max_dd_pct_at_0_5pct_risk": None}
    trades = sorted(trades, key=lambda trade: trade.entry_time)
    values = np.array([trade.net_r for trade in trades], dtype=float)
    wins = values[values > 0].sum()
    losses = -values[values <= 0].sum()
    pf = float(wins / losses) if losses > 0 else (math.inf if wins > 0 else 0.0)
    equity = np.cumprod(1 + 0.005 * values)
    peaks = np.maximum.accumulate(equity)
    drawdown = float(np.max((peaks - equity) / peaks) * 100)
    holds = [trade.hold_minutes for trade in trades]
    return {
        "n": len(trades),
        "pf": pf,
        "mean_net_r": float(values.mean()),
        "win_rate_pct": float((values > 0).mean() * 100),
        "mean_hold_min": float(np.mean(holds)),
        "median_hold_min": float(np.median(holds)),
        "max_hold_min": int(max(holds)),
        "max_dd_pct_at_0_5pct_risk": drawdown,
        "net_return_pct_sum": float(sum(trade.net_return_pct for trade in trades)),
    }


def period_trades(trades: list[Trade], start: str, end: str | None = None) -> list[Trade]:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else None
    selected = []
    for trade in trades:
        timestamp = pd.Timestamp(trade.entry_time)
        if timestamp < start_ts:
            continue
        if end_ts is not None and timestamp >= end_ts:
            continue
        selected.append(trade)
    return selected


def safe_number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            return "∞" if value > 0 else "—"
        return f"{float(value):.3f}"
    return str(value)


def write_report(
    output: Path,
    manifest: dict[str, Any],
    all_results: dict[str, dict[str, dict[str, list[Trade]]]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rows = [
        trade.__dict__ | {"cost_scenario": scenario}
        for scenario, groups in all_results.items()
        for symbol_strategies in groups.values()
        for trades in symbol_strategies.values()
        for trade in trades
    ]
    pd.DataFrame(rows).to_csv(output / "trades.csv", index=False)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# 30m perpetual intraday research report", "",
        "Research only. This is a Binance USDⓈ-M perpetual reference-market test, not a BingX fill simulation and not a live-performance claim.", "",
        f"Data cutoff (UTC): {manifest['data_cutoff_utc']}", "",
        "## Universe and data integrity", "",
        "| Symbol | First 30m bar | Last 30m bar | 30m bars | Funding events | Contract |",
        "|---|---|---|---:|---:|---|",
    ]
    for symbol, meta in manifest["symbols"].items():
        lines.append(f"| {symbol} | {meta['first_bar_utc']} | {meta['last_bar_utc']} | {meta['bars']} | {meta['funding_events']} | USDT-M PERPETUAL |")
    lines += [
        "", "## Aggregate IS and OOS results by family and cost case", "",
        "IS: entries from 2021-01-01 through 2023-12-31. OOS: entries from 2024-01-01 through the data cutoff.", "",
        "| Cost case | Period | Strategy | Trades | PF | Mean net R | Win % | Mean hold min | Median hold min | Max DD % (0.5% R) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario, groups in all_results.items():
        for period_name, period_start, period_end in (
            ("IS", "2021-01-01", "2024-01-01"),
            ("OOS", "2024-01-01", None),
        ):
            for strategy in STRATEGIES:
                subset = [
                    trade
                    for symbol_trades in groups.values()
                    for trade in period_trades(symbol_trades.get(strategy, []), period_start, period_end)
                ]
                m = metrics(subset)
                lines.append(
                    "| " + " | ".join([
                        scenario, period_name, strategy, safe_number(m["n"]), safe_number(m["pf"]),
                        safe_number(m["mean_net_r"]), safe_number(m["win_rate_pct"]),
                        safe_number(m["mean_hold_min"]), safe_number(m["median_hold_min"]),
                        safe_number(m["max_dd_pct_at_0_5pct_risk"]),
                    ]) + " |"
                )
    lines += ["", "## OOS by symbol and side (base cost)", "", "| Symbol | Strategy | Side | Trades | PF | Mean net R | Win % | Mean hold min |", "|---|---|---|---:|---:|---:|---:|---:|"]
    base_groups = all_results["base_30bps_rt"]
    for symbol, strategies in base_groups.items():
        for strategy in STRATEGIES:
            for side in ("LONG", "SHORT"):
                subset = [t for t in period_trades(strategies.get(strategy, []), "2024-01-01") if t.side == side]
                m = metrics(subset)
                lines.append("| " + " | ".join([symbol, strategy, side, safe_number(m["n"]), safe_number(m["pf"]), safe_number(m["mean_net_r"]), safe_number(m["win_rate_pct"]), safe_number(m["mean_hold_min"])]) + " |")
    lines += ["", "## OOS yearly stability (base cost)", "", "| Year | Strategy | Trades | PF | Mean net R | Mean hold min |", "|---:|---|---:|---:|---:|---:|"]
    base_all = [t for values in base_groups.values() for family in values.values() for t in family]
    for year in (2024, 2025, 2026):
        for strategy in STRATEGIES:
            subset = [t for t in base_all if t.strategy == strategy and pd.Timestamp(t.entry_time).year == year]
            m = metrics(subset)
            lines.append("| " + " | ".join([str(year), strategy, safe_number(m["n"]), safe_number(m["pf"]), safe_number(m["mean_net_r"]), safe_number(m["mean_hold_min"])]) + " |")
    lines += ["", "## Promotion rule", "", "No candidate is promoted by this report. A family may proceed to a separate BingX-specific validation only if it has at least 100 OOS closed trades pooled, positive net expectancy and PF >= 1.30 in both base and stress cost cases, positive results in at least 3 of the 5 symbols where history permits, both directions separately reviewed, no single symbol accounting for more than 50% of net profit, mean holding <= 90 minutes, median holding <= 60 minutes, maximum holding <= 9h, and acceptable rolling-year stability. A promising Binance proxy is only a candidate for BingX VST validation, not permission for live execution.", "", "## Limitations", "", "- Binance perpetual data is a reference-market proxy. Venue basis, BingX liquidity, actual bid/ask spread, market impact, queue priority, order trigger semantics and fill latency are not reproduced.", "- OHLC bars cannot reveal the within-bar path. Stop-first is intentionally adverse but does not recover actual execution prices.", "- Funding uses historical Binance funding events, not BingX funding history.", "- Unlevered returns exclude margin utilization, liquidation, portfolio correlation, wallet transfers and account-level constraints.", "- No parameter search is performed; thresholds are frozen before the run. A negative result is retained, not tuned away.", "", "*Generated by scripts/research_perp_intraday.py. Research only; no live or VST execution wiring.*", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/research_perp_intraday")
    parser.add_argument("--end", default=None, help="UTC end date, exclusive; defaults to latest fully closed 30m candle")
    args = parser.parse_args()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    end_ms = utc_ms(args.end) if args.end else (now_ms // INTERVAL_MS) * INTERVAL_MS
    if end_ms <= utc_ms("2024-01-01"):
        raise SystemExit("end must be later than 2024-01-01 UTC")
    contracts = get_perpetual_contracts()
    manifest: dict[str, Any] = {
        "venue": "Binance USDⓈ-M Futures",
        "contract_type": "PERPETUAL",
        "quote_asset": "USDT",
        "interval": INTERVAL,
        "data_cutoff_utc": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        "source_endpoints": ["/fapi/v1/exchangeInfo", "/fapi/v1/klines", "/fapi/v1/fundingRate"],
        "cost_model": {"fee_per_side": FEE_PER_SIDE, "base_slippage_per_side": BASE_SLIPPAGE_PER_SIDE, "stress_slippage_per_side": STRESS_SLIPPAGE_PER_SIDE, "max_cost_to_r": MAX_COST_TO_R},
        "holding_model": {"no_progress_bars": NO_PROGRESS_BARS, "no_progress_mfe_r": NO_PROGRESS_MFE_R, "max_hold_bars": MAX_HOLD_BARS},
        "symbols": {},
    }
    market_frames: dict[str, tuple[pd.DataFrame, list[dict[str, Any]]]] = {}
    for symbol in SYMBOLS:
        contract = contracts.get(symbol)
        if contract is None:
            raise RuntimeError(f"{symbol}: no active USDT perpetual contract found in exchangeInfo")
        start_ms = max(utc_ms(DATA_START_FLOOR), int(contract.get("onboardDate", 0) or 0))
        print(f"Fetching {symbol} {INTERVAL} perpetual candles and funding...", flush=True)
        klines = fetch_klines(symbol, start_ms, end_ms)
        funding = fetch_funding(symbol, start_ms, end_ms)
        validate_market_data(symbol, contract, klines, funding, end_ms)
        frame = build_frame(klines)
        market_frames[symbol] = (frame, funding)
        manifest["symbols"][symbol] = {
            "contract_type": contract.get("contractType"),
            "quote_asset": contract.get("quoteAsset"),
            "status": contract.get("status"),
            "onboard_date_ms": contract.get("onboardDate"),
            "requested_start_ms": start_ms,
            "first_bar_utc": frame.index[0].isoformat(),
            "last_bar_utc": frame.index[-1].isoformat(),
            "bars": len(frame),
            "funding_events": len(funding),
            "first_funding_utc": datetime.fromtimestamp(int(funding[0]["fundingTime"]) / 1000, UTC).isoformat(),
            "last_funding_utc": datetime.fromtimestamp(int(funding[-1]["fundingTime"]) / 1000, UTC).isoformat(),
        }

    all_results: dict[str, dict[str, dict[str, list[Trade]]]] = {}
    for scenario, slip in COST_SCENARIOS.items():
        scenario_results: dict[str, dict[str, list[Trade]]] = {}
        for symbol, (frame, funding) in market_frames.items():
            symbol_results: dict[str, list[Trade]] = {}
            for strategy in STRATEGIES:
                symbol_results[strategy] = []
                for side in (1, -1):
                    symbol_results[strategy].extend(run_backtest(symbol, frame, funding, strategy, side, slip))
                symbol_results[strategy].sort(key=lambda trade: trade.entry_time)
            scenario_results[symbol] = symbol_results
        all_results[scenario] = scenario_results
    write_report(Path(args.output), manifest, all_results)
    print(f"Research complete: {Path(args.output) / 'report.md'}", flush=True)
    for scenario, groups in all_results.items():
        for strategy in STRATEGIES:
            subset = [trade for by_strategy in groups.values() for trade in period_trades(by_strategy.get(strategy, []), "2024-01-01")]
            m = metrics(subset)
            print(f"{scenario} {strategy}: n={m['n']} PF={safe_number(m['pf'])} meanR={safe_number(m['mean_net_r'])} mean_hold={safe_number(m['mean_hold_min'])}m", flush=True)


if __name__ == "__main__":
    main()
