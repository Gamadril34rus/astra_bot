"""
ASTRA BOT — Base Strategy
Базовый класс для всех торговых стратегий
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from ..core import events, models

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Тип сигнала"""
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    GRID = "grid"
    ARBITRAGE = "arbitrage"


@dataclass
class StrategyConfig:
    """Конфигурация стратегии"""
    name: str = "strategy"
    enabled: bool = True
    weight: float = 1.0

    # Kill switch
    kill_switch: bool = False
    decay_threshold: float = 1.0  # Profit Factor порог для kill switch

    # Специфичные параметры
    parameters: dict[str, Any] = field(default_factory=dict)

    # Статистика
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    profit_factor: float = 0.0

    @property
    def is_healthy(self) -> bool:
        return not self.kill_switch and self.profit_factor >= self.decay_threshold

    def update_performance(self, won: bool, pnl: float):
        """Обновить статистику"""
        self.total_trades += 1
        if won:
            self.wins += 1
            self.net_pnl += pnl
        else:
            self.losses += 1
            self.net_pnl -= abs(pnl)

        # Простой расчёт profit factor
        if self.losses > 0 and self.wins > 0:
            avg_win = self.net_pnl / self.wins
            avg_loss = abs(self.net_pnl) / self.losses
            if avg_loss > 0:
                self.profit_factor = avg_win / avg_loss


@dataclass
class Signal:
    """Торговый сигнал"""
    id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    strategy_name: str = ""
    signal_type: SignalType = SignalType.MOMENTUM
    direction: models.TradeDirection = models.TradeDirection.LONG
    entry_price: Decimal = Decimal("0")
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    position_size: Decimal = Decimal("0")
    risk_amount: Decimal = Decimal("0")
    confidence: float = 0.0
    ml_probability: float | None = None
    expected_value: float | None = None
    market_regime: str = "UNKNOWN"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    status: str = "pending"
    rejection_reason: str | None = None
    features: dict[str, float] = field(default_factory=dict)

    @property
    def risk_reward_ratio(self) -> float:
        risk = abs(float(self.entry_price - self.stop_loss))
        reward = abs(float(self.take_profit - self.entry_price))
        if risk <= 0:
            return 0.0
        return reward / risk

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "symbol": self.symbol,
            "strategy": self.strategy_name,
            "type": self.signal_type.value,
            "direction": self.direction.value,
            "entry": float(self.entry_price),
            "stop_loss": float(self.stop_loss),
            "take_profit": float(self.take_profit),
            "size": float(self.position_size),
            "risk": float(self.risk_amount),
            "confidence": self.confidence,
            "ml_probability": self.ml_probability,
            "ev": self.expected_value,
            "regime": self.market_regime,
            "status": self.status,
        }


class BaseStrategy(ABC):
    """
    Базовый класс для всех торговых стратегий.

    Каждая стратегия должна:
    1. Оценивать рыночные данные
    2. Генерировать сигналы (или None если нет возможности)
    3. Рассчитывать стопы и тейк-профиты
    4. Проверять совместимость с режимом рынка
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.name = config.name
        self.enabled = config.enabled
        self.weight = config.weight
        self.kill_switch = config.kill_switch

        # Внутреннее состояние
        self._last_signal_time: datetime | None = None
        self._consecutive_losses: int = 0
        self._consecutive_wins: int = 0

        # Статистика
        self.performance = StrategyConfig(
            name=self.name,
            enabled=True,
            weight=self.weight,
        )

    @abstractmethod
    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook: models.OrderBook | None = None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        """
        Оценить возможность торговли.

        Args:
            symbol: Торговый символ
            candles: Исторические свечи
            orderbook: Стакан заявок (опционально)
            current_price: Текущая цена (опционально)
            market_regime: Текущий режим рынка (опционально)

        Returns:
            Signal если есть возможность, иначе None
        """

    @abstractmethod
    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        candles: list[models.Candle],
        atr: float | None = None,
    ) -> Decimal:
        """Рассчитать стоп-лосс цену"""

    @abstractmethod
    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        candles: list[models.Candle],
    ) -> list[dict]:
        """Рассчитать уровни тейк-профита"""

    def check_kill_switch(self) -> bool:
        """Проверить kill switch"""
        return self.config.kill_switch or self.performance.kill_switch

    def check_regime_compatibility(
        self,
        regime: str,
    ) -> str:
        """
        Проверить совместимость со режимом рынка.

        Returns:
            "ON" — можно торговать
            "REDUCED" — можно с ограничениями
            "OFF" — нельзя торговать
        """
        from ..engines.regime_detector import STRATEGY_REGIME_COMPATIBILITY

        return STRATEGY_REGIME_COMPATIBILITY.get(self.name, {}).get(
            regime, "OFF"
        )

    def update_performance(self, won: bool, pnl: float):
        """Обновить статистику производительности"""
        self.performance.update_performance(won, pnl)

        if not won:
            self._consecutive_losses += 1
            self._consecutive_wins = 0
        else:
            self._consecutive_wins += 1
            self._consecutive_losses = 0

        # Автоматический kill switch при ухудшении
        if self.performance.profit_factor < self.config.decay_threshold:
            if not self.config.kill_switch:
                logger.warning(
                    f"Strategy {self.name} kill switch activated: "
                    f"PF={self.performance.profit_factor:.2f} < {self.config.decay_threshold}"
                )
                self.config.kill_switch = True
                self.performance.kill_switch = True

                events.emit_async(events.EventType.STRATEGY_KILLED, {
                    "strategy": self.name,
                    "profit_factor": self.performance.profit_factor,
                    "reason": "Decay detected",
                })

    def should_skip_signal(self) -> bool:
        """Проверить нужно ли пропустить сигнал"""
        if not self.enabled:
            return True
        if self.check_kill_switch():
            return True
        return False

    def get_regime_compatibility(
        self,
        regime: str,
    ) -> str:
        """Получить уровень совместимости со режимом"""
        from ..engines.regime_detector import MarketRegime

        try:
            regime_enum = MarketRegime(regime)
            return self.check_regime_compatibility(regime_enum.value)
        except ValueError:
            return "OFF"

    def to_dict(self) -> dict:
        """Сериализовать состояние"""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "kill_switch": self.config.kill_switch,
            "total_trades": self.performance.total_trades,
            "wins": self.performance.wins,
            "losses": self.performance.losses,
            "net_pnl": self.performance.net_pnl,
            "profit_factor": self.performance.profit_factor,
            "win_rate": (
                self.performance.wins / self.performance.total_trades * 100
                if self.performance.total_trades > 0 else 0
            ),
        }


# Рейтинг стратегий по compatability
STRATEGY_COMPATIBILITY = {
    "momentum": {
        "BULL_TREND": "ON",
        "BEAR_TREND": "ON",
        "RANGE": "REDUCED",
        "BREAKOUT": "ON",
        "HIGH_VOLATILITY": "REDUCED",
        "LOW_VOLATILITY": "ON",
        "PANIC": "OFF",
        "UNKNOWN": "OFF",
    },
    "mean_reversion": {
        "BULL_TREND": "REDUCED",
        "BEAR_TREND": "REDUCED",
        "RANGE": "ON",
        "BREAKOUT": "OFF",
        "HIGH_VOLATILITY": "OFF",
        "LOW_VOLATILITY": "ON",
        "PANIC": "OFF",
        "UNKNOWN": "OFF",
    },
    "adaptive_grid": {
        "BULL_TREND": "OFF",
        "BEAR_TREND": "OFF",
        "RANGE": "ON",
        "BREAKOUT": "OFF",
        "HIGH_VOLATILITY": "OFF",
        "LOW_VOLATILITY": "REDUCED",
        "PANIC": "OFF",
        "UNKNOWN": "OFF",
    },
    "arbitrage": {
        "BULL_TREND": "ON",
        "BEAR_TREND": "ON",
        "RANGE": "ON",
        "BREAKOUT": "REDUCED",
        "HIGH_VOLATILITY": "OFF",
        "LOW_VOLATILITY": "ON",
        "PANIC": "OFF",
        "UNKNOWN": "ON",
    },
}
