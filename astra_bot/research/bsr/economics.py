"""Fee-aware BSR sizing and net-return calculations."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0005
    entry_slippage_rate: float = 0.0001
    exit_slippage_rate: float = 0.0001
    funding_rate: float = 0.0
    funding_paid_by_long: bool = True
    def __post_init__(self) -> None:
        for name in ("maker_fee_rate", "taker_fee_rate", "entry_slippage_rate", "exit_slippage_rate"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")


def round_trip_cost_rate(model: CostModel, *, entry_maker: bool = True,
                         exit_maker: bool = True, include_funding: bool = True,
                         is_long: bool = True) -> float:
    entry = model.maker_fee_rate if entry_maker else model.taker_fee_rate
    exit_ = model.maker_fee_rate if exit_maker else model.taker_fee_rate
    pays_funding = is_long == model.funding_paid_by_long
    funding = model.funding_rate if include_funding and pays_funding else 0.0
    if include_funding and not pays_funding:
        funding = -model.funding_rate
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
