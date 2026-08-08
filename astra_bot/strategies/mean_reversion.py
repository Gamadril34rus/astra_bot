"""
ASTRA BOT — Mean Reversion Strategy
Стратегия возвращения к среднему
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from ..core import models
from ..core.utils import (
    calculate_rsi,
    calculate_bollinger_bands,
    simple_moving_average,
)
from .base import BaseStrategy, StrategyConfig, Signal, SignalType

logger = logging.getLogger(__name__)


@dataclass
class MeanReversionConfig(StrategyConfig):
    """Конфигурация mean reversion стратегии"""
    name: str = "mean_reversion"
    
    # Bollinger Bands
    bb_period: int = 20
    bb_std_dev: float = 2.0
    
    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 30
    rsi_overbought: float = 70
    
    # Z-score
    zscore_threshold: float = 2.0  # Вход при |z| > 2
    
    # Волатильность
    min_atr_percent: float = 0.5  # Минимальная волатильность
    max_atr_percent: float = 5.0  # Максимальная волатильность
    
    # Средние
    sma_period: int = 50
    
    # Entry
    require_bb_touch: bool = True  # Требуется касание BB
    require_rsi_confirm: bool = True  # Требуется подтверждение RSI
    
    # Exit
    tp_at_mean: bool = True  # Тейк-профит на средней
    tp_zscore: float = 0.5  # Выход при z-score < 0.5


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion стратегия.
    
    Работает в диапазоне (RANGE):
    - Bollinger Bands bounce
    - RSI mean reversion
    - Z-score reversion
    
    Отключается при тренде.
    """
    
    def __init__(self, config: MeanReversionConfig = None):
        if config is None:
            config = MeanReversionConfig()
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
        """Оценить возможность mean reversion сделки"""
        if self.should_skip_signal():
            return None
        
        # Требуется много свечей для расчёта
        if len(candles) < self.config.bb_period + 20:
            return None
        
        # Проверка совместимости с режимом
        if market_regime:
            compatibility = self.get_regime_compatibility(market_regime)
            if compatibility == "OFF":
                logger.debug(f"{symbol}: Mean reversion OFF in {market_regime}")
                return None
            if compatibility == "REDUCED":
                # Можно торговать но с осторожностью
                pass
        
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        
        current_price = current_price or closes[-1]
        
        # Bollinger Bands
        bb = calculate_bollinger_bands(closes, self.config.bb_period, self.config.bb_std_dev)
        if bb is None:
            return None
        
        # RSI
        rsi = calculate_rsi(closes[-self.config.rsi_period:], self.config.rsi_period)
        
        # Z-score
        sma_mean = simple_moving_average(closes[-self.config.sma_period:], 
                                         self.config.sma_period)
        if sma_mean is None:
            return None
        
        # Расчёт стандартного отклонения
        recent_closes = closes[-self.config.bb_period:]
        std_dev = np.std(recent_closes) if len(recent_closes) >= 2 else 0
        z_score = (current_price - bb["middle"]) / std_dev if std_dev > 0 else 0
        
        # Проверка волатильности
        if len(highs) >= 15 and len(lows) >= 15:
            from ..core.utils import calculate_atr
            atr = calculate_atr(highs[-15:], lows[-15:], closes[-15:], 14)
            atr_percent = (atr / current_price * 100) if current_price > 0 else 0
        else:
            atr_percent = 0
        
        if atr_percent < self.config.min_atr_percent or atr_percent > self.config.max_atr_percent:
            return None
        
        # Определение направления
        direction = None
        
        # LONG: цена у нижней BB + RSI oversold + низкий z-score
        if current_price <= bb["lower"] or z_score < -self.config.zscore_threshold:
            if not self.config.require_bb_touch and z_score < -self.config.zscore_threshold:
                pass
            elif self.config.require_bb_touch and current_price <= bb["lower"]:
                pass
            else:
                return None
            
            if self.config.require_rsi_confirm and rsi and rsi < self.config.rsi_oversold:
                direction = models.TradeDirection.LONG
            elif not self.config.require_rsi_confirm:
                direction = models.TradeDirection.LONG
        
        # SHORT: цена у верхней BB + RSI overbought + высокий z-score
        elif current_price >= bb["upper"] or z_score > self.config.zscore_threshold:
            if not self.config.require_bb_touch and z_score > self.config.zscore_threshold:
                pass
            elif self.config.require_bb_touch and current_price >= bb["upper"]:
                pass
            else:
                return None
            
            if self.config.require_rsi_confirm and rsi and rsi > self.config.rsi_overbought:
                direction = models.TradeDirection.SHORT
            elif not self.config.require_rsi_confirm:
                direction = models.TradeDirection.SHORT
        
        if direction is None:
            return None
        
        # Расчёт цен
        entry_price = Decimal(str(current_price))
        stop_loss = self.calculate_stop_loss(entry_price, candles)
        tp_levels = self.calculate_take_profit(entry_price, stop_loss, candles, z_score)
        
        if not tp_levels:
            return None
        
        take_profit = tp_levels[0]["price"] if tp_levels else entry_price
        
        # Confidence
        confidence = self._calculate_confidence(z_score, rsi, atr_percent)
        
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MEAN_REVERSION,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=Decimal("0"),
            risk_amount=Decimal("0"),
            confidence=confidence,
            market_regime=market_regime or "UNKNOWN",
            features={
                "bb_middle": float(bb["middle"]),
                "bb_upper": float(bb["upper"]),
                "bb_lower": float(bb["lower"]),
                "bb_width": float(bb["bandwidth"]),
                "z_score": z_score,
                "rsi": rsi or 50,
                "atr_percent": atr_percent,
            },
        )
    
    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        candles: List[models.Candle],
        atr: float = None,
    ) -> Decimal:
        """Рассчитать стоп-лосс"""
        # Для mean reversion стоп за пределами BB
        return entry_price * Decimal("1.02")  # 2% стоп
    
    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        candles: List[models.Candle],
        z_score: float = 0,
    ) -> List[dict]:
        """Рассчитать уровни тейк-профита"""
        levels = []
        
        # TP1: Вход в зону neutral (z-score < 0.5)
        risk = abs(float(entry_price - stop_loss))
        
        if z_score < -1.5:  # LONG
            tp_price = float(entry_price) + risk * 1.0  # 1R
            levels.append({
                "level": 1,
                "price": Decimal(str(tp_price)),
                "r_multiple": 1.0,
            })
        elif z_score > 1.5:  # SHORT
            tp_price = float(entry_price) - risk * 1.0
            levels.append({
                "level": 1,
                "price": Decimal(str(tp_price)),
                "r_multiple": 1.0,
            })
        
        # TP2: Возврат к среднему
        closes = [float(c.close) for c in candles[-self.config.bb_period:]]
        sma = simple_moving_average(closes, self.config.bb_period)
        if sma:
            if z_score < 0:  # LONG
                levels.append({
                    "level": 2,
                    "price": Decimal(str(sma)),
                    "r_multiple": abs(float(sma - entry_price)) / risk if risk > 0 else 1.0,
                })
            else:  # SHORT
                levels.append({
                    "level": 2,
                    "price": Decimal(str(sma)),
                    "r_multiple": abs(float(sma - entry_price)) / risk if risk > 0 else 1.0,
                })
        
        return levels
    
    def _calculate_confidence(
        self,
        z_score: float,
        rsi: Optional[float],
        atr_percent: float,
    ) -> float:
        """Рассчитать уверенность"""
        confidence = 0.4  # Низкая базовая
        
        # Z-score strength
        abs_z = abs(z_score)
        if abs_z > 3.0:
            confidence += 0.3
        elif abs_z > 2.5:
            confidence += 0.2
        elif abs_z > 2.0:
            confidence += 0.1
        
        # RSI confirmation
        if rsi:
            if abs_z > 2.5:
                if (z_score < 0 and rsi < 30) or (z_score > 0 and rsi > 70):
                    confidence += 0.15
            elif abs_z > 2.0:
                if (z_score < 0 and rsi < 35) or (z_score > 0 and rsi > 65):
                    confidence += 0.1
        
        # Volatility consideration
        if 0.5 < atr_percent < 2.0:
            confidence += 0.05  # Оптимальная волатильность
        
        return min(0.9, confidence)
    
    def get_required_candles(self) -> int:
        return self.config.bb_period + 20


# Для использования numpy в типизации
import numpy as np


# Фабрика
def create_mean_reversion_strategy(config: dict = None) -> MeanReversionStrategy:
    """Создать mean reversion стратегию"""
    if config:
        cfg = MeanReversionConfig(**config)
    else:
        cfg = MeanReversionConfig()
    return MeanReversionStrategy(cfg)
