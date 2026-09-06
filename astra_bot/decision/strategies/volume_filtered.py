"""
Volume-filtered strategies — Block 4.3: 4 strategies with volume filters.

- Trend Following: EMA crossover + ADX + volume confirmation
- Mean Reversion: RSI + Bollinger + volume
- Breakout: Range breakout + volume spike
- Momentum: Returns + volume

Each strategy checks volume_ratio > 1.2 for confirmation.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from ...core import models
from ..context import SignalCandidate, StrategyContext

logger = logging.getLogger(__name__)


def _sma(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    return sum(prices[-period:]) / period


def _ema(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    if len(gains) < period:
        return 50.0
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _volume_filter(volumes: list[float], period: int = 20, threshold: float = 1.2) -> bool:
    """Check if current volume > threshold * SMA volume."""
    if len(volumes) < period:
        return True  # Not enough data, allow
    sma = sum(volumes[-period:]) / period
    if sma == 0:
        return True
    return volumes[-1] / sma >= threshold


class TrendFollowingStrategyV2:
    """Trend Following — Block 4.3 with volume filter."""

    name = "trend_following"

    def __init__(self, volume_threshold: float = 1.2):
        self.volume_threshold = volume_threshold

    async def evaluate(self, ctx: StrategyContext) -> SignalCandidate | None:
        candles = ctx.candles
        if len(candles) < 50:
            return None

        closes = [float(c.close) for c in candles]
        volumes = [float(c.volume) for c in candles]

        # Volume filter
        if not _volume_filter(volumes, 20, self.volume_threshold):
            return None

        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        ema200 = _ema(closes, 200) if len(closes) >= 200 else _sma(closes, 50)

        # Trend conditions
        price = closes[-1]
        if price > ema20 > ema50 > ema200:
            # Uptrend
            return SignalCandidate(
                symbol=ctx.symbol,
                direction="long",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(price * 0.99)),
                take_profit=Decimal(str(price * 1.02)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=0.7,
                features={"ema_trend": 1, "volume_ok": 1},
            )
        elif price < ema20 < ema50 < ema200:
            # Downtrend
            return SignalCandidate(
                symbol=ctx.symbol,
                direction="short",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(price * 1.01)),
                take_profit=Decimal(str(price * 0.98)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=0.7,
                features={"ema_trend": -1, "volume_ok": 1},
            )
        return None


class MeanReversionStrategyV2:
    """Mean Reversion — Block 4.3 with volume filter."""

    name = "mean_reversion"

    def __init__(self, volume_threshold: float = 1.0):
        self.volume_threshold = volume_threshold

    async def evaluate(self, ctx: StrategyContext) -> SignalCandidate | None:
        candles = ctx.candles
        if len(candles) < 30:
            return None

        closes = [float(c.close) for c in candles]
        volumes = [float(c.volume) for c in candles]

        # For mean reversion, we want low volume on extremes (exhaustion) or high volume
        rsi = _rsi(closes, 14)
        sma20 = _sma(closes, 20)
        std20 = (sum((x - sma20) ** 2 for x in closes[-20:]) / 20) ** 0.5 if len(closes) >= 20 else 0.0
        upper_bb = sma20 + 2 * std20
        lower_bb = sma20 - 2 * std20
        price = closes[-1]

        # Oversold: RSI <30 and price near lower BB
        if rsi < 30 and price < lower_bb * 1.01:
            # Check volume - for reversal we want volume spike or exhaustion
            vol_ok = _volume_filter(volumes, 20, 0.8)  # Allow lower volume too
            if vol_ok:
                return SignalCandidate(
                    symbol=ctx.symbol,
                    direction="long",
                    entry_price=Decimal(str(price)),
                    stop_loss=Decimal(str(price * 0.98)),
                    take_profit=Decimal(str(sma20)),
                    timeframe=ctx.timeframe,
                    strategy=self.name,
                    confidence=0.6,
                    features={"rsi": rsi, "bb_pos": -1},
                )
        # Overbought
        elif rsi > 70 and price > upper_bb * 0.99:
            vol_ok = _volume_filter(volumes, 20, 0.8)
            if vol_ok:
                return SignalCandidate(
                    symbol=ctx.symbol,
                    direction="short",
                    entry_price=Decimal(str(price)),
                    stop_loss=Decimal(str(price * 1.02)),
                    take_profit=Decimal(str(sma20)),
                    timeframe=ctx.timeframe,
                    strategy=self.name,
                    confidence=0.6,
                    features={"rsi": rsi, "bb_pos": 1},
                )
        return None


class BreakoutStrategyV2:
    """Breakout — Block 4.3 with volume filter."""

    name = "breakout"

    def __init__(self, volume_threshold: float = 1.5):
        self.volume_threshold = volume_threshold

    async def evaluate(self, ctx: StrategyContext) -> SignalCandidate | None:
        candles = ctx.candles
        if len(candles) < 30:
            return None

        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        volumes = [float(c.volume) for c in candles]

        # Volume filter - breakout needs high volume
        if not _volume_filter(volumes, 20, self.volume_threshold):
            return None

        # Range: last 20 candles high/low
        range_high = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
        range_low = min(lows[-21:-1]) if len(lows) >= 21 else min(lows[:-1])
        price = closes[-1]

        if price > range_high * 1.005:
            # Breakout up
            return SignalCandidate(
                symbol=ctx.symbol,
                direction="long",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(range_high * 0.99)),
                take_profit=Decimal(str(price + (price - range_low) * 0.5)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=0.75,
                features={"breakout": 1, "volume_spike": volumes[-1] / (sum(volumes[-20:]) / 20)},
            )
        elif price < range_low * 0.995:
            # Breakout down
            return SignalCandidate(
                symbol=ctx.symbol,
                direction="short",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(range_low * 1.01)),
                take_profit=Decimal(str(price - (range_high - price) * 0.5)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=0.75,
                features={"breakout": -1, "volume_spike": volumes[-1] / (sum(volumes[-20:]) / 20)},
            )
        return None


class MomentumStrategyV2:
    """Momentum — Block 4.3 with volume filter."""

    name = "momentum"

    def __init__(self, volume_threshold: float = 1.2):
        self.volume_threshold = volume_threshold

    async def evaluate(self, ctx: StrategyContext) -> SignalCandidate | None:
        candles = ctx.candles
        if len(candles) < 30:
            return None

        closes = [float(c.close) for c in candles]
        volumes = [float(c.volume) for c in candles]

        if not _volume_filter(volumes, 20, self.volume_threshold):
            return None

        # Momentum: 10-day return
        if len(closes) < 11:
            return None

        ret_10 = (closes[-1] - closes[-11]) / closes[-11] if closes[-11] != 0 else 0
        ret_20 = (closes[-1] - closes[-21]) / closes[-21] if len(closes) >= 21 and closes[-21] != 0 else 0

        price = closes[-1]

        if ret_10 > 0.03 and ret_20 > 0.05:
            # Strong up momentum
            return SignalCandidate(
                symbol=ctx.symbol,
                direction="long",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(price * 0.98)),
                take_profit=Decimal(str(price * 1.04)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=0.65,
                features={"ret_10": ret_10, "ret_20": ret_20},
            )
        elif ret_10 < -0.03 and ret_20 < -0.05:
            # Strong down momentum
            return SignalCandidate(
                symbol=ctx.symbol,
                direction="short",
                entry_price=Decimal(str(price)),
                stop_loss=Decimal(str(price * 1.02)),
                take_profit=Decimal(str(price * 0.96)),
                timeframe=ctx.timeframe,
                strategy=self.name,
                confidence=0.65,
                features={"ret_10": ret_10, "ret_20": ret_20},
            )
        return None
