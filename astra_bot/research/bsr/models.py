"""Immutable BSR research data models."""
from __future__ import annotations
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
        if any(p <= 0 or q <= 0 for p, q in (*self.bids, *self.asks)):
            raise ValueError("book prices and quantities must be positive")
        if self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("crossed or locked order book")


@dataclass(frozen=True, slots=True)
class MarketTrade:
    timestamp_ms: int
    price: float
    quantity: float
    buyer_maker: bool
    def __post_init__(self) -> None:
        if min(self.timestamp_ms, self.price, self.quantity) <= 0:
            raise ValueError("trade timestamp, price and quantity must be positive")


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
