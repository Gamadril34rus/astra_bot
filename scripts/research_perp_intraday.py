#!/usr/bin/env python3
"""Research-only 30m intraday strategies for verified USDⓈ-M perpetual futures.

Signals use closed 30m bars and completed 1h context. Entries are next-bar opens.
The runner models taker fees, adverse slippage, realized funding, conservative
same-bar stop-first handling, a 30m no-progress exit, and a hard 9h time stop.
It reads public BingX market-data endpoints only; it never places or manages orders.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_URL = "https://open-api.bingx.com"
API_MIN_INTERVAL_S = 1.05
_last_request_monotonic = 0.0
KLINES_ENDPOINT = "/openApi/swap/v3/quote/klines"
DEPTH_ENDPOINT = "/openApi/swap/v2/quote/depth"
CONTRACTS_ENDPOINT = "/openApi/swap/v2/quote/contracts"
FUNDING_ENDPOINT = "/openApi/swap/v2/quote/fundingRate"
MARK_KLINES_ENDPOINT = "/openApi/swap/v1/market/markPriceKlines"
PAGE_KLINES = 1000
PAGE_FUNDING = 1000
INTERVAL = "30m"
INTERVAL_MS = 30 * 60 * 1000
FEE_PER_SIDE = 0.0005
BASE_SLIPPAGE_PER_SIDE = 0.0010
STRESS_SLIPPAGE_PER_SIDE = 0.0015
MAX_HOLD_BARS = 18  # 18 x 30m = 9h
NO_PROGRESS_BARS = 1
NO_PROGRESS_MFE_R = 0.35
MAX_COST_TO_R = 0.40

# Each market history starts at the later of 2021-01-01 UTC and BingX launchTime.
DATA_START_FLOOR = "2021-01-01"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")

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



def _average_sweep_price(levels: list[list[Any]], target_quantity: float) -> float | None:
    """Return volume-weighted average execution price or None if book is thin."""
    remaining = target_quantity
    quote_spent = 0.0
    quantity_filled = 0.0
    for price_raw, quantity_raw in levels:
        price, available = float(price_raw), float(quantity_raw)
        if not math.isfinite(price) or not math.isfinite(available) or price <= 0 or available <= 0:
            continue
        take = min(remaining, available)
        quote_spent += take * price
        quantity_filled += take
        remaining -= take
        if remaining <= max(1e-12, target_quantity * 1e-10):
            return quote_spent / quantity_filled
    return None


def calculate_orderbook_costs(
    orderbook: dict[str, Any], notionals_usdt: tuple[float, ...] = (1_000.0, 5_000.0, 10_000.0)
) -> dict[str, Any]:
    """Estimate taker round-trip spread+depth cost from one live order-book snapshot.

    This is a current liquidity diagnostic, not a historical fill reconstruction.
    """
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    if not bids or not asks:
        raise ValueError("BingX order book has no bid or ask levels")
    best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
    if best_bid <= 0 or best_ask <= best_bid:
        raise ValueError(f"invalid top of book: bid={best_bid} ask={best_ask}")
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * 10_000
    scenarios: dict[str, Any] = {}
    for notional in notionals_usdt:
        target_quantity = notional / mid
        average_buy = _average_sweep_price(asks, target_quantity)
        average_sell = _average_sweep_price(bids, target_quantity)
        if average_buy is None or average_sell is None:
            scenarios[f"{int(notional)}"] = {
                "complete": False,
                "available_bid_notional_usdt": round(sum(float(p) * float(q) for p, q in bids), 2),
                "available_ask_notional_usdt": round(sum(float(p) * float(q) for p, q in asks), 2),
            }
            continue
        round_trip_book_bps = (average_buy - average_sell) / mid * 10_000
        round_trip_fee_bps = 2 * FEE_PER_SIDE * 10_000
        total_round_trip_bps = round_trip_book_bps + round_trip_fee_bps
        scenarios[f"{int(notional)}"] = {
            "complete": True,
            "average_buy_price": average_buy,
            "average_sell_price": average_sell,
            "round_trip_book_cost_bps": round(round_trip_book_bps, 3),
            "taker_fee_round_trip_bps": round(round_trip_fee_bps, 3),
            "total_round_trip_cost_bps": round(total_round_trip_bps, 3),
        }
    return {
        "timestamp_ms": int(orderbook.get("T") or 0),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "top_spread_bps": round(spread_bps, 3),
        "taker_fee_per_side_bps": FEE_PER_SIDE * 10_000,
        "notional_scenarios": scenarios,
        "note": "current snapshot only; not a substitute for historical BBO/L2 or actual fills",
    }


def fetch_orderbook_execution_costs(symbol: str) -> dict[str, Any]:
    rows = request_json(DEPTH_ENDPOINT, {"symbol": symbol, "limit": 100})
    if not rows or not isinstance(rows[0], dict):
        raise RuntimeError(f"{symbol}: invalid BingX order-book response")
    return calculate_orderbook_costs(rows[0])
def unwrap_data(payload: Any, path: str) -> list[Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path}: invalid BingX response object")
    if str(payload.get("code", 0)) not in {"0", "None"}:
        raise RuntimeError(f"{path}: BingX code={payload.get('code')} msg={payload.get('msg')}")
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("klines", "fundingRates", "data"):
            if key in data:
                data = data[key]
                break
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise RuntimeError(f"{path}: unexpected data payload type {type(data).__name__}")


def request_json(path: str, params: dict[str, Any], retries: int = 5) -> list[Any]:
    global _last_request_monotonic
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{path}?{query}"
    last_error: Exception | None = None
    for attempt in range(retries):
        elapsed = time.monotonic() - _last_request_monotonic
        if elapsed < API_MIN_INTERVAL_S:
            time.sleep(API_MIN_INTERVAL_S - elapsed)
        request = urllib.request.Request(url, headers={"User-Agent": "astra-bot-bingx-perp-research/1.0"})
        _last_request_monotonic = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and str(payload.get("code")) == "100410":
                last_error = RuntimeError(
                    f"BingX transient code=100410 msg={payload.get('msg')}"
                )
                if attempt >= 3:
                    raise RuntimeError(
                        f"{path}: BingX returned code=100410 after 4 attempts"
                    ) from last_error
                delay = (5, 30, 60)[attempt]
                print(
                    f"{path}: BingX code=100410; retry {attempt + 2}/4 in {delay}s",
                    flush=True,
                )
                time.sleep(delay)
                continue
            return unwrap_data(payload, path)
        except urllib.error.HTTPError as exc:
            if exc.code not in (418, 429, 500, 502, 503, 504):
                raise RuntimeError(f"{path} returned HTTP {exc.code}: {exc.read()[:300]!r}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
        time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"request failed after {retries} attempts: {path}: {last_error}")


def get_perpetual_contracts() -> dict[str, dict[str, Any]]:
    contracts = request_json(CONTRACTS_ENDPOINT, {})
    result = {}
    for row in contracts:
        symbol = str(row.get("symbol", "")).upper()
        if (
            symbol.endswith("-USDT")
            and str(row.get("currency", "")).upper() == "USDT"
            and str(row.get("status", "")) == "1"
            and str(row.get("apiStateOpen", "true")).lower() == "true"
        ):
            result[symbol] = row
    return result


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list[list[Any]]:
    """Fetch bounded 30m candles and normalize BingX array/object payloads.

    Some BingX responses expose only base-asset ``volume`` in object rows,
    without quote turnover or taker-buy fields. In that case quote turnover
    is estimated as base volume times close; aggressor fields remain NaN.
    """
    rows_by_time: dict[int, list[Any]] = {}
    cursor = ((start_ms + INTERVAL_MS - 1) // INTERVAL_MS) * INTERVAL_MS
    last_closed_open = (end_ms // INTERVAL_MS) * INTERVAL_MS - INTERVAL_MS
    while cursor <= last_closed_open:
        page_last_open = min(
            cursor + (PAGE_KLINES - 1) * INTERVAL_MS, last_closed_open
        )
        page = request_json(
            KLINES_ENDPOINT,
            {
                "symbol": symbol,
                "interval": INTERVAL,
                "startTime": cursor,
                "endTime": page_last_open + INTERVAL_MS - 1,
                "limit": PAGE_KLINES,
            },
        )
        normalized_page: list[list[Any]] = []
        for row in page:
            if isinstance(row, dict):
                open_p = float(row["open"])
                high = float(row["high"])
                low = float(row["low"])
                close = float(row["close"])
                volume = float(row.get("volume") or 0.0)
                quote_raw = row.get("quoteVolume", row.get("quoteAssetVolume"))
                if quote_raw is None or float(quote_raw or 0.0) <= 0.0:
                    quote_volume = volume * close
                    quote_source = "estimated_base_volume_x_close"
                else:
                    quote_volume = float(quote_raw)
                    quote_source = "reported_quote_volume"
                taker_base = row.get("takerBuyBaseVolume")
                taker_quote = row.get("takerBuyQuoteVolume")
                normalized_page.append([
                    row.get("time", row.get("openTime")), open_p, high, low, close,
                    volume, row.get("closeTime", 0), quote_volume,
                    row.get("n", row.get("trades", 0)),
                    float(taker_base) if taker_base is not None else math.nan,
                    float(taker_quote) if taker_quote is not None else math.nan,
                    quote_source,
                ])
            else:
                values = list(row)
                if len(values) < 6:
                    raise ValueError(f"{symbol}: malformed BingX kline array: {row!r}")
                open_p, high, low, close, volume = map(float, values[1:6])
                quote_raw = values[7] if len(values) > 7 else None
                if quote_raw is None or float(quote_raw or 0.0) <= 0.0:
                    quote_volume = volume * close
                    quote_source = "estimated_base_volume_x_close"
                else:
                    quote_volume = float(quote_raw)
                    quote_source = "reported_quote_volume"
                normalized_page.append([
                    values[0], open_p, high, low, close, volume,
                    values[6] if len(values) > 6 else 0, quote_volume,
                    values[8] if len(values) > 8 else 0,
                    float(values[9]) if len(values) > 9 and values[9] is not None else math.nan,
                    float(values[10]) if len(values) > 10 and values[10] is not None else math.nan,
                    quote_source,
                ])
        normalized_page.sort(key=lambda item: int(item[0]))
        actual_times = [
            int(row[0]) for row in normalized_page
            if cursor <= int(row[0]) <= page_last_open
            and int(row[0]) + INTERVAL_MS <= end_ms
        ]
        if not actual_times:
            if rows_by_time:
                raise RuntimeError(
                    f"BingX returned an empty candle window after history began "
                    f"for {symbol}: {cursor}..{page_last_open}"
                )
            cursor = page_last_open + INTERVAL_MS
            continue
        expected_start = cursor if rows_by_time else actual_times[0]
        expected_times = list(range(expected_start, page_last_open + 1, INTERVAL_MS))
        if actual_times != expected_times:
            raise RuntimeError(
                f"incomplete BingX candle window for {symbol}: "
                f"start={cursor} end={page_last_open} "
                f"expected={len(expected_times)} actual={len(actual_times)}"
            )
        for row in normalized_page:
            open_time = int(row[0])
            if open_time in actual_times:
                if open_time in rows_by_time and rows_by_time[open_time] != row:
                    raise RuntimeError(
                        f"conflicting BingX candle duplicate for {symbol} at {open_time}"
                    )
                rows_by_time[open_time] = row
        cursor = page_last_open + INTERVAL_MS
    if not rows_by_time:
        raise RuntimeError(f"BingX returned no retained closed 30m candles for {symbol}")
    return [rows_by_time[key] for key in sorted(rows_by_time)]
def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    """Fetch funding in bounded 30-day windows; never stop at a short page."""
    rows: dict[int, dict[str, Any]] = {}
    chunk_ms = 30 * 24 * 60 * 60 * 1000
    cursor = start_ms
    while cursor <= end_ms:
        chunk_end = min(end_ms, cursor + chunk_ms - 1)
        page = request_json(
            FUNDING_ENDPOINT,
            {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": chunk_end,
                "limit": PAGE_FUNDING,
            },
        )
        accepted = 0
        for row in page:
            if not isinstance(row, dict):
                continue
            funding_time = int(row.get("fundingTime", row.get("time", 0)) or 0)
            rate = row.get("fundingRate")
            if not funding_time or rate is None or not cursor <= funding_time <= chunk_end:
                continue
            normalized = {
                "fundingTime": funding_time,
                "fundingRate": str(rate),
                "markPrice": row.get("markPrice"),
            }
            previous = rows.get(funding_time)
            if previous is not None and previous["fundingRate"] != normalized["fundingRate"]:
                raise RuntimeError(
                    f"conflicting BingX funding rates for {symbol} at {funding_time}"
                )
            rows[funding_time] = normalized
            accepted += 1
        print(
            f"{symbol}: funding window {datetime.fromtimestamp(cursor / 1000, UTC).isoformat()}.."
            f"{datetime.fromtimestamp(chunk_end / 1000, UTC).isoformat()} "
            f"rows={len(page)} accepted={accepted}",
            flush=True,
        )
        cursor = chunk_end + 1
    ordered = [rows[key] for key in sorted(rows)]
    if ordered:
        print(
            f"{symbol}: funding events={len(ordered)} "
            f"coverage={datetime.fromtimestamp(ordered[0]['fundingTime'] / 1000, UTC).isoformat()}.."
            f"{datetime.fromtimestamp(ordered[-1]['fundingTime'] / 1000, UTC).isoformat()}",
            flush=True,
        )
    return ordered


def latest_contiguous_funding_segment(
    funding: list[dict[str, Any]], max_gap_hours: float = 12.0
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the latest uninterrupted funding segment and return excluded rows."""
    ordered = sorted(funding, key=lambda row: int(row["fundingTime"]))
    if not ordered:
        return [], []
    max_gap_ms = max_gap_hours * 60 * 60 * 1000
    gap_indices = [
        index
        for index, (left, right) in enumerate(pairwise(ordered))
        if int(right["fundingTime"]) - int(left["fundingTime"]) > max_gap_ms
    ]
    start_index = gap_indices[-1] + 1 if gap_indices else 0
    return ordered[start_index:], ordered[:start_index]
def fetch_mark_prices(symbol: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """Fetch complete bounded 4h mark-price windows for funding settlements."""
    rows: dict[int, float] = {}
    interval_ms = 4 * 60 * 60 * 1000
    cursor = ((start_ms + interval_ms - 1) // interval_ms) * interval_ms
    last_open = (end_ms // interval_ms) * interval_ms
    while cursor <= last_open:
        page_last = min(cursor + (PAGE_KLINES - 1) * interval_ms, last_open)
        page = request_json(
            MARK_KLINES_ENDPOINT,
            {
                "symbol": symbol,
                "interval": "4h",
                "startTime": cursor,
                "endTime": page_last + interval_ms - 1,
                "limit": PAGE_KLINES,
            },
        )
        parsed: list[tuple[int, float]] = []
        for row in page:
            try:
                if isinstance(row, dict):
                    timestamp = int(row.get("time", row.get("openTime", 0)))
                    price = float(row.get("open", 0))
                else:
                    timestamp, price = int(row[0]), float(row[1])
                if cursor <= timestamp <= page_last and math.isfinite(price) and price > 0:
                    parsed.append((timestamp, price))
            except (ValueError, TypeError, IndexError):
                continue
        parsed.sort()
        actual_times = [timestamp for timestamp, _ in parsed]
        if not actual_times:
            if rows:
                raise RuntimeError(
                    f"BingX mark-price history gap for {symbol}: {cursor}..{page_last}"
                )
            cursor = page_last + interval_ms
            continue
        expected_start = cursor if rows else actual_times[0]
        expected_times = list(range(expected_start, page_last + 1, interval_ms))
        if actual_times != expected_times:
            raise RuntimeError(
                f"incomplete BingX mark-price window for {symbol}: "
                f"start={cursor} end={page_last} "
                f"expected={len(expected_times)} actual={len(actual_times)}"
            )
        for timestamp, price in parsed:
            if timestamp in rows and rows[timestamp] != price:
                raise RuntimeError(
                    f"conflicting BingX mark-price duplicate for {symbol} at {timestamp}"
                )
            rows[timestamp] = price
        cursor = page_last + interval_ms
    return rows
def validate_market_data(
    symbol: str,
    contract: dict[str, Any],
    klines: list[list[Any]],
    funding: list[dict[str, Any]],
    end_ms: int,
) -> None:
    if not symbol.endswith("-USDT") or str(contract.get("currency", "")).upper() != "USDT":
        raise ValueError(f"{symbol}: instrument is not a USDT-margined perpetual")
    if str(contract.get("status", "")) != "1":
        raise ValueError(f"{symbol}: contract is not currently active on BingX")
    launch_ms = int(contract.get("launchTime", 0) or 0)
    if not klines or len(klines) < 500:
        raise ValueError(f"{symbol}: insufficient 30m kline history ({len(klines)})")
    opens = [int(row[0]) for row in klines]
    if any(value % INTERVAL_MS for value in opens):
        raise ValueError(f"{symbol}: kline timestamps are not aligned to 30m UTC boundaries")
    if len(opens) != len(set(opens)):
        raise ValueError(f"{symbol}: duplicate kline open times")
    if opens != sorted(opens):
        raise ValueError(f"{symbol}: klines are not ordered")
    if any(right - left != INTERVAL_MS for left, right in pairwise(opens)):
        raise ValueError(f"{symbol}: missing or irregular 30m intervals")
    if opens[-1] + INTERVAL_MS > end_ms:
        raise ValueError(f"{symbol}: latest bar is not fully closed")
    if launch_ms and opens[0] < launch_ms:
        raise ValueError(f"{symbol}: candle history predates BingX launchTime")
    for row in klines:
        open_p, high, low, close, volume = map(float, row[1:6])
        if min(open_p, high, low, close) <= 0 or volume < 0:
            raise ValueError(f"{symbol}: non-positive OHLC or negative volume")
        if high < max(open_p, close, low) or low > min(open_p, close, high):
            raise ValueError(f"{symbol}: invalid OHLC geometry")
    if not funding:
        raise ValueError(f"{symbol}: no historical funding returned")
    times = [int(row["fundingTime"]) for row in funding]
    if times != sorted(set(times)):
        raise ValueError(f"{symbol}: duplicate or unordered funding events")
    if any(not math.isfinite(float(row["fundingRate"])) for row in funding):
        raise ValueError(f"{symbol}: non-finite funding rate")
    if any(float(row.get("markPrice", 0) or 0) <= 0 for row in funding):
        raise ValueError(f"{symbol}: funding event missing its mark price")
    if times[0] > opens[0] + 24 * 60 * 60 * 1000:
        raise ValueError(f"{symbol}: funding coverage starts more than 24h after candles")
    if times[-1] < opens[-1] - 24 * 60 * 60 * 1000:
        raise ValueError(f"{symbol}: funding coverage ends more than 24h before candles")
    gaps = [(right - left, left, right) for left, right in pairwise(times)]
    largest_gap = max(gaps, default=(0, 0, 0))
    if largest_gap[0] > 12 * 60 * 60 * 1000:
        left_dt = datetime.fromtimestamp(largest_gap[1] / 1000, UTC).isoformat()
        right_dt = datetime.fromtimestamp(largest_gap[2] / 1000, UTC).isoformat()
        raise ValueError(
            f"{symbol}: funding history gap {largest_gap[0] / 3_600_000:.1f}h "
            f"between {left_dt} and {right_dt}; total events={len(times)}"
        )

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
        "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume",
        "quote_volume_source",
    ]
    normalized_rows = [
        list(row) if len(row) >= len(columns)
        else [*list(row), "unknown"]
        for row in klines
    ]
    frame = pd.DataFrame(normalized_rows, columns=columns)
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
    candle_range = (frame["high"] - frame["low"]).replace(0, np.nan)
    frame["close_location"] = (frame["close"] - frame["low"]) / candle_range
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




def persist_source_snapshot(
    output: Path, symbol: str, frame: pd.DataFrame, funding: list[dict[str, Any]]
) -> dict[str, str]:
    """Persist normalized, verified inputs for independent replay and audit."""
    source_dir = output / "source_data"
    source_dir.mkdir(parents=True, exist_ok=True)
    stem = symbol.replace("-", "")
    candle_path = source_dir / f"{stem}_30m.csv"
    funding_path = source_dir / f"funding_{stem}.csv"
    frame.to_csv(candle_path, index=True, index_label="bar_open_utc")
    pd.DataFrame(funding).to_csv(funding_path, index=False)
    def digest(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    return {
        "candles_file": str(candle_path.relative_to(output)),
        "candles_sha256": digest(candle_path),
        "funding_file": str(funding_path.relative_to(output)),
        "funding_sha256": digest(funding_path),
    }
def signal_feature_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    """Count valid features and each independent gate before combining signals."""
    htf_valid = frame[["htf_close", "htf_ema20", "htf_ema50", "htf_adx"]].notna().all(axis=1)
    uptrend = (frame["htf_ema20"] > frame["htf_ema50"]) & (frame["htf_close"] > frame["htf_ema20"])
    downtrend = (frame["htf_ema20"] < frame["htf_ema50"]) & (frame["htf_close"] < frame["htf_ema20"])
    neutral = ((frame["htf_ema20"] / frame["htf_ema50"] - 1).abs() <= 0.003) & (frame["htf_adx"] < 22)
    touch_long = (
        (frame["low"].shift(1) <= frame["ema20"].shift(1) + 0.15 * frame["atr"].shift(1))
        & (frame["low"].shift(1) >= frame["ema20"].shift(1) - 0.60 * frame["atr"].shift(1))
    )
    touch_short = (
        (frame["high"].shift(1) >= frame["ema20"].shift(1) - 0.15 * frame["atr"].shift(1))
        & (frame["high"].shift(1) <= frame["ema20"].shift(1) + 0.60 * frame["atr"].shift(1))
    )
    masks = signal_masks(frame)
    quote_sources = (
        frame["quote_volume_source"]
        if "quote_volume_source" in frame
        else pd.Series("unknown", index=frame.index)
    )
    close_location = (
        frame["close_location"]
        if "close_location" in frame
        else pd.Series(np.nan, index=frame.index)
    )
    counts = {
        "rows": len(frame),
        "valid_htf_rows": int(htf_valid.sum()),
        "valid_atr_rows": int(frame["atr"].notna().sum()),
        "valid_rsi_rows": int(frame["rsi"].notna().sum()),
        "valid_volume_ratio_rows": int(frame["volume_ratio"].notna().sum()),
        "positive_quote_volume_rows": int((frame["quote_volume"] > 0).sum()),
        "valid_taker_buy_ratio_rows": int(frame["taker_buy_ratio"].notna().sum()),
        "estimated_quote_volume_rows": int((quote_sources == "estimated_base_volume_x_close").sum()),
        "valid_close_location_rows": int(close_location.notna().sum()),
        "uptrend_rows": int(uptrend.fillna(False).sum()),
        "downtrend_rows": int(downtrend.fillna(False).sum()),
        "neutral_rows": int(neutral.fillna(False).sum()),
        "touch_long_rows": int(touch_long.fillna(False).sum()),
        "touch_short_rows": int(touch_short.fillna(False).sum()),
        "signals": {
            f"{name}:{'LONG' if side > 0 else 'SHORT'}": int(mask.sum())
            for (name, side), mask in masks.items()
        },
        "volume_ratio_quantiles": {
            str(q): float(value) if math.isfinite(float(value)) else None
            for q, value in frame["volume_ratio"].quantile([0.1, 0.5, 0.9]).items()
        },
        "taker_buy_ratio_quantiles": {
            str(q): float(value) if math.isfinite(float(value)) else None
            for q, value in frame["taker_buy_ratio"].quantile([0.1, 0.5, 0.9]).items()
        },
    }
    return counts
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
        & (frame["close_location"] >= 0.65) & (frame["close"] > frame["open"])
        & frame["rsi"].between(54, 75)
    )
    expansion_short = (
        downtrend & (frame["close"] < prior_low) & (frame["volume_ratio"] >= 1.35)
        & (frame["close_location"] <= 0.35) & (frame["close"] < frame["open"])
        & frame["rsi"].between(25, 46)
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
    diagnostics: dict[str, int] | None = None,
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
    if diagnostics is not None:
        diagnostics.setdefault("raw_signals", int(signals[:-MAX_HOLD_BARS].sum()))
        diagnostics.setdefault("tail_signals_skipped", int(signals[-MAX_HOLD_BARS:].sum()))
        diagnostics.setdefault("cost_rejected", 0)
        diagnostics.setdefault("target_rejected", 0)
        diagnostics.setdefault("completed_trades", 0)
    signal_idx = 0
    # Do not start positions without a full 9h observation window; otherwise
    # the end of the data set would masquerade as a time-stop exit.
    while signal_idx < len(frame) - MAX_HOLD_BARS:
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
            if diagnostics is not None:
                diagnostics["cost_rejected"] += 1
            signal_idx += 1
            continue
        stop = entry_raw - side * stop_distance
        if config["target_mode"] == "band_mid":
            take = float(bb_mid[signal_idx])
            target_distance = side * (take - entry_raw)
            if not math.isfinite(take) or target_distance < 0.85 * stop_distance:
                if diagnostics is not None:
                    diagnostics["target_rejected"] += 1
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
        if diagnostics is not None:
            diagnostics["completed_trades"] += 1
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
        "Research only. This is a BingX USDT-M perpetual candle/funding replay, not a full historical order-book fill simulation and not a live-performance claim.", "",
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
        "| Cost case | Period | Strategy | Trades | PF | Mean net R | Win % | Mean hold min | Median hold min | Trade-sequence DD proxy % (0.5% R) |",
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
    diagnostics = manifest.get("strategy_diagnostics", {})
    lines += ["", "## Signal eligibility diagnostics", "", "| Symbol | Strategy | Side | Raw signals | Cost rejected | Target rejected | Completed |", "|---|---|---|---:|---:|---:|---:|"]
    for diagnostic_key, values in sorted(diagnostics.items()):
        symbol, strategy, side = diagnostic_key.split(":", 2)
        lines.append(" | ".join(["| " + symbol, strategy, side, str(values.get("raw_signals", 0)), str(values.get("cost_rejected", 0)), str(values.get("target_rejected", 0)), str(values.get("completed_trades", 0))]) + " |")
    if not diagnostics:
        lines.append("| — | — | — | 0 | 0 | 0 | 0 |")
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
    lines += ["", "## Promotion rule", "", "No candidate is promoted by this report. A family may proceed to a separate BingX-specific validation only if it has at least 100 OOS closed trades pooled, positive net expectancy and PF >= 1.30 in both base and stress cost cases, positive results in at least 3 of the 5 symbols where history permits, both directions separately reviewed, no single symbol accounting for more than 50% of net profit, mean holding <= 90 minutes, median holding <= 60 minutes, maximum holding <= 9h, and acceptable rolling-year stability. A positive BingX candle/funding replay is only a candidate for VST validation with observed bid/ask and exchange fills, not permission for live execution.", "", "## Limitations", "", "- BingX OHLC history cannot reconstruct the historical bid/ask book, queue priority, actual fill latency or market impact. Slippage remains an explicit sensitivity assumption.", "- OHLC bars cannot reveal the within-bar path. Stop-first is intentionally adverse but does not recover actual execution prices.", "- Funding uses BingX historical funding-rate events and aligned BingX 4h mark-price candle opens; this mark-price alignment is a historical proxy and should be checked against settlement records when available.", "- The 0.5%-per-R drawdown is a sequential trade-series proxy, not account or portfolio drawdown. Positions across symbols can overlap; concurrent exposure is not mark-to-market aggregated, and margin, liquidation, wallet transfers and account-level constraints are not simulated.", "- No parameter search is performed; thresholds are frozen before the run. A negative result is retained, not tuned away.", "", "*Generated by scripts/research_perp_intraday.py. Research only; no live or VST execution wiring.*", ""]
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
        "venue": "BingX USDT-M Perpetual",
        "contract_type": "USDT-M perpetual swap",
        "quote_asset": "USDT",
        "interval": INTERVAL,
        "data_cutoff_utc": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        "source_endpoints": [CONTRACTS_ENDPOINT, KLINES_ENDPOINT, FUNDING_ENDPOINT, MARK_KLINES_ENDPOINT],
        "volume_feature_provenance": {
            "quote_volume": "reported by BingX when available; otherwise estimated as base-asset candle volume multiplied by close for relative activity only",
            "directional_confirmation": "close location within candle range; this is price-action evidence, not taker order flow",
            "taker_buy_ratio": "retained as diagnostic when supplied; not used as a signal gate because the observed object payload omits aggressor-volume fields",
        },
        "cost_model": {"fee_per_side": FEE_PER_SIDE, "base_slippage_per_side": BASE_SLIPPAGE_PER_SIDE, "stress_slippage_per_side": STRESS_SLIPPAGE_PER_SIDE, "max_cost_to_r": MAX_COST_TO_R},
        "holding_model": {"no_progress_bars": NO_PROGRESS_BARS, "no_progress_mfe_r": NO_PROGRESS_MFE_R, "max_hold_bars": MAX_HOLD_BARS},
        "symbols": {},
    }
    market_frames: dict[str, tuple[pd.DataFrame, list[dict[str, Any]]]] = {}
    for symbol in SYMBOLS:
        contract = contracts.get(symbol)
        if contract is None:
            raise RuntimeError(f"{symbol}: no active BingX USDT-M perpetual contract found")
        start_ms = max(utc_ms(DATA_START_FLOOR), int(contract.get("launchTime", 0) or 0))
        print(f"Fetching BingX {symbol} {INTERVAL} perpetual candles, funding and mark prices...", flush=True)
        raw_funding = fetch_funding(symbol, start_ms, end_ms)
        funding, excluded_funding = latest_contiguous_funding_segment(raw_funding)
        if not funding:
            raise ValueError(f"{symbol}: no contiguous BingX funding segment")
        funding_start_ms = int(funding[0]["fundingTime"])
        # Earlier candles cannot be costed without missing funding events.
        # Fetch only the latest continuous funding-covered interval.
        klines = fetch_klines(symbol, funding_start_ms, end_ms)
        missing_mark_events = [
            event for event in funding if float(event.get("markPrice") or 0) <= 0
        ]
        mark_prices = (
            fetch_mark_prices(
                symbol, min(int(event["fundingTime"]) for event in missing_mark_events), end_ms
            )
            if missing_mark_events else {}
        )
        for event in missing_mark_events:
            event["markPrice"] = mark_prices.get(int(event["fundingTime"]))
        klines = [row for row in klines if int(row[0]) >= funding_start_ms]
        if not klines:
            raise ValueError(f"{symbol}: no candles inside latest funding segment")
        validate_market_data(symbol, contract, klines, funding, end_ms)
        frame = build_frame(klines)
        source_snapshot = persist_source_snapshot(Path(args.output), symbol, frame, funding)
        market_frames[symbol] = (frame, funding)
        manifest.setdefault("feature_diagnostics", {})[symbol] = signal_feature_diagnostics(frame)
        manifest["symbols"][symbol] = {
            "symbol": contract.get("symbol"),
            "settlement_currency": contract.get("currency"),
            "status": contract.get("status"),
            "launch_time_ms": contract.get("launchTime"),
            "requested_start_ms": start_ms,
            "first_bar_utc": frame.index[0].isoformat(),
            "last_bar_utc": frame.index[-1].isoformat(),
            "bars": len(frame),
            "source_snapshot": source_snapshot,
            "funding_events": len(funding),
            "funding_events_excluded_before_latest_contiguous_segment": len(excluded_funding),
            "excluded_funding_first_utc": datetime.fromtimestamp(int(excluded_funding[0]["fundingTime"]) / 1000, UTC).isoformat() if excluded_funding else None,
            "excluded_funding_last_utc": datetime.fromtimestamp(int(excluded_funding[-1]["fundingTime"]) / 1000, UTC).isoformat() if excluded_funding else None,
            "first_funding_utc": datetime.fromtimestamp(int(funding[0]["fundingTime"]) / 1000, UTC).isoformat(),
            "last_funding_utc": datetime.fromtimestamp(int(funding[-1]["fundingTime"]) / 1000, UTC).isoformat(),
        }

    live_execution_costs: dict[str, Any] = {}
    for symbol in SYMBOLS:
        try:
            live_execution_costs[symbol] = fetch_orderbook_execution_costs(symbol)
        except Exception as exc:
            live_execution_costs[symbol] = {"status": "unavailable", "error": str(exc)}
    manifest["current_orderbook_cost_snapshot"] = live_execution_costs

    all_results: dict[str, dict[str, dict[str, list[Trade]]]] = {}
    diagnostics_report: dict[str, dict[str, dict[str, int]]] = {}
    for scenario, slip in COST_SCENARIOS.items():
        scenario_results: dict[str, dict[str, list[Trade]]] = {}
        for symbol, (frame, funding) in market_frames.items():
            symbol_results: dict[str, list[Trade]] = {}
            for strategy in STRATEGIES:
                symbol_results[strategy] = []
                for side in (1, -1):
                    side_diagnostics: dict[str, int] = {}
                    symbol_results[strategy].extend(
                        run_backtest(
                            symbol, frame, funding, strategy, side, slip,
                            side_diagnostics if scenario == "base_30bps_rt" else None,
                        )
                    )
                    if scenario == "base_30bps_rt":
                        diagnostics_report[f"{symbol}:{strategy}:{'LONG' if side > 0 else 'SHORT'}"] = side_diagnostics
                symbol_results[strategy].sort(key=lambda trade: trade.entry_time)
            scenario_results[symbol] = symbol_results
        all_results[scenario] = scenario_results
    manifest["strategy_diagnostics"] = diagnostics_report
    write_report(Path(args.output), manifest, all_results)
    print(f"Research complete: {Path(args.output) / 'report.md'}", flush=True)
    for scenario, groups in all_results.items():
        for strategy in STRATEGIES:
            subset = [trade for by_strategy in groups.values() for trade in period_trades(by_strategy.get(strategy, []), "2024-01-01")]
            m = metrics(subset)
            print(f"{scenario} {strategy}: n={m['n']} PF={safe_number(m['pf'])} meanR={safe_number(m['mean_net_r'])} mean_hold={safe_number(m['mean_hold_min'])}m", flush=True)


if __name__ == "__main__":
    main()
