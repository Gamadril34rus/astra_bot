"""
ASTRA BOT - Portfolio Opportunity Allocator

Движок распределения возможностей портфеля
Приоритетное направление #4

Оптимальный набор сигналов с учётом:
- Корреляции рисков
- Ограниченного капитала
- Максимизации Sharpe ratio портфеля
- Risk-adjusted returns

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from collections import defaultdict

import numpy as np

logger = logging.getLogger(__name__)


class AllocationMethod(str, Enum):
    """Методы распределения"""
    EQUAL = "equal"  # Равное распределение
    RISK_PARITY = "risk_parity"  # Паритет риска
    SHARPE_MAXIMIZATION = "sharpe_maximization"  # Максимизация Sharpe
    CORRELATION_ADJUSTED = "correlation_adjusted"  # С учётом корреляции
    VOLATILITY_WEIGHTED = "volatility_weighted"  # Взвешенное по волатильности
    CONFIDENCE_WEIGHTED = "confidence_weighted"  # Взвешенное по уверенности
    RISK_ADJUSTED = "risk_adjusted"  # Взвешенное по риску


class SignalStatus(str, Enum):
    """Статусы сигналов"""
    SELECTED = "selected"  # Выбран
    REJECTED = "rejected"  # Отклонён
    PENDING = "pending"  # В ожидании


@dataclass
class OpportunitySignal:
    """Сигнал возможности"""
    signal_id: str
    symbol: str
    
    # Параметры сигнала
    direction: str = "neutral"  # long/short/neutral
    confidence: float = 0.0  # 0-1
    strength: float = 0.0  # 0-1
    
    # Ожидаемая доходность
    expected_return: float = 0.0  # %
    expected_return_std: float = 0.0  # Стандартное отклонение
    sharpe_ratio: float = 0.0
    
    # Риск
    risk: float = 0.0  # %
    max_drawdown: float = 0.0  # %
    
    # Временной горизонт
    time_horizon: str = "1h"
    
    # Время создания
    creation_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Статус
    status: SignalStatus = SignalStatus.PENDING
    
    # Аллокация
    allocated_weight: float = 0.0  # Выделенный вес
    allocated_capital: float = 0.0  # Выделенный капитал
    
    # Причина
    reason: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": self.confidence,
            "strength": self.strength,
            "expected_return": self.expected_return,
            "expected_return_std": self.expected_return_std,
            "sharpe_ratio": self.sharpe_ratio,
            "risk": self.risk,
            "max_drawdown": self.max_drawdown,
            "time_horizon": self.time_horizon,
            "creation_time": self.creation_time.isoformat(),
            "status": self.status.value,
            "allocated_weight": self.allocated_weight,
            "allocated_capital": self.allocated_capital,
            "reason": self.reason,
        }


@dataclass
class CorrelationMatrix:
    """Матрица корреляций"""
    symbols: list[str]
    matrix: list[list[float]] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "matrix": self.matrix,
        }
    
    def get_correlation(self, symbol1: str, symbol2: str) -> float:
        """Получить корреляцию между двумя символами"""
        if symbol1 not in self.symbols or symbol2 not in self.symbols:
            return 0.0
        
        idx1 = self.symbols.index(symbol1)
        idx2 = self.symbols.index(symbol2)
        
        return self.matrix[idx1][idx2]


@dataclass
class AllocationResult:
    """Результат распределения"""
    portfolio_id: str
    timestamp: datetime
    
    # Сигналы
    signals: list[OpportunitySignal] = field(default_factory=list)
    selected_signals: list[OpportunitySignal] = field(default_factory=list)
    rejected_signals: list[OpportunitySignal] = field(default_factory=list)
    
    # Аллокация
    total_capital: float = 0.0
    allocated_capital: float = 0.0
    unallocated_capital: float = 0.0
    
    # Метрики портфеля
    expected_portfolio_return: float = 0.0
    expected_portfolio_risk: float = 0.0
    portfolio_sharpe_ratio: float = 0.0
    portfolio_correlation: float = 0.0
    
    # Метод
    method: AllocationMethod = AllocationMethod.EQUAL
    
    # Уверенность
    confidence: float = 0.0
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "timestamp": self.timestamp.isoformat(),
            "total_signals": len(self.signals),
            "selected_signals": len(self.selected_signals),
            "rejected_signals": len(self.rejected_signals),
            "total_capital": self.total_capital,
            "allocated_capital": self.allocated_capital,
            "unallocated_capital": self.unallocated_capital,
            "expected_portfolio_return": self.expected_portfolio_return,
            "expected_portfolio_risk": self.expected_portfolio_risk,
            "portfolio_sharpe_ratio": self.portfolio_sharpe_ratio,
            "portfolio_correlation": self.portfolio_correlation,
            "method": self.method.value,
            "confidence": self.confidence,
            "recommendations": self.recommendations,
        }


@dataclass
class PortfolioAnalysis:
    """Полный анализ портфеля"""
    portfolio_id: str
    timestamp: datetime
    
    # Аллокации
    allocations: list[AllocationResult] = field(default_factory=list)
    
    # Метрики
    cumulative_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    
    # Корреляции
    correlation_matrix: CorrelationMatrix | None = None
    
    # Уверенность
    confidence: float = 0.0
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "portfolio_id": self.portfolio_id,
            "timestamp": self.timestamp.isoformat(),
            "allocations_count": len(self.allocations),
            "cumulative_return": self.cumulative_return,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "confidence": self.confidence,
            "recommendations": self.recommendations,
        }
        
        if self.correlation_matrix:
            result["correlation_matrix"] = self.correlation_matrix.to_dict()
        
        return result


class PortfolioOpportunityAllocator:
    """
    Движок распределения возможностей портфеля.
    
    Оптимальный набор сигналов с учётом корреляции рисков.
    """
    
    def __init__(self):
        # Сигналы
        self._signals: dict[str, OpportunitySignal] = {}
        
        # Аллокации
        self._allocations: dict[str, AllocationResult] = {}
        
        # Корреляции
        self._correlations: dict[str, CorrelationMatrix] = {}
        
        # Пороги
        self.thresholds = {
            "min_confidence": 0.3,
            "min_sharpe_ratio": 0.5,
            "max_risk_per_signal": 0.05,  # 5% риск на сигнал
            "max_portfolio_risk": 0.20,  # 20% риск портфеля
            "max_correlation": 0.7,  # Максимальная корреляция
            "min_expected_return": 0.005,  # 0.5% ожидаемый возврат
        }
    
    def add_signal(
        self,
        signal_id: str,
        symbol: str,
        direction: str,
        confidence: float,
        expected_return: float,
        expected_return_std: float,
        risk: float,
        time_horizon: str = "1h",
        **kwargs,
    ) -> OpportunitySignal:
        """
        Добавить сигнал.
        
        Args:
            signal_id: ID сигнала
            symbol: Символ
            direction: Направление (long/short/neutral)
            confidence: Уверенность (0-1)
            expected_return: Ожидаемый возврат (%)
            expected_return_std: Стандартное отклонение возврата
            risk: Риск (%)
            time_horizon: Временной горизонт
            **kwargs: Дополнительные параметры
        
        Returns:
            Сигнал
        """
        # Рассчитать Sharpe ratio
        sharpe_ratio = expected_return / expected_return_std if expected_return_std > 0 else 0.0
        
        signal = OpportunitySignal(
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            strength=kwargs.get("strength", confidence),
            expected_return=expected_return,
            expected_return_std=expected_return_std,
            sharpe_ratio=sharpe_ratio,
            risk=risk,
            max_drawdown=kwargs.get("max_drawdown", 0.0),
            time_horizon=time_horizon,
        )
        
        self._signals[signal_id] = signal
        
        return signal
    
    def set_correlation_matrix(
        self,
        portfolio_id: str,
        symbols: list[str],
        matrix: list[list[float]],
    ):
        """
        Установить матрицу корреляций.
        
        Args:
            portfolio_id: ID портфеля
            symbols: Символы
            matrix: Матрица корреляций
        """
        self._correlations[portfolio_id] = CorrelationMatrix(
            symbols=symbols,
            matrix=matrix,
        )
    
    def allocate_equal(
        self,
        portfolio_id: str,
        signals: list[OpportunitySignal],
        total_capital: float,
    ) -> AllocationResult:
        """
        Равное распределение.
        
        Args:
            portfolio_id: ID портфеля
            signals: Сигналы
            total_capital: Общий капитал
        
        Returns:
            Результат распределения
        """
        selected = []
        rejected = []
        
        # Отфильтровать сигналы
        for signal in signals:
            if signal.confidence >= self.thresholds["min_confidence"] and \
               signal.expected_return >= self.thresholds["min_expected_return"]:
                signal.status = SignalStatus.SELECTED
                selected.append(signal)
            else:
                signal.status = SignalStatus.REJECTED
                signal.reason = "Low confidence or expected return"
                rejected.append(signal)
        
        # Равное распределение
        if selected:
            weight_per_signal = 1.0 / len(selected)
            capital_per_signal = total_capital * weight_per_signal
            
            for signal in selected:
                signal.allocated_weight = weight_per_signal
                signal.allocated_capital = capital_per_signal
        
        # Рассчитать метрики портфеля
        portfolio_return = np.mean([s.expected_return for s in selected]) if selected else 0.0
        portfolio_risk = np.mean([s.risk for s in selected]) if selected else 0.0
        
        return AllocationResult(
            portfolio_id=portfolio_id,
            timestamp=datetime.now(timezone.utc),
            signals=signals,
            selected_signals=selected,
            rejected_signals=rejected,
            total_capital=total_capital,
            allocated_capital=total_capital if selected else 0.0,
            unallocated_capital=0.0 if selected else total_capital,
            expected_portfolio_return=portfolio_return,
            expected_portfolio_risk=portfolio_risk,
            portfolio_sharpe_ratio=portfolio_return / portfolio_risk if portfolio_risk > 0 else 0.0,
            method=AllocationMethod.EQUAL,
        )
    
    def allocate_risk_parity(
        self,
        portfolio_id: str,
        signals: list[OpportunitySignal],
        total_capital: float,
    ) -> AllocationResult:
        """
        Распределение по паритету риска.
        
        Args:
            portfolio_id: ID портфеля
            signals: Сигналы
            total_capital: Общий капитал
        
        Returns:
            Результат распределения
        """
        selected = []
        rejected = []
        
        # Отфильтровать сигналы
        for signal in signals:
            if signal.confidence >= self.thresholds["min_confidence"] and \
               signal.risk <= self.thresholds["max_risk_per_signal"]:
                signal.status = SignalStatus.SELECTED
                selected.append(signal)
            else:
                signal.status = SignalStatus.REJECTED
                signal.reason = "Low confidence or high risk"
                rejected.append(signal)
        
        # Распределение по паритету риска
        if selected:
            # Нормализовать риски
            total_risk = sum(s.risk for s in selected)
            if total_risk > 0:
                for signal in selected:
                    signal.allocated_weight = signal.risk / total_risk
                    signal.allocated_capital = total_capital * signal.allocated_weight
        
        # Рассчитать метрики портфеля
        portfolio_return = sum(s.expected_return * s.allocated_weight for s in selected)
        portfolio_risk = sum(s.risk * s.allocated_weight for s in selected)
        
        return AllocationResult(
            portfolio_id=portfolio_id,
            timestamp=datetime.now(timezone.utc),
            signals=signals,
            selected_signals=selected,
            rejected_signals=rejected,
            total_capital=total_capital,
            allocated_capital=total_capital if selected else 0.0,
            unallocated_capital=0.0 if selected else total_capital,
            expected_portfolio_return=portfolio_return,
            expected_portfolio_risk=portfolio_risk,
            portfolio_sharpe_ratio=portfolio_return / portfolio_risk if portfolio_risk > 0 else 0.0,
            method=AllocationMethod.RISK_PARITY,
        )
    
    def allocate_sharpe_maximization(
        self,
        portfolio_id: str,
        signals: list[OpportunitySignal],
        total_capital: float,
        correlation_matrix: CorrelationMatrix | None = None,
    ) -> AllocationResult:
        """
        Максимизация Sharpe ratio портфеля.
        
        Args:
            portfolio_id: ID портфеля
            signals: Сигналы
            total_capital: Общий капитал
            correlation_matrix: Матрица корреляций
        
        Returns:
            Результат распределения
        """
        selected = []
        rejected = []
        
        # Отфильтровать сигналы
        for signal in signals:
            if signal.confidence >= self.thresholds["min_confidence"] and \
               signal.sharpe_ratio >= self.thresholds["min_sharpe_ratio"]:
                signal.status = SignalStatus.SELECTED
                selected.append(signal)
            else:
                signal.status = SignalStatus.REJECTED
                signal.reason = "Low confidence or Sharpe ratio"
                rejected.append(signal)
        
        # Упрощённая максимизация Sharpe
        # В реальности нужно использовать оптимизацию
        if selected:
            # Сортировать по Sharpe ratio
            selected.sort(key=lambda x: x.sharpe_ratio, reverse=True)
            
            # Выделить больше капитала сигналам с высоким Sharpe
            total_sharpe = sum(s.sharpe_ratio for s in selected)
            if total_sharpe > 0:
                for signal in selected:
                    signal.allocated_weight = signal.sharpe_ratio / total_sharpe
                    signal.allocated_capital = total_capital * signal.allocated_weight
        
        # Рассчитать метрики портфеля
        portfolio_return = sum(s.expected_return * s.allocated_weight for s in selected)
        portfolio_risk = sum(s.risk * s.allocated_weight for s in selected)
        
        # Учесть корреляции
        if correlation_matrix and len(selected) > 1:
            # Упрощённая корректировка
            avg_correlation = np.mean([
                correlation_matrix.get_correlation(s1.symbol, s2.symbol)
                for i, s1 in enumerate(selected)
                for j, s2 in enumerate(selected)
                if i < j
            ])
            portfolio_risk *= (1 + avg_correlation * 0.5)
        
        return AllocationResult(
            portfolio_id=portfolio_id,
            timestamp=datetime.now(timezone.utc),
            signals=signals,
            selected_signals=selected,
            rejected_signals=rejected,
            total_capital=total_capital,
            allocated_capital=total_capital if selected else 0.0,
            unallocated_capital=0.0 if selected else total_capital,
            expected_portfolio_return=portfolio_return,
            expected_portfolio_risk=portfolio_risk,
            portfolio_sharpe_ratio=portfolio_return / portfolio_risk if portfolio_risk > 0 else 0.0,
            portfolio_correlation=avg_correlation if 'avg_correlation' in locals() else 0.0,
            method=AllocationMethod.SHARPE_MAXIMIZATION,
        )
    
    def allocate_correlation_adjusted(
        self,
        portfolio_id: str,
        signals: list[OpportunitySignal],
        total_capital: float,
        correlation_matrix: CorrelationMatrix | None = None,
    ) -> AllocationResult:
        """
        Распределение с учётом корреляции.
        
        Args:
            portfolio_id: ID портфеля
            signals: Сигналы
            total_capital: Общий капитал
            correlation_matrix: Матрица корреляций
        
        Returns:
            Результат распределения
        """
        selected = []
        rejected = []
        
        # Отфильтровать сигналы
        for signal in signals:
            if signal.confidence >= self.thresholds["min_confidence"]:
                signal.status = SignalStatus.SELECTED
                selected.append(signal)
            else:
                signal.status = SignalStatus.REJECTED
                signal.reason = "Low confidence"
                rejected.append(signal)
        
        # Учесть корреляции
        if correlation_matrix and len(selected) > 1:
            # Упрощённая логика: уменьшить вес высококоррелированных сигналов
            for i, signal1 in enumerate(selected):
                for j, signal2 in enumerate(selected):
                    if i < j:
                        corr = correlation_matrix.get_correlation(signal1.symbol, signal2.symbol)
                        if corr > self.thresholds["max_correlation"]:
                            # Уменьшить вес обоих сигналов
                            signal1.allocated_weight *= (1 - corr * 0.3)
                            signal2.allocated_weight *= (1 - corr * 0.3)
        
        # Нормализовать веса
        if selected:
            total_weight = sum(s.allocated_weight for s in selected)
            if total_weight > 0:
                for signal in selected:
                    signal.allocated_weight /= total_weight
                    signal.allocated_capital = total_capital * signal.allocated_weight
        
        # Рассчитать метрики портфеля
        portfolio_return = sum(s.expected_return * s.allocated_weight for s in selected)
        portfolio_risk = sum(s.risk * s.allocated_weight for s in selected)
        
        # Рассчитать среднюю корреляцию
        avg_correlation = 0.0
        if correlation_matrix and len(selected) > 1:
            avg_correlation = np.mean([
                correlation_matrix.get_correlation(s1.symbol, s2.symbol)
                for i, s1 in enumerate(selected)
                for j, s2 in enumerate(selected)
                if i < j
            ])
        
        return AllocationResult(
            portfolio_id=portfolio_id,
            timestamp=datetime.now(timezone.utc),
            signals=signals,
            selected_signals=selected,
            rejected_signals=rejected,
            total_capital=total_capital,
            allocated_capital=total_capital if selected else 0.0,
            unallocated_capital=0.0 if selected else total_capital,
            expected_portfolio_return=portfolio_return,
            expected_portfolio_risk=portfolio_risk,
            portfolio_sharpe_ratio=portfolio_return / portfolio_risk if portfolio_risk > 0 else 0.0,
            portfolio_correlation=avg_correlation,
            method=AllocationMethod.CORRELATION_ADJUSTED,
        )
    
    def allocate_optimal(
        self,
        portfolio_id: str,
        signals: list[OpportunitySignal],
        total_capital: float,
        method: AllocationMethod = AllocationMethod.SHARPE_MAXIMIZATION,
        correlation_matrix: CorrelationMatrix | None = None,
    ) -> AllocationResult:
        """
        Оптимальное распределение.
        
        Args:
            portfolio_id: ID портфеля
            signals: Сигналы
            total_capital: Общий капитал
            method: Метод распределения
            correlation_matrix: Матрица корреляций
        
        Returns:
            Результат распределения
        """
        if method == AllocationMethod.EQUAL:
            return self.allocate_equal(portfolio_id, signals, total_capital)
        elif method == AllocationMethod.RISK_PARITY:
            return self.allocate_risk_parity(portfolio_id, signals, total_capital)
        elif method == AllocationMethod.SHARPE_MAXIMIZATION:
            return self.allocate_sharpe_maximization(portfolio_id, signals, total_capital, correlation_matrix)
        elif method == AllocationMethod.CORRELATION_ADJUSTED:
            return self.allocate_correlation_adjusted(portfolio_id, signals, total_capital, correlation_matrix)
        else:
            return self.allocate_equal(portfolio_id, signals, total_capital)
    
    def analyze_portfolio(
        self,
        portfolio_id: str,
        signals: list[OpportunitySignal],
        total_capital: float,
        correlation_matrix: CorrelationMatrix | None = None,
    ) -> PortfolioAnalysis:
        """
        Полный анализ портфеля.
        
        Args:
            portfolio_id: ID портфеля
            signals: Сигналы
            total_capital: Общий капитал
            correlation_matrix: Матрица корреляций
        
        Returns:
            Полный анализ
        """
        allocations = []
        
        # Провести аллокацию разными методами
        for method in AllocationMethod:
            allocation = self.allocate_optimal(
                portfolio_id, signals, total_capital, method, correlation_matrix
            )
            allocations.append(allocation)
        
        # Выбрать лучшую аллокацию
        best_allocation = max(allocations, key=lambda x: x.portfolio_sharpe_ratio)
        
        # Рассчитать метрики портфеля
        cumulative_return = best_allocation.expected_portfolio_return
        sharpe_ratio = best_allocation.portfolio_sharpe_ratio
        max_drawdown = max(s.max_drawdown for s in best_allocation.selected_signals) if best_allocation.selected_signals else 0.0
        win_rate = np.mean([1 if s.expected_return > 0 else 0 for s in best_allocation.selected_signals]) if best_allocation.selected_signals else 0.0
        
        # Сгенерировать рекомендации
        recommendations = self._generate_recommendations(best_allocation)
        
        # Рассчитать уверенность
        confidence = self._calculate_confidence(best_allocation)
        
        return PortfolioAnalysis(
            portfolio_id=portfolio_id,
            timestamp=datetime.now(timezone.utc),
            allocations=allocations,
            cumulative_return=cumulative_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            correlation_matrix=correlation_matrix,
            confidence=confidence,
            recommendations=recommendations,
        )
    
    def _generate_recommendations(self, allocation: AllocationResult) -> list[str]:
        """Сгенерировать рекомендации"""
        recommendations = []
        
        if allocation.selected_signals:
            recommendations.append(f"Selected {len(allocation.selected_signals)} signals out of {len(allocation.signals)}")
            recommendations.append(f"Expected portfolio return: {allocation.expected_portfolio_return:.2f}%")
            recommendations.append(f"Expected portfolio risk: {allocation.expected_portfolio_risk:.2f}%")
            recommendations.append(f"Portfolio Sharpe ratio: {allocation.portfolio_sharpe_ratio:.2f}")
        else:
            recommendations.append("No signals selected for allocation")
        
        if allocation.unallocated_capital > 0:
            recommendations.append(f"{allocation.unallocated_capital:.2f} capital unallocated")
        
        return recommendations
    
    def _calculate_confidence(self, allocation: AllocationResult) -> float:
        """Рассчитать уверенность"""
        confidence = 0.5
        
        # Учесть количество выбранных сигналов
        if allocation.selected_signals:
            confidence += 0.1 * min(1, len(allocation.selected_signals) / 10)
        
        # Учесть Sharpe ratio
        if allocation.portfolio_sharpe_ratio > 1.0:
            confidence += 0.2
        elif allocation.portfolio_sharpe_ratio > 0.5:
            confidence += 0.1
        
        # Учесть риск
        if allocation.expected_portfolio_risk < self.thresholds["max_portfolio_risk"]:
            confidence += 0.1
        
        return min(1.0, confidence)


# Глобальный экземпляр
_portfolio_allocator: PortfolioOpportunityAllocator | None = None


def get_portfolio_allocator() -> PortfolioOpportunityAllocator:
    """Получить глобальный Portfolio Opportunity Allocator"""
    global _portfolio_allocator
    if _portfolio_allocator is None:
        _portfolio_allocator = PortfolioOpportunityAllocator()
    return _portfolio_allocator


def reset_portfolio_allocator():
    """Сбросить Portfolio Opportunity Allocator (для тестов)"""
    global _portfolio_allocator
    _portfolio_allocator = PortfolioOpportunityAllocator()
