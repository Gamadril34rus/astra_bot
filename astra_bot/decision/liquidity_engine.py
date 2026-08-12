"""Liquidity engine — проверка стоимости исполнения."""

from __future__ import annotations

from dataclasses import dataclass

from .orderbook_engine import OrderBookReport


@dataclass
class LiquidityReport:
    spread_pct: float
    expected_slippage_pct: float
    avg_volume_24h: float
    is_tradable: bool
    expected_cost_pct: float

    def to_dict(self) -> dict:
        return self.__dict__


class LiquidityEngine:
    def __init__(
        self,
        max_slippage_pct: float = 0.1,
        min_volume: float = 1_000_000.0,
        taker_fee_pct: float = 0.05,
        safety_buffer_pct: float = 0.03,
    ):
        self.max_slippage_pct = max_slippage_pct
        self.min_volume = min_volume
        self.taker_fee_pct = taker_fee_pct
        self.safety_buffer_pct = safety_buffer_pct

    def assess(
        self,
        book: OrderBookReport,
        avg_volume_24h: float = 0.0,
        edge_pct: float = 0.0,
    ) -> LiquidityReport:
        # Приблизительный slippage: половина спреда + штраф за малый depth.
        slippage = book.spread_pct / 2
        if book.bid_depth and book.ask_depth:
            depth = min(book.bid_depth, book.ask_depth)
            if depth < self.min_volume / 100:
                slippage += 0.1
        cost = slippage + self.taker_fee_pct + self.safety_buffer_pct
        is_tradable = (
            book.is_healthy
            and slippage <= self.max_slippage_pct
            and edge_pct > cost
        )
        return LiquidityReport(
            spread_pct=book.spread_pct,
            expected_slippage_pct=slippage,
            avg_volume_24h=avg_volume_24h,
            is_tradable=is_tradable,
            expected_cost_pct=cost,
        )
