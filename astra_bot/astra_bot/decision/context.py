"""Контексты рыночных данных и кандидатов в сделку."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..core import models


@dataclass
class MarketContext:
    """Снимок рынка по инструменту на всех таймфреймах."""

    symbol: str
    current_price: Decimal
    candles: dict[str, list[models.Candle]] = field(default_factory=dict)
    orderbook: models.OrderBook | None = None
    news_score: int = 0
    onchain_score: float = 0.0
    derivatives: dict[str, float] = field(default_factory=dict)
    # Карта символ -> режим/цена BTC/ETH/SOL для корреляции.
    global_market: dict[str, Any] = field(default_factory=dict)

    def candles_on(self, tf: str) -> list[models.Candle]:
        return self.candles.get(tf, [])


@dataclass
class SignalCandidate:
    """Сигнал от одной из стратегий. До финального решения."""

    symbol: str
    direction: str  # long/short
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    timeframe: str
    strategy: str
    confidence: float = 0.0
    features: dict[str, Any] = field(default_factory=dict)
    ml_probability: float | None = None
    expected_edge_pct: float | None = None
    position_size: Decimal = Decimal("0")
    # Заполняется после risk/scoring.
    total_score: float = 0.0
    rejections: list[str] = field(default_factory=list)

    @property
    def risk_reward(self) -> float:
        risk = abs(float(self.entry_price - self.stop_loss))
        reward = abs(float(self.take_profit - self.entry_price))
        if risk <= 0:
            return 0.0
        return reward / risk

    @property
    def risk_amount(self) -> float:
        return abs(float(self.entry_price - self.stop_loss))

    def reject(self, reason: str) -> None:
        self.rejections.append(reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry": str(self.entry_price),
            "stop": str(self.stop_loss),
            "take": str(self.take_profit),
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "confidence": round(self.confidence, 3),
            "ml_probability": self.ml_probability,
            "expected_edge_pct": self.expected_edge_pct,
            "rr": round(self.risk_reward, 2),
            "size": str(self.position_size),
            "score": round(self.total_score, 1),
            "rejections": self.rejections,
        }
