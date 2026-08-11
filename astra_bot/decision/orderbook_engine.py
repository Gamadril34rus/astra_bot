"""Order book engine.

Анализирует глубину стакана и дисбаланс. Самостоятельно сигналом
НЕ является. Если данных нет — возвращает нейтральную оценку,
а не блокирует торговлю.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import models


@dataclass
class OrderBookReport:
    spread_pct: float
    bid_depth: float
    ask_depth: float
    imbalance: float  # >0 bullish, <0 bearish
    is_healthy: bool

    def to_dict(self) -> dict:
        return self.__dict__


class OrderBookEngine:
    def __init__(self, max_spread_pct: float = 0.25, min_depth: float = 5000.0):
        self.max_spread_pct = max_spread_pct
        self.min_depth = min_depth

    def analyse(self, orderbook: models.OrderBook | None, mid_price: float) -> OrderBookReport:
        if orderbook is None or not getattr(orderbook, "bids", None):
            return OrderBookReport(0.0, 0.0, 0.0, 0.0, True)

        best_bid = float(orderbook.bids[0].price) if orderbook.bids else 0.0
        best_ask = float(orderbook.asks[0].price) if orderbook.asks else 0.0
        spread_pct = (
            (best_ask - best_bid) / mid_price * 100
            if best_bid and best_ask and mid_price
            else 0.0
        )
        bid_depth = sum(float(q.price) * float(q.size) for q in orderbook.bids[:10])
        ask_depth = sum(float(q.price) * float(q.size) for q in orderbook.asks[:10])
        total = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0
        is_healthy = spread_pct <= self.max_spread_pct and (bid_depth + ask_depth) >= self.min_depth
        return OrderBookReport(
            spread_pct=spread_pct,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            imbalance=imbalance,
            is_healthy=is_healthy,
        )
