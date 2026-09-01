"""
ASTRA BOT - Position Sizing Engine

Движок расчёта размера позиции (ТЗ Пункты 18, 20, 24, 32, 39-40, 53, 59, 73-74, 86, 92)

Рассчитывает:
- position size
- based on risk per trade
- based on volatility
- based on account size
- based on correlation
- based on liquidity
- based on confidence
- based on drawdown limits

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class SizingMethod(str, Enum):
    """Методы расчёта размера позиции"""
    FIXED = "fixed"  # Фиксированный размер
    RISK_BASED = "risk_based"  # На основе риска
    VOLATILITY_BASED = "volatility_based"  # На основе волатильности
    ACCOUNT_BASED = "account_based"  # На основе размера счёта
    CORRELATION_BASED = "correlation_based"  # На основе корреляции
    LIQUIDITY_BASED = "liquidity_based"  # На основе ликвидности
    CONFIDENCE_BASED = "confidence_based"  # На основе уверенности
    DRAWDOWN_BASED = "drawdown_based"  # На основе лимита просадки
    KELLY = "kelly"  # Формула Келли
    COMPOUNDING = "compounding"  # С учётом реинвестирования


class RiskLevel(str, Enum):
    """Уровни риска"""
    CONSERVATIVE = "conservative"  # Консервативный
    MODERATE = "moderate"  # Умеренный
    AGGRESSIVE = "aggressive"  # Агрессивный


@dataclass
class PositionSize:
    """Размер позиции"""
    symbol: str
    method: SizingMethod
    
    # Расчёт
    quantity: float = 0.0  # Количество (акции, контракты и т.д.)
    notional: float = 0.0  # Номинальная стоимость
    percentage: float = 0.0  # Процент от счёта
    
    # Параметры
    entry_price: float = 0.0
    stop_loss: float = 0.0
    risk_amount: float = 0.0
    risk_percentage: float = 0.0
    
    # Ограничения
    max_position_size: float = 0.0
    max_notional: float = 0.0
    max_percentage: float = 0.0
    
    # Уверенность
    confidence: float = 0.0
    
    # Временная метка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "method": self.method.value,
            "quantity": self.quantity,
            "notional": self.notional,
            "percentage": self.percentage,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "risk_amount": self.risk_amount,
            "risk_percentage": self.risk_percentage,
            "max_position_size": self.max_position_size,
            "max_notional": self.max_notional,
            "max_percentage": self.max_percentage,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SizingParameters:
    """Параметры расчёта размера позиции"""
    account_size: float = 10000.0  # Размер счёта
    risk_per_trade: float = 1.0  # Риск на сделку в %
    max_drawdown: float = 10.0  # Максимальная просадка в %
    max_position_size: float = 0.1  # Максимальный размер позиции в % от счёта
    max_leverage: float = 1.0  # Максимальный плечо
    
    # Волатильность
    atr: float = 0.0
    volatility_pct: float = 0.0
    
    # Корреляции
    correlations: dict[str, float] = field(default_factory=dict)
    
    # Ликвидность
    avg_daily_volume: float = 0.0
    bid_ask_spread: float = 0.0
    
    # Уверенность
    confidence: float = 0.5
    
    # Временной горизонт
    time_horizon: str = "1h"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "account_size": self.account_size,
            "risk_per_trade": self.risk_per_trade,
            "max_drawdown": self.max_drawdown,
            "max_position_size": self.max_position_size,
            "max_leverage": self.max_leverage,
            "atr": self.atr,
            "volatility_pct": self.volatility_pct,
            "correlations": self.correlations,
            "avg_daily_volume": self.avg_daily_volume,
            "bid_ask_spread": self.bid_ask_spread,
            "confidence": self.confidence,
            "time_horizon": self.time_horizon,
        }


@dataclass
class PositionSizingResult:
    """Результат расчёта размера позиции"""
    symbol: str
    parameters: SizingParameters
    
    # Размеры по разным методам
    sizes: dict[SizingMethod, PositionSize] = field(default_factory=dict)
    
    # Итоговый размер
    final_size: PositionSize | None = None
    
    # Ограничения
    constraints: list[str] = field(default_factory=list)
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    # Временная метка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "symbol": self.symbol,
            "final_size": self.final_size.to_dict() if self.final_size else None,
            "parameters": self.parameters.to_dict(),
            "constraints": self.constraints,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat(),
        }
        
        result["sizes"] = {m.value: s.to_dict() for m, s in self.sizes.items()}
        
        return result


class PositionSizingEngine:
    """
    Движок расчёта размера позиции.
    
    Рассчитывает оптимальный размер позиции на основе различных методов.
    """
    
    def __init__(self):
        # Пороги
        self.thresholds = {
            "min_account_size": 1000.0,
            "min_risk_per_trade": 0.1,  # 0.1%
            "max_risk_per_trade": 5.0,  # 5%
            "min_confidence": 0.1,
            "max_confidence": 1.0,
            "min_liquidity": 10000.0,  # Минимальная ликвидность
            "max_spread_pct": 0.01,  # 1%
        }
        
        # История расчётов
        self._results: dict[str, PositionSizingResult] = {}
    
    def calculate_fixed_size(
        self,
        symbol: str,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать фиксированный размер позиции.
        
        Args:
            symbol: Символ
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        # Фиксированный процент от счёта
        percentage = parameters.max_position_size
        notional = parameters.account_size * percentage / 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.FIXED,
            quantity=0.0,  # Нужна цена для расчёта количества
            notional=notional,
            percentage=percentage,
            confidence=parameters.confidence,
        )
    
    def calculate_risk_based_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции на основе риска.
        
        Args:
            symbol: Символ
            entry_price: Цена входа
            stop_loss: Стоп-лосс
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        if entry_price <= 0 or stop_loss <= 0:
            return PositionSize(
                symbol=symbol,
                method=SizingMethod.RISK_BASED,
                confidence=0.0,
            )
        
        # Рассчитать риск на сделку в деньгах
        risk_amount = parameters.account_size * parameters.risk_per_trade / 100
        
        # Рассчитать расстояние до стоп-лосса
        distance = abs(entry_price - stop_loss)
        
        if distance <= 0:
            return PositionSize(
                symbol=symbol,
                method=SizingMethod.RISK_BASED,
                confidence=0.0,
            )
        
        # Рассчитать количество
        quantity = risk_amount / distance
        notional = quantity * entry_price
        percentage = notional / parameters.account_size * 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.RISK_BASED,
            quantity=quantity,
            notional=notional,
            percentage=percentage,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_amount=risk_amount,
            risk_percentage=parameters.risk_per_trade,
            confidence=parameters.confidence,
        )
    
    def calculate_volatility_based_size(
        self,
        symbol: str,
        entry_price: float,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции на основе волатильности.
        
        Args:
            symbol: Символ
            entry_price: Цена входа
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        if entry_price <= 0:
            return PositionSize(
                symbol=symbol,
                method=SizingMethod.VOLATILITY_BASED,
                confidence=0.0,
            )
        
        # Использовать ATR или волатильность
        volatility = parameters.atr if parameters.atr > 0 else (entry_price * parameters.volatility_pct / 100)
        
        if volatility <= 0:
            volatility = entry_price * 0.01  # 1% по умолчанию
        
        # Рассчитать риск на сделку
        risk_amount = parameters.account_size * parameters.risk_per_trade / 100
        
        # Рассчитать количество
        quantity = risk_amount / volatility
        notional = quantity * entry_price
        percentage = notional / parameters.account_size * 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.VOLATILITY_BASED,
            quantity=quantity,
            notional=notional,
            percentage=percentage,
            entry_price=entry_price,
            risk_amount=risk_amount,
            risk_percentage=parameters.risk_per_trade,
            confidence=parameters.confidence,
        )
    
    def calculate_account_based_size(
        self,
        symbol: str,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции на основе размера счёта.
        
        Args:
            symbol: Символ
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        # Процент от счёта
        percentage = min(parameters.max_position_size, 100.0)
        notional = parameters.account_size * percentage / 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.ACCOUNT_BASED,
            notional=notional,
            percentage=percentage,
            max_percentage=parameters.max_position_size,
            confidence=parameters.confidence,
        )
    
    def calculate_correlation_based_size(
        self,
        symbol: str,
        portfolio_symbols: list[str],
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции на основе корреляции с портфелем.
        
        Args:
            symbol: Символ
            portfolio_symbols: Символы в портфеле
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        if not portfolio_symbols:
            return self.calculate_fixed_size(symbol, parameters)
        
        # Рассчитать среднюю корреляцию
        correlations = [parameters.correlations.get(s, 0.0) for s in portfolio_symbols]
        avg_correlation = np.mean(correlations) if correlations else 0.0
        
        # Если высокая корреляция, уменьшить размер позиции
        if avg_correlation > 0.7:
            # Уменьшить размер на (correlation - 0.7) * 100%
            size_reduction = (avg_correlation - 0.7) * 100
            max_percentage = max(0, parameters.max_position_size - size_reduction)
        elif avg_correlation < -0.7:
            # Увеличить размер (диверсификация)
            size_increase = (0.7 + avg_correlation) * 50
            max_percentage = min(100, parameters.max_position_size + size_increase)
        else:
            max_percentage = parameters.max_position_size
        
        notional = parameters.account_size * max_percentage / 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.CORRELATION_BASED,
            notional=notional,
            percentage=max_percentage,
            max_percentage=parameters.max_position_size,
            confidence=parameters.confidence * (1 - abs(avg_correlation)),
        )
    
    def calculate_liquidity_based_size(
        self,
        symbol: str,
        entry_price: float,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции на основе ликвидности.
        
        Args:
            symbol: Символ
            entry_price: Цена входа
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        if entry_price <= 0:
            return PositionSize(
                symbol=symbol,
                method=SizingMethod.LIQUIDITY_BASED,
                confidence=0.0,
            )
        
        # Рассчитать максимальный размер позиции на основе ликвидности
        # Не более X% от среднего дневного объёма
        if parameters.avg_daily_volume > 0:
            max_position_notional = parameters.avg_daily_volume * 0.01  # 1% от ADV
        else:
            max_position_notional = parameters.account_size * parameters.max_position_size / 100
        
        # Учесть спред
        if parameters.bid_ask_spread > 0:
            spread_factor = 1 - (parameters.bid_ask_spread / 100)
            max_position_notional *= spread_factor
        
        # Ограничить размером счёта
        max_position_notional = min(max_position_notional, parameters.account_size)
        
        notional = max_position_notional
        percentage = notional / parameters.account_size * 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.LIQUIDITY_BASED,
            notional=notional,
            percentage=percentage,
            max_notional=max_position_notional,
            confidence=min(1.0, parameters.confidence * (1 + parameters.avg_daily_volume / 1000000)),
        )
    
    def calculate_confidence_based_size(
        self,
        symbol: str,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции на основе уверенности.
        
        Args:
            symbol: Символ
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        # Масштабировать размер по уверенности
        confidence_factor = parameters.confidence
        max_percentage = parameters.max_position_size * confidence_factor
        notional = parameters.account_size * max_percentage / 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.CONFIDENCE_BASED,
            notional=notional,
            percentage=max_percentage,
            confidence=parameters.confidence,
        )
    
    def calculate_drawdown_based_size(
        self,
        symbol: str,
        current_drawdown: float,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции на основе текущей просадки.
        
        Args:
            symbol: Символ
            current_drawdown: Текущая просадка в %
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        # Если просадка близка к максимальной, уменьшить размер
        drawdown_ratio = current_drawdown / parameters.max_drawdown
        
        if drawdown_ratio >= 1.0:
            # Не открывать новые позиции
            return PositionSize(
                symbol=symbol,
                method=SizingMethod.DRAWDOWN_BASED,
                confidence=0.0,
            )
        
        # Уменьшить размер пропорционально
        size_reduction = drawdown_ratio * 100
        max_percentage = max(0, parameters.max_position_size - size_reduction)
        notional = parameters.account_size * max_percentage / 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.DRAWDOWN_BASED,
            notional=notional,
            percentage=max_percentage,
            confidence=parameters.confidence * (1 - drawdown_ratio),
        )
    
    def calculate_kelly_size(
        self,
        symbol: str,
        win_probability: float,
        win_loss_ratio: float,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции по формуле Келли.
        
        Формула: f* = p - (1-p)/b
        где p - вероятность выигрыша, b - отношение выигрыша к проигрышу
        
        Args:
            symbol: Символ
            win_probability: Вероятность выигрыша
            win_loss_ratio: Отношение выигрыша к проигрышу
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        if win_probability <= 0 or win_loss_ratio <= 0:
            return PositionSize(
                symbol=symbol,
                method=SizingMethod.KELLY,
                confidence=0.0,
            )
        
        # Формула Келли
        f_star = win_probability - (1 - win_probability) / win_loss_ratio
        
        # Обычно используют половину от f*
        position_fraction = f_star / 2
        
        # Ограничить
        position_fraction = max(0, min(position_fraction, parameters.max_position_size / 100))
        
        notional = parameters.account_size * position_fraction
        percentage = position_fraction * 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.KELLY,
            notional=notional,
            percentage=percentage,
            confidence=min(1.0, win_probability * 2),
        )
    
    def calculate_compounding_size(
        self,
        symbol: str,
        current_equity: float,
        initial_equity: float,
        parameters: SizingParameters,
    ) -> PositionSize:
        """
        Рассчитать размер позиции с учётом реинвестирования.
        
        Args:
            symbol: Символ
            current_equity: Текущий капитал
            initial_equity: Начальный капитал
            parameters: Параметры
        
        Returns:
            Размер позиции
        """
        if initial_equity <= 0:
            return PositionSize(
                symbol=symbol,
                method=SizingMethod.COMPOUNDING,
                confidence=0.0,
            )
        
        # Рассчитать коэффициент роста
        growth_factor = current_equity / initial_equity
        
        # Увеличить размер позиции пропорционально росту
        base_percentage = parameters.max_position_size
        adjusted_percentage = min(100, base_percentage * growth_factor)
        
        notional = current_equity * adjusted_percentage / 100
        
        return PositionSize(
            symbol=symbol,
            method=SizingMethod.COMPOUNDING,
            notional=notional,
            percentage=adjusted_percentage,
            confidence=parameters.confidence,
        )
    
    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float | None = None,
        parameters: SizingParameters | None = None,
        portfolio_symbols: list[str] | None = None,
        current_drawdown: float = 0.0,
        win_probability: float | None = None,
        win_loss_ratio: float | None = None,
        current_equity: float | None = None,
        initial_equity: float | None = None,
    ) -> PositionSizingResult:
        """
        Рассчитать размер позиции.
        
        Args:
            symbol: Символ
            entry_price: Цена входа
            stop_loss: Стоп-лосс
            parameters: Параметры
            portfolio_symbols: Символы в портфеле
            current_drawdown: Текущая просадка
            win_probability: Вероятность выигрыша
            win_loss_ratio: Отношение выигрыша к проигрышу
            current_equity: Текущий капитал
            initial_equity: Начальный капитал
        
        Returns:
            Результат расчёта
        """
        if parameters is None:
            parameters = SizingParameters()
        
        sizes = {}
        
        # Рассчитать размеры по всем методам
        sizes[SizingMethod.FIXED] = self.calculate_fixed_size(symbol, parameters)
        
        if stop_loss is not None:
            sizes[SizingMethod.RISK_BASED] = self.calculate_risk_based_size(
                symbol, entry_price, stop_loss, parameters
            )
        
        sizes[SizingMethod.VOLATILITY_BASED] = self.calculate_volatility_based_size(
            symbol, entry_price, parameters
        )
        
        sizes[SizingMethod.ACCOUNT_BASED] = self.calculate_account_based_size(
            symbol, parameters
        )
        
        if portfolio_symbols:
            sizes[SizingMethod.CORRELATION_BASED] = self.calculate_correlation_based_size(
                symbol, portfolio_symbols, parameters
            )
        
        sizes[SizingMethod.LIQUIDITY_BASED] = self.calculate_liquidity_based_size(
            symbol, entry_price, parameters
        )
        
        sizes[SizingMethod.CONFIDENCE_BASED] = self.calculate_confidence_based_size(
            symbol, parameters
        )
        
        sizes[SizingMethod.DRAWDOWN_BASED] = self.calculate_drawdown_based_size(
            symbol, current_drawdown, parameters
        )
        
        if win_probability is not None and win_loss_ratio is not None:
            sizes[SizingMethod.KELLY] = self.calculate_kelly_size(
                symbol, win_probability, win_loss_ratio, parameters
            )
        
        if current_equity is not None and initial_equity is not None:
            sizes[SizingMethod.COMPOUNDING] = self.calculate_compounding_size(
                symbol, current_equity, initial_equity, parameters
            )
        
        # Определить итоговый размер (минимальный из всех методов)
        # Это консервативный подход - брать минимальный размер
        valid_sizes = [s for s in sizes.values() if s.notional > 0]
        
        if valid_sizes:
            # Найти размер с минимальной номинальной стоимостью
            final_size = min(valid_sizes, key=lambda x: x.notional)
            
            # Учесть ограничения
            constraints = []
            if final_size.percentage > parameters.max_position_size:
                constraints.append(f"Position size exceeds max {parameters.max_position_size}%")
                final_size = PositionSize(
                    symbol=symbol,
                    method=SizingMethod.RISK_BASED,
                    notional=parameters.account_size * parameters.max_position_size / 100,
                    percentage=parameters.max_position_size,
                )
        else:
            final_size = PositionSize(
                symbol=symbol,
                method=SizingMethod.FIXED,
                confidence=0.0,
            )
            constraints.append("No valid sizing method produced a positive size")
        
        # Создать рекомендации
        recommendations = []
        
        if final_size.confidence > 0.7:
            recommendations.append("High confidence - consider full position size")
        elif final_size.confidence > 0.5:
            recommendations.append("Moderate confidence - consider partial position size")
        else:
            recommendations.append("Low confidence - consider reducing position size or waiting")
        
        if constraints:
            recommendations.append(f"Constraints: {', '.join(constraints)}")
        
        result = PositionSizingResult(
            symbol=symbol,
            sizes=sizes,
            final_size=final_size,
            parameters=parameters,
            constraints=constraints,
            recommendations=recommendations,
        )
        
        # Сохранить результат
        result_id = f"{symbol}_{datetime.now(timezone.utc).isoformat()}"
        self._results[result_id] = result
        
        return result


# Глобальный экземпляр
_position_sizing_engine: PositionSizingEngine | None = None


def get_position_sizing_engine() -> PositionSizingEngine:
    """Получить глобальный Position Sizing Engine"""
    global _position_sizing_engine
    if _position_sizing_engine is None:
        _position_sizing_engine = PositionSizingEngine()
    return _position_sizing_engine


def reset_position_sizing_engine():
    """Сбросить Position Sizing Engine (для тестов)"""
    global _position_sizing_engine
    _position_sizing_engine = PositionSizingEngine()
