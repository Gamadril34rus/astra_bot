"""Fee-aware BSR sizing and net-return calculations."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0005
    entry_slippage_rate: float = 0.0001
    exit_slippage_rate: float = 0.0001
    # Signed exchange funding rate: positive => long pays; negative => short pays.
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


def net_pnl(*, notional: float, gross_return_rate: float, model: CostModel,
            entry_maker: bool = True, exit_maker: bool = True, is_long: bool = True) -> float:
    if notional <= 0:
        raise ValueError("notional must be positive")
    costs = round_trip_cost_rate(model, entry_maker=entry_maker, exit_maker=exit_maker,
                                 is_long=is_long)
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
