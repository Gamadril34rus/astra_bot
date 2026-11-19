"""Immutable BSR research data models."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    symbol: str
    timestamp_ms: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not self.symbol or self.timestamp_ms <= 0:
            raise ValueError("symbol and positive timestamp_ms are required")
        if not self.bids or not self.asks:
            raise ValueError("both order-book sides must be non-empty")
        levels = (*self.bids, *self.asks)
        if any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0
               for p, q in levels):
            raise ValueError("book prices and quantities must be finite and positive")
        if any(self.bids[i][0] <= self.bids[i + 1][0]
               for i in range(len(self.bids) - 1)):
            raise ValueError("bids must be strictly descending")
        if any(self.asks[i][0] >= self.asks[i + 1][0]
               for i in range(len(self.asks) - 1)):
            raise ValueError("asks must be strictly ascending")
        if self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("crossed or locked order book")


@dataclass(frozen=True, slots=True)
class MarketTrade:
    timestamp_ms: int
    price: float
    quantity: float
    buyer_maker: bool
    def __post_init__(self) -> None:
        if (self.timestamp_ms <= 0 or not math.isfinite(self.price)
                or not math.isfinite(self.quantity) or self.price <= 0 or self.quantity <= 0):
            raise ValueError("trade timestamp, price and quantity must be finite and positive")
        if not isinstance(self.buyer_maker, bool):
            raise ValueError("buyer_maker must be a boolean")


@dataclass(frozen=True, slots=True)
class SignalConfig:
    depth_bps: float = 20.0
    min_spread_bps: float = 0.0
    max_spread_bps: float = 8.0
    min_dislocation_bps: float = 8.0
    min_flow_imbalance: float = 0.20
    min_depth_drawdown: float = 0.25
    min_depth_recovery: float = 0.35
    min_rebound_bps: float = 2.0
    min_aggressive_notional: float = 10_000.0
    max_snapshot_age_ms: int = 2_000
    max_snapshot_gap_ms: int = 5_000
    min_score: float = 75.0


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    direction: Literal["LONG", "SHORT"]
    score: float
    timestamp_ms: int
    mid_price: float
    fair_price: float
    deviation_bps: float
    spread_bps: float
    book_imbalance: float
    trade_imbalance: float
    aggressive_notional: float
    depth_drawdown: float
    depth_recovery: float
    rebound_bps: float
