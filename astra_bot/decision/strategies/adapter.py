"""Адаптер интерфейсов стратегий.

Стратегии из ``decision/strategies/`` (pattern_strategies, volume_filtered)
написаны против плоского контекста :class:`StrategyContext` и возвращают
:class:`SignalCandidate` напрямую, а DecisionPipeline вызывает стратегии
по контракту ``BaseStrategy.evaluate(symbol, candles, ...) -> Signal``.

Раньше эти девять стратегий молча не загружались: импорт падал на
несуществующем ``StrategyContext``, а после «починки» импорта упал бы
вызов с неправильной сигнатурой. :class:`PipelineStrategyAdapter`
связывает два контракта и делает V2-стратегии доступными пайплайну.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from ...core import models
from ...strategies.base import Signal, SignalType
from ..context import SignalCandidate, StrategyContext

logger = logging.getLogger(__name__)

__all__ = ["PipelineStrategyAdapter"]


class PipelineStrategyAdapter:
    """V2/pattern-стратегия (ctx -> SignalCandidate) в контракте пайплайна.

    Пайплайн ожидает ``async evaluate(symbol=..., candles=..., ...) -> Signal``
    и читает ``name`` / ``preferred_timeframe``. Адаптер принимает вызов
    пайплайна, собирает :class:`StrategyContext`, делегирует внутренней
    стратегии и конвертирует её ``SignalCandidate`` обратно в ``Signal``.
    """

    def __init__(
        self,
        inner: Any,
        signal_type: SignalType = SignalType.MOMENTUM,
        default_timeframe: str = "5m",
    ) -> None:
        self.inner = inner
        self.signal_type = signal_type
        self.default_timeframe = default_timeframe
        self.name = getattr(inner, "name", inner.__class__.__name__)
        self.preferred_timeframe = getattr(inner, "preferred_timeframe", None)

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook: models.OrderBook | None = None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        ctx = StrategyContext(
            symbol=symbol,
            timeframe=self.preferred_timeframe or self.default_timeframe,
            candles=list(candles),
            orderbook=orderbook,
            current_price=(
                Decimal(str(current_price))
                if current_price is not None
                else None
            ),
            market_regime=market_regime or "UNKNOWN",
        )
        candidate: SignalCandidate | None = await self.inner.evaluate(ctx)
        if candidate is None:
            return None

        try:
            direction = models.TradeDirection(candidate.direction)
        except ValueError:
            logger.warning(
                "adapter %s: неизвестное направление %r — сигнал отброшен",
                self.name,
                candidate.direction,
            )
            return None

        entry = candidate.entry_price
        stop = candidate.stop_loss
        return Signal(
            symbol=candidate.symbol or symbol,
            strategy_name=candidate.strategy or self.name,
            signal_type=self.signal_type,
            direction=direction,
            entry_price=entry,
            stop_loss=stop,
            take_profit=candidate.take_profit,
            position_size=candidate.position_size,
            risk_amount=abs(entry - stop),
            confidence=max(0.0, min(1.0, float(candidate.confidence))),
            market_regime=market_regime or "UNKNOWN",
            features={
                str(k): v
                for k, v in dict(candidate.features or {}).items()
                if isinstance(v, (int, float))
            },
        )
