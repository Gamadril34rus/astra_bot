"""
Funding Rate Contrarian Strategy.

Ставка фандинга > +0.15%/8ч (funding_long_extreme = 0.0015) -> искать SHORT.
Ставка фандинга < -0.05%/8ч (funding_short_extreme = -0.0005) -> искать LONG.
В комбинации с перекупленностью/перепроданностью по RSI или ложным пробоем.
Публичный BingXClient с TTL-кэшем 15 минут.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from ..adapters.bingx.client import BingXClient
from ..core import models
from ..core.utils import calculate_rsi
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

_lazy_bingx_client: BingXClient | None = None
_funding_cache: dict[str, tuple[float, float]] = {}  # symbol -> (timestamp, rate)


async def _get_funding_rate(symbol: str, ttl: int = 900) -> float | None:
    global _lazy_bingx_client
    now = time.time()
    if symbol in _funding_cache:
        ts, rate = _funding_cache[symbol]
        if now - ts < ttl:
            return rate
    try:
        if _lazy_bingx_client is None or _lazy_bingx_client._session is None:
            _lazy_bingx_client = BingXClient({})
            await _lazy_bingx_client.initialize()
        res = await _lazy_bingx_client.get_funding_rate(symbol)
        if res and "rate" in res:
            rate = float(res["rate"])
            _funding_cache[symbol] = (now, rate)
            return rate
    except Exception:
        pass
    return None


@dataclass
class FundingRateContrarianConfig(StrategyConfig):
    name: str = "funding_rate_contrarian"
    enabled: bool = True
    funding_long_extreme: float = 0.0015  # +0.15% -> SHORT
    funding_short_extreme: float = -0.0005  # -0.05% -> LONG
    rsi_period: int = 14
    rsi_overbought: float = 65.0
    rsi_oversold: float = 35.0
    stop_pct: float = 0.01
    min_rr: float = 1.2


class FundingRateContrarianStrategy(BaseStrategy[FundingRateContrarianConfig]):
    """Контртрендовая стратегия по экстремумам фандинга."""

    def __init__(self, config: FundingRateContrarianConfig | None = None):
        super().__init__(config or FundingRateContrarianConfig())
        self.config: FundingRateContrarianConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        try:
            c = self.config
            if not candles or len(candles) < c.rsi_period + 5:
                return None

            funding_rate = await _get_funding_rate(symbol)
            if funding_rate is None:
                return None

            closes = [float(x.close) for x in candles]
            rsi_val = calculate_rsi(closes, period=c.rsi_period)
            if rsi_val is None:
                return None

            curr_candle = candles[-1]
            price = float(current_price or curr_candle.close)

            direction = None
            stop_price = 0.0
            target_price = 0.0

            if funding_rate >= c.funding_long_extreme and rsi_val >= c.rsi_overbought:
                direction = models.TradeDirection.SHORT
                stop_price = price * (1.0 + c.stop_pct)
                target_price = price * (1.0 - c.stop_pct * c.min_rr)
            elif funding_rate <= c.funding_short_extreme and rsi_val <= c.rsi_oversold:
                direction = models.TradeDirection.LONG
                stop_price = price * (1.0 - c.stop_pct)
                target_price = price * (1.0 + c.stop_pct * c.min_rr)

            if direction is None:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 100.0 * abs(funding_rate)))

            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MEAN_REVERSION,
                direction=direction,
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(stop_price)),
                take_profit=Decimal(str(target_price)),
                position_size=Decimal("0"),
                risk_amount=Decimal("0"),
                confidence=confidence,
                market_regime=market_regime or "UNKNOWN",
            )
        except Exception:
            return None

    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        return entry_price * Decimal("0.99")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.015"), "fraction": 1.0}]
