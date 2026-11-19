"""Fee-aware BSR sizing and net-return calculations."""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CostModel:
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0005
    entry_slippage_rate: float = 0.0001
    exit_slippage_rate: float = 0.0001
    # Fallback signed funding rate: positive => long pays; negative => short pays.
    funding_rate: float = 0.0

    def __post_init__(self) -> None:
        for name in ("maker_fee_rate", "taker_fee_rate", "entry_slippage_rate", "exit_slippage_rate"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not math.isfinite(self.funding_rate):
            raise ValueError("funding_rate must be finite")


def round_trip_cost_rate(model: CostModel, *, entry_maker: bool = True,
                         exit_maker: bool = True, include_funding: bool = True,
                         is_long: bool = True) -> float:
    entry = model.maker_fee_rate if entry_maker else model.taker_fee_rate
    exit_ = model.maker_fee_rate if exit_maker else model.taker_fee_rate
    funding = (model.funding_rate if is_long else -model.funding_rate) if include_funding else 0.0
    return entry + exit_ + model.entry_slippage_rate + model.exit_slippage_rate + funding


def funding_cost_rate(
    funding_events: Iterable[dict[str, Any]],
    *,
    entry_time_ms: int,
    exit_time_ms: int,
    is_long: bool = True,
) -> float:
    """Sum signed funding settlements during (entry_time_ms, exit_time_ms].

    Positive result is a cost to the position; negative result is a credit.
    Events must be filtered to one contract before calling. The interval is
    explicit so a trade is not charged a funding settlement it did not cross.
    """
    if (isinstance(entry_time_ms, bool) or not isinstance(entry_time_ms, int)
            or isinstance(exit_time_ms, bool) or not isinstance(exit_time_ms, int)
            or entry_time_ms <= 0 or exit_time_ms <= entry_time_ms):
        raise ValueError("entry/exit times must be positive ordered integer milliseconds")

    seen_times: set[int] = set()
    signed_total = 0.0
    for event in funding_events:
        if not isinstance(event, dict):
            raise ValueError("funding events must be objects")
        raw_time = event.get("fundingTime")
        if isinstance(raw_time, bool):
            raise ValueError("fundingTime must be a positive integer")
        if isinstance(raw_time, int):
            timestamp = raw_time
        elif isinstance(raw_time, str) and raw_time.isdecimal():
            timestamp = int(raw_time)
        else:
            raise ValueError("fundingTime must be a positive integer")
        if timestamp <= 0:
            raise ValueError("fundingTime must be a positive integer")
        if timestamp in seen_times:
            raise ValueError(f"duplicate fundingTime: {timestamp}")
        seen_times.add(timestamp)
        try:
            rate = float(event["fundingRate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("fundingRate must be numeric") from exc
        if not math.isfinite(rate):
            raise ValueError("fundingRate must be finite")
        if entry_time_ms < timestamp <= exit_time_ms:
            signed_total += rate

    return signed_total if is_long else -signed_total


def net_pnl(
    *,
    notional: float,
    gross_return_rate: float,
    model: CostModel,
    entry_maker: bool = True,
    exit_maker: bool = True,
    is_long: bool = True,
    funding_events: Iterable[dict[str, Any]] | None = None,
    entry_time_ms: int | None = None,
    exit_time_ms: int | None = None,
) -> float:
    if notional <= 0:
        raise ValueError("notional must be positive")
    if funding_events is None:
        if entry_time_ms is not None or exit_time_ms is not None:
            raise ValueError("funding event timestamps require funding_events")
        costs = round_trip_cost_rate(model, entry_maker=entry_maker, exit_maker=exit_maker,
                                     is_long=is_long)
    else:
        if entry_time_ms is None or exit_time_ms is None:
            raise ValueError("entry_time_ms and exit_time_ms are required with funding_events")
        costs = round_trip_cost_rate(model, entry_maker=entry_maker, exit_maker=exit_maker,
                                     include_funding=False)
        costs += funding_cost_rate(funding_events, entry_time_ms=entry_time_ms,
                                   exit_time_ms=exit_time_ms, is_long=is_long)
    return notional * (gross_return_rate - costs)


def position_quantity(*, equity: float, risk_fraction: float, entry_price: float,
                      stop_price: float, max_leverage: float, cost_rate: float) -> float:
    if min(equity, risk_fraction, entry_price, max_leverage) <= 0:
        raise ValueError("equity, risk_fraction, entry_price and leverage must be positive")
    if stop_price <= 0 or cost_rate < 0:
        raise ValueError("stop_price must be positive and cost_rate non-negative")
    risk_per_notional = abs(entry_price - stop_price) / entry_price + cost_rate
    if risk_per_notional <= 0:
        raise ValueError("risk per notional must be positive")
    notional = min(equity * risk_fraction / risk_per_notional, equity * max_leverage)
    return notional / entry_price
