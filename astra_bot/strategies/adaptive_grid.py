"""
ASTRA BOT — Adaptive Grid Strategy
Адаптивная сетевая стратегия
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import (
    calculate_atr,
    simple_moving_average,
)
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class GridConfig(StrategyConfig):
    """Конфигурация grid стратегии"""
    name: str = "adaptive_grid"

    # Сетка
    grid_levels: int = 5  # Количество уровней по каждую сторону
    grid_spacing_percent: float = 1.0  # Расстояние между уровнями в %

    # ATR-based spacing
    use_atr_spacing: bool = True
    atr_multiplier: float = 1.0

    # Управление позицией
    max_positions_per_grid: int = 3  # Максимум позиций в сетке
    max_total_grid_positions: int = 10

    # Условия работы
    min_volume_ratio: float = 1.0
    max_spread_percent: float = 0.5  # Максимальный спред

    # Запреты
    martingale_enabled: bool = False  # ЗАПРЕЩЕНО
    averaging_down_enabled: bool = False  # ЗАПРЕЩЕНО

    # Выход
    take_profit_percent: float = 2.0  # Общий TP в %
    stop_loss_percent: float = 5.0  # SL для всей сетки

    # Адаптация
    adapt_to_volatility: bool = True
    volatility_lookback: int = 20


class AdaptiveGridStrategy(BaseStrategy):
    """
    Адаптивная сетевая стратегия.

    Работает ТОЛЬКО в RANGE режиме.
    Автоматически отключается при BREAKOUT или PANIC.

    Важные ограничения:
    - НЕ Martingale
    - НЕ Averaging Down
    - Сетка адаптируется к волатильности
    """

    def __init__(self, config: GridConfig = None):
        if config is None:
            config = GridConfig()
        super().__init__(config)
        self.config = config

        # Состояние сетки
        self._grid_levels: dict[str, list[dict]] = {}
        self._active_positions: dict[str, int] = {}

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook: models.OrderBook | None = None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        """Оценить возможность grid сделки"""
        if self.should_skip_signal():
            return None

        # GRID ТОЛЬКО в RANGE
        if market_regime and market_regime != "RANGE":
            logger.debug(f"{symbol}: Grid OFF in {market_regime}")
            return None

        # Проверка количества свечей
        if len(candles) < self.config.volatility_lookback:
            return None

        current_price = current_price or float(candles[-1].close)

        # Проверка ликвидности
        if orderbook:
            spread_pct = orderbook.spread_pct
            if spread_pct and float(spread_pct) > self.config.max_spread_percent:
                logger.debug(f"{symbol}: Spread {spread_pct:.2f}% too wide")
                return None

        # Расчёт сетки
        grid = self._calculate_grid_levels(
            symbol, current_price, candles
        )

        if not grid:
            return None

        # Проверка активных позиций
        active_count = self._active_positions.get(symbol, 0)
        if active_count >= self.config.max_total_grid_positions:
            return None

        # Находим подходящий уровень
        entry_level = self._find_entry_level(current_price, grid)

        if entry_level is None:
            return None

        # Расчёт цен
        entry_price = Decimal(str(entry_level["price"]))
        stop_loss = self.calculate_stop_loss(entry_price, candles)
        take_profit = self.calculate_take_profit(entry_price)

        # Проверка R:R
        risk = abs(float(entry_price - stop_loss))
        reward = abs(float(take_profit - entry_price))
        if risk > 0 and reward / risk < 1.0:
            return None

        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.GRID,
            direction=entry_level["direction"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=Decimal("0"),
            risk_amount=Decimal("0"),
            confidence=0.6,  # Умеренная уверенность для grid
            market_regime=market_regime or "RANGE",
            features={
                "grid_level": entry_level["level"],
                "grid_spacing": entry_level["spacing"],
                "distance_from_mid": abs(float(entry_price) - grid["mid_price"]) / grid["mid_price"] * 100,
            },
        )

    def _calculate_grid_levels(
        self,
        symbol: str,
        current_price: float,
        candles: list[models.Candle],
    ) -> dict | None:
        """Рассчитать уровни сетки"""
        # Центр сетки — SMA или текущая цена
        closes = [float(c.close) for c in candles[-self.config.volatility_lookback:]]
        mid_price = simple_moving_average(closes, min(20, len(closes))) or current_price

        # Расчёт спейсинга
        if self.config.use_atr_spacing:
            highs = [float(c.high) for c in candles[-20:]]
            lows = [float(c.low) for c in candles[-20:]]
            atr = calculate_atr(highs, lows, closes[-20:], 14)
            spacing = atr * self.config.atr_multiplier if atr else mid_price * 0.01
        else:
            spacing = mid_price * (self.config.grid_spacing_percent / 100)

        if spacing <= 0:
            return None

        # Генерация уровней
        levels = []
        for i in range(1, self.config.grid_levels + 1):
            # LONG уровни (ниже mid)
            long_price = mid_price - spacing * i
            levels.append({
                "level": -i,
                "price": long_price,
                "direction": models.TradeDirection.LONG,
                "spacing": spacing,
                "distance_from_mid": (mid_price - long_price) / mid_price * 100,
            })

            # SHORT уровни (выше mid)
            short_price = mid_price + spacing * i
            levels.append({
                "level": i,
                "price": short_price,
                "direction": models.TradeDirection.SHORT,
                "spacing": spacing,
                "distance_from_mid": (short_price - mid_price) / mid_price * 100,
            })

        return {
            "mid_price": mid_price,
            "spacing": spacing,
            "levels": levels,
        }

    def _find_entry_level(
        self,
        current_price: float,
        grid: dict,
    ) -> dict | None:
        """Найти подходящий уровень входа"""
        levels = grid["levels"]
        current_price = float(current_price)

        # Ищем ближайший уровень
        best_level = None
        best_distance = float('inf')

        for level in levels:
            distance = abs(current_price - level["price"])

            # Проверяем что цена не слишком далеко от уровня
            if distance <= level["spacing"] * 0.5:
                if distance < best_distance:
                    best_distance = distance
                    best_level = level

        return best_level

    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        candles: list[models.Candle],
    ) -> Decimal:
        """Рассчитать стоп-лосс"""
        # SL за пределами сетки
        return entry_price * Decimal("0.95")  # 5% стоп

    def calculate_take_profit(
        self,
        entry_price: Decimal,
    ) -> Decimal:
        """Рассчитать тейк-профит"""
        # TP на средней или по проценту
        return entry_price * Decimal("1.02")  # 2% TP

    def add_position(self, symbol: str, level: int):
        """Добавить позицию в сетку"""
        if symbol not in self._active_positions:
            self._active_positions[symbol] = 0
        self._active_positions[symbol] += 1

    def remove_position(self, symbol: str):
        """Удалить позицию из сетки"""
        if symbol in self._active_positions:
            self._active_positions[symbol] = max(0,
                self._active_positions[symbol] - 1)

    def is_grid_full(self, symbol: str) -> bool:
        """Проверить заполнена ли сетка"""
        return self._active_positions.get(symbol, 0) >= self.config.max_total_grid_positions

    def reset_grid(self, symbol: str):
        """Сбросить сетку"""
        self._active_positions.pop(symbol, None)

    def get_required_candles(self) -> int:
        return self.config.volatility_lookback

    def to_dict(self) -> dict:
        result = super().to_dict()
        result["grid_levels"] = self.config.grid_levels
        result["active_positions"] = dict(self._active_positions)
        return result


# Фабрика
def create_adaptive_grid_strategy(config: dict = None) -> AdaptiveGridStrategy:
    """Создать adaptive grid стратегию"""
    if config:
        cfg = GridConfig(**config)
    else:
        cfg = GridConfig()
    return AdaptiveGridStrategy(cfg)
