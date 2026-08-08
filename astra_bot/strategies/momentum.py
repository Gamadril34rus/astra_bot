"""
ASTRA BOT — Momentum Strategy
Трендовая/импульсная стратегия
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional
import numpy as np

from ..core import models
from ..core.utils import (
    calculate_atr,
    calculate_rsi,
    simple_moving_average,
    exponential_moving_average,
    calculate_bollinger_bands,
)
from .base import BaseStrategy, StrategyConfig, Signal, SignalType

logger = logging.getLogger(__name__)


@dataclass
class MomentumConfig(StrategyConfig):
    """Конфигурация momentum стратегии"""
    name: str = "momentum"
    
    # EMA параметры
    ema_short: int = 20
    ema_medium: int = 50
    ema_long: int = 200
    
    # ATR параметры
    atr_period: int = 14
    atr_stop_multiplier: float = 1.5
    
    # Volume подтверждение
    volume_ratio_threshold: float = 1.5  # Объём должен быть выше SMA на 50%
    
    # Trend strength
    min_adx: float = 20  # Минимальный ADX для тренда
    
    # Входные параметры
    min_risk_reward: float = 1.5  # Минимальное соотношение R:R
    max_risk_per_trade: float = 0.005  # Максимальный риск 0.5%
    
    # Фильтры
    require_volume_confirmation: bool = True
    require_trend_alignment: bool = True
    require_breakout: bool = False
    
    # Take profit
    tp_levels: List[float] = field(default_factory=lambda: [1.0, 2.0, 3.0])  # 1R, 2R, 3R
    
    # Параметры для backtest
    lookback_period: int = 200


class MomentumStrategy(BaseStrategy):
    """
    Momentum / Trend Following стратегия.
    
    Логика:
    - EMA20 > EMA50 > EMA200 для LONG
    - Подтверждение объёмом
    - Валидный брейк아웃
    - Приемлемая волатильность
    - Совместимость с режимом рынка
    """
    
    def __init__(self, config: MomentumConfig = None):
        if config is None:
            config = MomentumConfig()
        super().__init__(config)
        
        self.config = config
    
    async def evaluate(
        self,
        symbol: str,
        candles: List[models.Candle],
        orderbook: Optional[models.OrderBook] = None,
        current_price: Optional[float] = None,
        market_regime: Optional[str] = None,
    ) -> Optional[Signal]:
        """
        Оценить возможность momentum сделки.
        """
        if self.should_skip_signal():
            return None
        
        # Проверка количества свечей
        if len(candles) < self.config.lookback_period:
            logger.debug(f"{symbol}: Insufficient candles for momentum")
            return None
        
        # Проверка совместимости с режимом
        if market_regime:
            compatibility = self.get_regime_compatibility(market_regime)
            if compatibility == "OFF":
                return None
        
        # Расчёт индикаторов
        closes = [float(c.close) for c in candles]
        volumes = [float(c.volume) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        
        current_price = current_price or float(candles[-1].close)
        
        # EMA
        ema_short = exponential_moving_average(closes[-self.config.ema_short:], 
                                                self.config.ema_short)
        ema_medium = exponential_moving_average(closes[-self.config.ema_medium:], 
                                                self.config.ema_medium)
        ema_long = exponential_moving_average(closes[-self.config.ema_long:], 
                                               self.config.ema_long)
        
        if ema_short is None or ema_medium is None or ema_long is None:
            return None
        
        # ATR
        atr = calculate_atr(highs[-self.config.atr_period:], 
                           lows[-self.config.atr_period:], 
                           closes[-self.config.atr_period:], 
                           self.config.atr_period)
        
        # RSI
        rsi = calculate_rsi(closes)
        
        # Volume анализ
        avg_volume = simple_moving_average(volumes[-20:], 20) if len(volumes) >= 20 else 0
        volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 1.0
        
        # Проверка условий входа
        long_conditions = []
        short_conditions = []
        
        # Trend alignment
        if self.config.require_trend_alignment:
            if ema_short > ema_medium > ema_long:
                long_conditions.append("ema_aligned")
            elif ema_short < ema_medium < ema_long:
                short_conditions.append("ema_aligned")
        
        # Volume confirmation
        if self.config.require_volume_confirmation:
            if volume_ratio >= self.config.volume_ratio_threshold:
                long_conditions.append("volume_confirmed")
                short_conditions.append("volume_confirmed")
        
        # RSI filter (не перекуплен/перепродан)
        if rsi and not (rsi > 70 or rsi < 30):
            long_conditions.append("rsi_ok")
            short_conditions.append("rsi_ok")
        
        # Решение
        direction = None
        if len(long_conditions) >= 2 and closes[-1] > ema_short:
            direction = models.TradeDirection.LONG
        elif len(short_conditions) >= 2 and closes[-1] < ema_short:
            direction = models.TradeDirection.SHORT
        
        if direction is None:
            return None
        
        # Расчёт цен
        entry_price = Decimal(str(current_price))
        stop_loss = self.calculate_stop_loss(entry_price, candles, atr)
        tp_levels = self.calculate_take_profit(entry_price, stop_loss, candles)
        
        if not tp_levels:
            return None
        
        # Выбор основного TP
        take_profit = Decimal(str(tp_levels[0]["price"]))
        
        # Расчёт R:R
        risk = abs(float(entry_price - stop_loss))
        reward = abs(float(take_profit - entry_price))
        rr_ratio = reward / risk if risk > 0 else 0
        
        if rr_ratio < self.config.min_risk_reward:
            logger.debug(f"{symbol}: R:R {rr_ratio:.2f} below threshold {self.config.min_risk_reward}")
            return None
        
        # Confidence расчёт
        confidence = self._calculate_confidence(
            ema_short, ema_medium, ema_long,
            volume_ratio, rsi, atr
        )
        
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MOMENTUM,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=Decimal("0"),  # Рассчитывается risk engine
            risk_amount=Decimal("0"),
            confidence=confidence,
            market_regime=market_regime or "UNKNOWN",
            features={
                "ema_short": ema_short,
                "ema_medium": ema_medium,
                "ema_long": ema_long,
                "atr": atr or 0,
                "rsi": rsi or 50,
                "volume_ratio": volume_ratio,
                "price": current_price,
            },
        )
    
    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        candles: List[models.Candle],
        atr: Optional[float] = None,
    ) -> Decimal:
        """Рассчитать стоп-лосс на основе ATR"""
        if atr is None:
            highs = [float(c.high) for c in candles[-self.config.atr_period:]]
            lows = [float(c.low) for c in candles[-self.config.atr_period:]]
            closes = [float(c.close) for c in candles[-self.config.atr_period:]]
            atr = calculate_atr(highs, lows, closes, self.config.atr_period)
        
        if atr is None or atr <= 0:
            # Fallback: фиксированный процент
            return entry_price * Decimal("0.98")  # 2% стоп
        
        stop_distance = atr * self.config.atr_stop_multiplier
        # Для LONG стоп ниже входа
        return entry_price - Decimal(str(stop_distance))
    
    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        candles: List[models.Candle],
    ) -> List[dict]:
        """Рассчитать уровни тейк-профита"""
        risk = abs(float(entry_price - stop_loss))
        
        levels = []
        for tp_mult in self.config.tp_levels:
            tp_price = float(entry_price) + risk * tp_mult
            levels.append({
                "level": len(levels) + 1,
                "price": Decimal(str(tp_price)),
                "r_multiple": tp_mult,
            })
        
        return levels
    
    def _calculate_confidence(
        self,
        ema_short: float,
        ema_medium: float,
        ema_long: float,
        volume_ratio: float,
        rsi: Optional[float],
        atr: Optional[float],
    ) -> float:
        """Рассчитать уверенность сигнала"""
        confidence = 0.5  # Базовая
        
        # Trend strength
        if ema_short > ema_medium > ema_long:
            confidence += 0.2
        elif ema_short > ema_medium:
            confidence += 0.1
        
        # Volume confirmation
        if volume_ratio > 2.0:
            confidence += 0.15
        elif volume_ratio > 1.5:
            confidence += 0.1
        
        # RSI in neutral zone
        if rsi and 40 < rsi < 60:
            confidence += 0.05
        
        # Volatility consideration
        if atr and atr > 0:
            # Умеренная волатильность предпочтительна
            pass
        
        return min(0.95, confidence)
    
    def get_required_candles(self) -> int:
        """Минимальное количество свечей для работы"""
        return max(
            self.config.ema_long,
            self.config.lookback_period,
        )


# Фабрика
def create_momentum_strategy(config: dict = None) -> MomentumStrategy:
    """Создать momentum стратегию"""
    if config:
        cfg = MomentumConfig(**config)
    else:
        cfg = MomentumConfig()
    return MomentumStrategy(cfg)
