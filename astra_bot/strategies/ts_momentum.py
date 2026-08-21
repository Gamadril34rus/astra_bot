"""Time-series momentum (трендовый фильтр, 45 дней).

Правило из свободного академического препринта Moskowitz, Ooi, Pedersen
«Time Series Momentum» (и семейства правил «momentum» из бесплатных курсов
Babypips/Investopedia): держим направление рынка по знаку доходности за
последние N дней (по умолчанию 45), с мёртвой зоной ±2%, чтобы не дёргаться
вбок. Проверено на истории Binance BTC/USDT (2021–2026, таймфреймы 1h/4h)
скриптом ``scripts/research_free_strategies.py``:

- 45 дней, 4h, 2 года: +5.7% (PF 1.58, просадка 6.2%) при buy&hold +5.2%
  с просадкой 53% — т.е. тот же результат с в ~9 раз меньшей просадкой;
- правило положительно на периодах 30–90 дней и на обеих половинах окна;
- вариант с ``adx_min=20`` (вход только в подтверждённый тренд) прошёл
  walk-forward: OOS PF 1.30, история 2021–2026 PF 1.37 (strategy_lab.py).

Стратегия работает **переворотами** (flip), а не одиночными входами:
сигнал подаётся только при смене режима (0→long, long→short, …→0), поэтому
движок должен уметь закрывать противоположную позицию по сигналу (см.
``DecisionPipeline`` — действия FLIP/CLOSE и ``BacktestConfig.close_on_opposite_signal``).

Атрибут ``preferred_timeframe`` указывает пайплайну, на каких свечах
стратегию оценивать (в live-движке тянутся свечи 4h).

Реальные деньги не используются — сигналы идут только в demo/paper-контур.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from ..core.utils import calculate_adx, calculate_atr
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)

# Действия флип-стратегии в features["tsm_action"] (тип dict[str, float]).
TSM_ACTION_FLAT = 1.0  # режим окончен: закрыть позицию, не открывать
TSM_ACTION_FLIP = 2.0  # режим сменился: закрыть противоположную и открыть новую

# Сколько баров в сутках для известных таймфреймов (для оценки lookback).
_BARS_PER_DAY_HINT = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}


@dataclass
class TimeSeriesMomentumConfig(StrategyConfig):
    """Конфигурация time-series momentum (флип-стратегии)."""

    name: str = "ts_momentum"

    # Окно импульса в днях. 45 — середина устойчивого диапазона 30–90,
    # подтверждённого на 1h/4h и на истории 2021–2026.
    lookback_days: int = 45

    # Мёртвая зона: |доходность| < band → «вне рынка» (flat).
    band: float = 0.02

    # Разрешать шорты. Для spot-режима можно выключить (long-only).
    allow_short: bool = True

    # Фильтр подтверждения тренда: новые режимы (long/short) открываются
    # только при ADX > adx_min. Если импульс развернулся, но тренд слабый —
    # выходим в flat вместо переворота. 0 — фильтр выключен.
    # Правило adx_min=20 прошло walk-forward валидацию (strategy_lab.py).
    adx_min: float = 0.0
    adx_period: int = 14

    # Катастрофический стоп: 6×ATR(14). В норме позиция живёт до смены
    # режима; стоп защищает от резкого обвала тренда.
    atr_period: int = 14
    atr_stop_mult: float = 6.0

    # Минимальное число свечей (авто-пересчёт от таймфрейма в evaluate).
    min_bars: int = 0

    # На каком таймфрейме оцениваться в DecisionPipeline (live-движок).
    preferred_timeframe: str = "4h"


def _bars_per_day(candles: list[models.Candle]) -> float:
    """Оценить баров в сутках по шагу свечей (медиана дельт, мс)."""
    if len(candles) < 3:
        return 0.0
    steps = sorted(
        b - a
        for a, b in zip(
            [int(c.open_time) for c in candles[:-1]],
            [int(c.open_time) for c in candles[1:]],
            strict=True,
        )
        if b > a
    )
    if not steps:
        return 0.0
    step_ms = steps[len(steps) // 2]
    if step_ms <= 0:
        return 0.0
    return 86_400_000.0 / step_ms


class TimeSeriesMomentumStrategy(BaseStrategy):
    """Трендовый фильтр: перевороты по знаку N-дневной доходности."""

    def __init__(self, config: TimeSeriesMomentumConfig | None = None):
        super().__init__(config or TimeSeriesMomentumConfig())
        self.config: TimeSeriesMomentumConfig
        # Текущее направление режима: -1 / 0 / 1. Сигнал — только на смене.
        self._direction: int = 0

    # ------------------------------------------------------------------ core
    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        if self.should_skip_signal():
            return None

        need = self._lookback_bars(candles)
        if need <= 0 or len(candles) < need + 2:
            return None

        price = float(current_price or candles[-1].close)
        past_price = float(candles[-(need + 1)].close)
        if past_price <= 0:
            return None
        momentum = price / past_price - 1.0

        desired = self._desired_direction(momentum)
        previous = self._direction

        # Фильтр подтверждения тренда: новый long/short только при
        # ADX > adx_min, иначе неподтверждённый переворот = выход в flat
        # (семантика из strategy_lab.py, прошедшая walk-forward).
        if (
            desired != previous
            and desired != 0
            and self.config.adx_min > 0
        ):
            adx = calculate_adx(
                [float(c.high) for c in candles],
                [float(c.low) for c in candles],
                [float(c.close) for c in candles],
                period=self.config.adx_period,
            )
            if adx is None or adx <= self.config.adx_min:
                desired = 0

        if desired == previous:
            return None
        # Переход зафиксирован; повторных сигналов не будет, пока режим
        # не сменится снова (в движке это флип/выход, а не накопление).
        self._direction = desired

        # Выход в «вне рынка» — сигнал CLOSE (flat).
        if desired == 0:
            logger.info(
                "ts_momentum %s: flat (momentum=%.3f%%)", symbol, momentum * 100
            )
            return Signal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type=SignalType.MOMENTUM,
                direction=models.TradeDirection.LONG,  # заполнитель, не открывается
                entry_price=Decimal("0"),
                stop_loss=Decimal("0"),
                take_profit=Decimal("0"),
                confidence=0.0,
                market_regime=market_regime or "UNKNOWN",
                features={
                    "tsm_action": TSM_ACTION_FLAT,
                    "tsm_momentum": round(momentum, 4),
                    "tsm_from": float(previous),
                    "tsm_to": 0.0,
                },
            )

        atr = calculate_atr(
            [float(c.high) for c in candles],
            [float(c.low) for c in candles],
            [float(c.close) for c in candles],
            period=self.config.atr_period,
        ) or (price * 0.01)
        entry = Decimal(str(price))
        stop_dist = Decimal(str(atr)) * Decimal(str(self.config.atr_stop_mult))
        stop = entry - stop_dist if desired == 1 else entry + stop_dist
        take = Decimal("0")  # без тейков: выход по смене режима или стопу

        direction = (
            models.TradeDirection.LONG
            if desired == 1
            else models.TradeDirection.SHORT
        )
        logger.info(
            "ts_momentum %s: flip -> %s (momentum=%.3f%% за %d баров)",
            symbol, direction.value, momentum * 100, need,
        )
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MOMENTUM,
            direction=direction,
            entry_price=entry,
            stop_loss=stop,
            take_profit=take,
            position_size=Decimal("0"),
            risk_amount=Decimal("0"),
            confidence=min(0.9, 0.5 + abs(momentum)),
            market_regime=market_regime or "UNKNOWN",
            features={
                "tsm_action": TSM_ACTION_FLIP,
                "tsm_momentum": round(momentum, 4),
                "tsm_from": float(previous),
                "tsm_to": float(desired),
                "no_take_profit": 1.0,
            },
        )

    # -------------------------------------------------------------- helpers
    def get_required_candles(self) -> int:
        """Сколько свечей нужно движку передавать стратегии.

        Бэктестер режет окно до этого числа; считаем по preferred_timeframe,
        чтобы окно импульса помещалось целиком (4h → 45д × 6 баров).
        """
        hint = _BARS_PER_DAY_HINT.get(self.config.preferred_timeframe)
        if hint:
            return int(self.config.lookback_days * hint) + 2
        return 2000

    def _lookback_bars(self, candles: list[models.Candle]) -> int:
        """Число баров окна импульса для текущего таймфрейма."""
        bpd = _bars_per_day(candles)
        if bpd <= 0:
            return 0
        return max(round(self.config.lookback_days * bpd), 1)

    def _desired_direction(self, momentum: float) -> int:
        """Целевой режим по импульсу.

        Мёртвая зона (±band) держит текущий режим (как ffill в
        исследовательском харнессе) — иначе правило дёргалось бы
        входами/выходами на каждом касании нуля. Flat случается только
        в long-only режиме при импульсе ниже −band (или до первого входа).
        """
        if momentum > self.config.band:
            return 1
        if momentum < -self.config.band:
            return -1 if self.config.allow_short else 0
        return self._direction

    # ------------------------------------------------------- BaseStrategy API
    def calculate_stop_loss(self, entry_price, candles, atr=None):
        atr_val = atr or calculate_atr(
            [float(c.high) for c in candles],
            [float(c.low) for c in candles],
            [float(c.close) for c in candles],
            period=self.config.atr_period,
        ) or (float(entry_price) * 0.01)
        dist = atr_val * self.config.atr_stop_mult
        if self._direction >= 1:
            return entry_price - Decimal(str(dist))
        return entry_price + Decimal(str(dist))

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        return []  # без тейк-профитов: выход по смене режима
