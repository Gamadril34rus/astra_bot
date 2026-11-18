"""Historical BingX perpetual funding collection and trade-level accounting."""
from __future__ import annotations

import time
from collections import defaultdict

import requests


FUNDING_URL = "https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate"


def fetch_funding_rates(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    request_get=requests.get,
    sleep=time.sleep,
) -> list[dict]:
    """Fetch and validate all funding settlements in [start_ms, end_ms]."""
    if start_ms > end_ms:
        raise ValueError("start_ms must be <= end_ms")
    cursor = int(end_ms)
    rows: dict[int, dict] = {}
    while cursor >= start_ms:
        response = request_get(
            FUNDING_URL,
            params={
                "symbol": symbol,
                "startTime": int(start_ms),
                "endTime": cursor,
                "limit": 1000,
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"{symbol} funding HTTP {response.status_code}")
        body = response.json()
        if str(body.get("code")) not in ("0", "0.0"):
            raise RuntimeError(
                f"{symbol} funding API code={body.get('code')} msg={body.get('msg')}"
            )
        batch = body.get("data")
        if not isinstance(batch, list):
            raise RuntimeError(f"{symbol} funding response missing data list")
        if not batch:
            break
        for item in batch:
            ts = int(item["fundingTime"])
            if start_ms <= ts <= end_ms:
                rows[ts] = {
                    "symbol": symbol,
                    "fundingTime": ts,
                    "fundingRate": float(item["fundingRate"]),
                    "markPrice": float(item["markPrice"]),
                }
        oldest = min(int(item["fundingTime"]) for item in batch)
        if oldest <= start_ms or oldest >= cursor:
            break
        cursor = oldest - 1
        sleep(1.05)
    return [rows[ts] for ts in sorted(rows)]


def apply_funding_to_trades(
    trades: list[dict],
    funding_by_symbol: dict[str, list[dict]],
) -> list[dict]:
    """Return copied trades with settlement cashflows included in net_pnl_pct.

    Positive funding means longs pay and shorts receive. Funding is calculated
    on the settlement mark notional, scaled to the trade's entry notional.
    A settlement applies only while the position is open: entry <= time < exit.
    """
    by_symbol = defaultdict(list)
    for symbol, events in funding_by_symbol.items():
        by_symbol[symbol].extend(events)
    for events in by_symbol.values():
        events.sort(key=lambda item: int(item["fundingTime"]))

    adjusted = []
    for original in trades:
        trade = dict(original)
        paid_pct = 0.0
        count = 0
        entry = int(trade["entry_ts"])
        exit_ = int(trade["exit_ts"])
        entry_price = float(trade["entry_fill"])
        if entry_price <= 0:
            raise ValueError("entry_fill must be positive")
        side_sign = 1.0 if trade["side"] == "long" else -1.0
        for event in by_symbol.get(trade["symbol"], []):
            ts = int(event["fundingTime"])
            cutoff = int(trade.get("funding_cutoff_ts", exit_))
            if entry < ts < cutoff:
                mark = float(event["markPrice"])
                rate = float(event["fundingRate"])
                if mark <= 0:
                    raise ValueError("funding markPrice must be positive")
                paid_pct += side_sign * rate * mark / entry_price
                count += 1
        trade["funding_pnl_pct"] = -paid_pct
        trade["funding_events"] = count
        trade["net_pnl_pct"] = float(trade["net_pnl_pct"]) - paid_pct
        adjusted.append(trade)
    return adjusted
