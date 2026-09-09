"""
Open Interest Divergence Strategy.

Цена обновляет хай, а OI падает -> SHORT.
Цена падает, а OI растёт -> LONG.
Данные: метод get_open_interest BingXClient с TTL-кэшем 15 минут.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..adapters.bingx.client import BingXClient
from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)

_lazy_bingx_client: BingXClient | None = None
_oi_cache: dict[str, list[tuple[float, float]]] = {}  # symbol -> [(timestamp, oi_val)]

# Блок J: OI-история персистится в файл. In-memory кэш с TTL 900с при
# сессиях по 200с давал каждую сессию 1 точку → oi_change всегда 0 →
# стратегия не могла сработать никогда. Последние 100 точек на символ.
_OI_HISTORY_FILE = Path("models/oi_history.json")
_OI_HISTORY_LOADED = False


def _load_oi_history() -> None:
    """Подтянуть OI-историю из файла (раз за процесс)."""
    global _OI_HISTORY_LOADED
    if _OI_HISTORY_LOADED:
        return
    _OI_HISTORY_LOADED = True
    try:
        if _OI_HISTORY_FILE.exists():
            data = json.loads(_OI_HISTORY_FILE.read_text(encoding="utf-8"))
            for sym, pts in (data.get("symbols") or {}).items():
                clean = [(float(ts), float(v)) for ts, v in pts if v is not None]
                if clean:
                    _oi_cache[str(sym)] = clean[-100:]
    except Exception as exc:
        logger.debug("oi history load failed: %s", exc)


def _save_oi_history() -> None:
    """Атомарно сохранить OI-историю в файл."""
    try:
        _OI_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated": time.time(),
            "symbols": {sym: hist[-100:] for sym, hist in _oi_cache.items()},
        }
        tmp = _OI_HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(_OI_HISTORY_FILE)
    except Exception as exc:
        logger.debug("oi history save failed: %s", exc)


async def _get_open_interest_val(symbol: str, ttl: int = 900) -> float | None:
    global _lazy_bingx_client
    _load_oi_history()
    now = time.time()
    history = _oi_cache.get(symbol, [])
    history = [x for x in history if now - x[0] < 86400]
    _oi_cache[symbol] = history

    if history and (now - history[-1][0] < ttl):
        return history[-1][1]

    try:
        if _lazy_bingx_client is None or _lazy_bingx_client._session is None:
            _lazy_bingx_client = BingXClient({})
            await _lazy_bingx_client.initialize()
        val = await _lazy_bingx_client.get_open_interest(symbol)
        if val is not None:
            float_val = float(val)
            history.append((now, float_val))
            _oi_cache[symbol] = history[-100:]
            _save_oi_history()
            return float_val
    except Exception as exc:
        logger.debug("network/helper error: %s", exc)

    return history[-1][1] if history else None


@dataclass
class OpenInterestDivergenceConfig(StrategyConfig):
    name: str = "open_interest_divergence"
    enabled: bool = True
    oi_window: int = 10
    price_change_min: float = 0.01
    stop_pct: float = 0.01
    min_rr: float = 1.5


class OpenInterestDivergenceStrategy(BaseStrategy[OpenInterestDivergenceConfig]):
    """Стратегия дивергенции цены и открытого интереса (OI)."""

    def __init__(self, config: OpenInterestDivergenceConfig | None = None):
        super().__init__(config or OpenInterestDivergenceConfig())
        self.config: OpenInterestDivergenceConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        if not self.config.enabled:
            logger.debug("%s: Strategy disabled", self.name)
            return None

        try:
            c = self.config
            if not candles or len(candles) < c.oi_window + 2:
                return None

            curr_oi = await _get_open_interest_val(symbol)
            if curr_oi is None or curr_oi <= 0:
                return None

            history = _oi_cache.get(symbol, [])
            if len(history) < 2:
                prev_oi = curr_oi
            else:
                prev_oi = history[-2][1]

            if prev_oi <= 0:
                return None

            oi_change_pct = (curr_oi - prev_oi) / prev_oi

            history_candles = candles[:-1]
            if len(history_candles) < c.oi_window:
                return None

            price_start = float(history_candles[-c.oi_window].close)
            curr_candle = candles[-1]
            price = float(current_price or curr_candle.close)

            price_change_pct = (price - price_start) / price_start

            direction = None
            stop_price = 0.0
            target_price = 0.0

            if price_change_pct >= c.price_change_min and oi_change_pct < -0.005:
                direction = models.TradeDirection.SHORT
                stop_price = price * (1.0 + c.stop_pct)
                target_price = price * (1.0 - c.stop_pct * c.min_rr)

            elif price_change_pct <= -c.price_change_min and oi_change_pct > 0.005:
                direction = models.TradeDirection.LONG
                stop_price = price * (1.0 - c.stop_pct)
                target_price = price * (1.0 + c.stop_pct * c.min_rr)

            if direction is None:
                return None

            confidence = min(0.85, max(0.5, 0.5 + 10.0 * abs(oi_change_pct)))

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
        except Exception as exc:
            logger.warning("%s evaluate error: %s", self.name, exc)
            return None

    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        return entry_price * Decimal("0.99")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.015"), "fraction": 1.0}]
