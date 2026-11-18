"""Backward-as-of Binance Spot / BingX Perpetual trade basis alignment."""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class BasisObservation:
    symbol: str
    perp_timestamp_ms: int
    spot_timestamp_ms: int
    perp_price: float
    spot_price: float
    spot_age_ms: int
    basis_bps: float


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.replace("-", "").replace("_", "").replace("/", "").upper()
    if not normalized.endswith("USDT") or len(normalized) <= 4:
        raise ValueError(f"expected a USDT symbol, got {symbol!r}")
    return normalized


def _valid_trade(row: dict[str, Any]) -> tuple[str, int, float] | None:
    try:
        symbol = _normalize_symbol(str(row["symbol"]))
        timestamp = int(row["exchange_time_ms"])
        price = float(row["price"])
    except (KeyError, TypeError, ValueError):
        return None
    if timestamp <= 0 or not math.isfinite(price) or price <= 0:
        return None
    return symbol, timestamp, price


def align_cross_venue_trades(
    perp_trades: Sequence[dict[str, Any]],
    spot_trades: Sequence[dict[str, Any]],
    *,
    max_spot_age_ms: int = 1_000,
) -> list[BasisObservation]:
    """Match each perp print to the latest spot print at or before its event time.

    This is deliberately a backward as-of join: a later Binance print is never
    allowed to leak into an earlier BingX observation.
    """
    if max_spot_age_ms < 0:
        raise ValueError("max_spot_age_ms cannot be negative")

    spot_by_symbol: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in spot_trades:
        parsed = _valid_trade(row)
        if parsed is not None:
            symbol, timestamp, price = parsed
            spot_by_symbol[symbol].append((timestamp, price))

    spot_times: dict[str, list[int]] = {}
    for symbol, rows in spot_by_symbol.items():
        rows.sort(key=lambda item: item[0])
        spot_times[symbol] = [timestamp for timestamp, _ in rows]

    observations: list[BasisObservation] = []
    for row in perp_trades:
        parsed = _valid_trade(row)
        if parsed is None:
            continue
        symbol, perp_ts, perp_price = parsed
        rows = spot_by_symbol.get(symbol)
        times = spot_times.get(symbol)
        if not rows or not times:
            continue
        index = bisect_right(times, perp_ts) - 1
        if index < 0:
            continue
        spot_ts, spot_price = rows[index]
        age = perp_ts - spot_ts
        if age > max_spot_age_ms:
            continue
        observations.append(BasisObservation(
            symbol=symbol,
            perp_timestamp_ms=perp_ts,
            spot_timestamp_ms=spot_ts,
            perp_price=perp_price,
            spot_price=spot_price,
            spot_age_ms=age,
            basis_bps=math.log(perp_price / spot_price) * 10_000.0,
        ))

    observations.sort(key=lambda item: (item.symbol, item.perp_timestamp_ms))
    return observations
