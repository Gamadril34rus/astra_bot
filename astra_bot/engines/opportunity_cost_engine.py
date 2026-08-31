"""
ASTRA BOT — Opportunity Cost Engine

Движок расчёта альтернативной стоимости (Master Specification v2, Section 22)

Если одновременно возникает несколько сигналов:
- Signal A
- Signal B
- Signal C

Рассчитывает:
- expected_return
- risk
- confidence
- correlation
- capital_requirement

И выбирает оптимальное распределение ограниченного капитала.

Не считать каждую сделку независимой.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SignalOpportunity:
    """Возможность сигнала"""
    signal_id: str
    symbol: str
    direction: str  # long/short
    
    # Ожидания
    expected_return: float  # Ожидаемая доходность (%)
    risk: float  # Риск (%)
    confidence: float  # Уверенность (0-1)
    
    # Требования
    capital_requirement: float  # Требуемый капитал
    position_size: float  # Размер позиции
    
    # Корреляции
    correlations: dict[str, float] = field(default_factory=dict)  # symbol -> correlation
    
    # Временные метки
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "expected_return": self.expected_return,
            "risk": self.risk,
            "confidence": self.confidence,
            "capital_requirement": self.capital_requirement,
            "position_size": self.position_size,
            "correlations": self.correlations,
            "created_at": self.created_at.isoformat(),
        }
        
        if self.expires_at:
            result["expires_at"] = self.expires_at.isoformat()
        
        return result


@dataclass
class CapitalAllocation:
    """Распределение капитала"""
    signal_id: str
    allocated_capital: float  # Выделенный капитал
    position_size: float  # Размер позиции
    priority: int  # Приоритет
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "allocated_capital": self.allocated_capital,
            "position_size": self.position_size,
            "priority": self.priority,
        }


@dataclass
class OpportunityCostResult:
    """Результат расчёта альтернативной стоимости"""
    total_capital: float
    available_capital: float
    
    # Возможности
    opportunities: list[SignalOpportunity]
    
    # Распределение
    allocations: list[CapitalAllocation]
    
    # Альтернативная стоимость
    opportunity_cost: float  # Стоимость нереализованных возможностей
    
    # Метаданные
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "total_capital": self.total_capital,
            "available_capital": self.available_capital,
            "opportunities": [o.to_dict() for o in self.opportunities],
            "allocations": [a.to_dict() for a in self.allocations],
            "opportunity_cost": self.opportunity_cost,
            "timestamp": self.timestamp.isoformat(),
        }


class OpportunityCostEngine:
    """
    Движок расчёта альтернативной стоимости.
    
    Оптимизирует распределение капитала между несколькими сигналами
    с учётом корреляций и ограничений.
    """
    
    def __init__(self):
        # Ограничения
        self.max_positions = 5
        self.max_exposure_per_symbol = 0.2  # 20% капитала на один символ
        self.max_total_exposure = 1.0  # 100% капитала
        
        # Веса для оценки сигналов
        self.signal_weights = {
            "expected_return": 0.4,
            "confidence": 0.3,
            "risk_adjusted": 0.3,
        }
    
    def calculate_signal_score(self, signal: SignalOpportunity) -> float:
        """
        Рассчитать оценку сигнала.
        
        Args:
            signal: Возможность сигнала
        
        Returns:
            Оценка сигнала
        """
        # Рассчитать Sharpe-like ratio
        if signal.risk > 0:
            sharpe_ratio = signal.expected_return / signal.risk
        else:
            sharpe_ratio = 0.0
        
        # Оценка на основе весов
        score = (
            self.signal_weights["expected_return"] * signal.expected_return +
            self.signal_weights["confidence"] * signal.confidence * 100 +
            self.signal_weights["risk_adjusted"] * sharpe_ratio * 10
        )
        
        return score
    
    def calculate_portfolio_risk(
        self,
        opportunities: list[SignalOpportunity],
        allocations: list[CapitalAllocation]
    ) -> float:
        """
        Рассчитать риск портфеля.
        
        Args:
            opportunities: Возможности сигналов
            allocations: Распределение капитала
        
        Returns:
            Риск портфеля
        """
        if not allocations:
            return 0.0
        
        # Создать матрицу корреляций
        symbols = [o.symbol for o in opportunities]
        n = len(symbols)
        
        # Инициализировать матрицу
        corr_matrix = np.eye(n)
        
        # Заполнить корреляции
        for i in range(n):
            for j in range(n):
                if i != j:
                    symbol_i = symbols[i]
                    symbol_j = symbols[j]
                    
                    # Найти корреляцию между символами
                    corr = opportunities[i].correlations.get(symbol_j, 0.0)
                    corr_matrix[i, j] = corr
                    corr_matrix[j, i] = corr
        
        # Рассчитать риск портфеля
        # Упрощённая модель: portfolio_risk = sqrt(sum(sum(w_i * w_j * corr_ij * risk_i * risk_j)))
        weights = []
        risks = []
        
        for alloc in allocations:
            # Найти соответствующую возможность
            opp = next((o for o in opportunities if o.signal_id == alloc.signal_id), None)
            if opp:
                # Вес = выделенный капитал / общий капитал
                total_allocated = sum(a.allocated_capital for a in allocations)
                weight = alloc.allocated_capital / total_allocated if total_allocated > 0 else 0
                weights.append(weight)
                risks.append(opp.risk)
            else:
                weights.append(0.0)
                risks.append(0.0)
        
        portfolio_risk = 0.0
        for i in range(len(weights)):
            for j in range(len(weights)):
                portfolio_risk += weights[i] * weights[j] * corr_matrix[i, j] * risks[i] * risks[j]
        
        return np.sqrt(portfolio_risk)
    
    def optimize_capital_allocation(
        self,
        opportunities: list[SignalOpportunity],
        total_capital: float
    ) -> list[CapitalAllocation]:
        """
        Оптимизировать распределение капитала.
        
        Args:
            opportunities: Возможности сигналов
            total_capital: Общий капитал
        
        Returns:
            Оптимальное распределение
        """
        if not opportunities:
            return []
        
        # Отсортировать по оценке
        scored_opportunities = []
        for opp in opportunities:
            score = self.calculate_signal_score(opp)
            scored_opportunities.append((opp, score))
        
        scored_opportunities.sort(key=lambda x: x[1], reverse=True)
        
        # Жадный алгоритм распределения
        allocations = []
        allocated_capital = 0.0
        
        for opp, score in scored_opportunities:
            # Проверить ограничения
            if len(allocations) >= self.max_positions:
                break
            
            # Проверить экспозицию по символу
            symbol_allocation = sum(
                a.allocated_capital for a in allocations
                if next((o for o in opportunities if o.signal_id == a.signal_id), None).symbol == opp.symbol
            )
            
            max_symbol_capital = total_capital * self.max_exposure_per_symbol
            if symbol_allocation >= max_symbol_capital:
                continue
            
            # Проверить общую экспозицию
            if allocated_capital >= total_capital:
                break
            
            # Выделить капитал
            # Размер позиции пропорционален оценке
            capital_to_allocate = min(
                opp.capital_requirement,
                total_capital * 0.2,  # Не более 20% на одну сделку
                total_capital - allocated_capital,
                max_symbol_capital - symbol_allocation
            )
            
            if capital_to_allocate > 0:
                # Рассчитать размер позиции
                position_size = capital_to_allocate / opp.entry_price if hasattr(opp, 'entry_price') else capital_to_allocate
                
                allocations.append(CapitalAllocation(
                    signal_id=opp.signal_id,
                    allocated_capital=capital_to_allocate,
                    position_size=position_size,
                    priority=len(allocations) + 1
                ))
                
                allocated_capital += capital_to_allocate
        
        return allocations
    
    def calculate_opportunity_cost(
        self,
        opportunities: list[SignalOpportunity],
        allocations: list[CapitalAllocation]
    ) -> float:
        """
        Рассчитать альтернативную стоимость.
        
        Args:
            opportunities: Возможности сигналов
            allocations: Распределение капитала
        
        Returns:
            Альтернативная стоимость
        """
        if not opportunities or not allocations:
            return 0.0
        
        # Найти нереализованные возможности
        allocated_signal_ids = {a.signal_id for a in allocations}
        unallocated_opportunities = [
            o for o in opportunities 
            if o.signal_id not in allocated_signal_ids
        ]
        
        if not unallocated_opportunities:
            return 0.0
        
        # Рассчитать ожидаемую доходность нереализованных возможностей
        opportunity_cost = 0.0
        for opp in unallocated_opportunities:
            # Ожидаемая доходность с учётом уверенности
            expected_pnl = opp.expected_return * opp.confidence
            opportunity_cost += expected_pnl
        
        # Средняя альтернативная стоимость
        opportunity_cost /= len(unallocated_opportunities) if unallocated_opportunities else 1
        
        return opportunity_cost
    
    def evaluate_signals(
        self,
        opportunities: list[SignalOpportunity],
        total_capital: float,
        available_capital: float
    ) -> OpportunityCostResult:
        """
        Оценить сигналы и рассчитать альтернативную стоимость.
        
        Args:
            opportunities: Возможности сигналов
            total_capital: Общий капитал
            available_capital: Доступный капитал
        
        Returns:
            OpportunityCostResult
        """
        # Оптимизировать распределение
        allocations = self.optimize_capital_allocation(
            opportunities, available_capital
        )
        
        # Рассчитать альтернативную стоимость
        opportunity_cost = self.calculate_opportunity_cost(
            opportunities, allocations
        )
        
        # Рассчитать риск портфеля
        portfolio_risk = self.calculate_portfolio_risk(
            opportunities, allocations
        )
        
        # Добавить информацию о риске в результат
        result = OpportunityCostResult(
            total_capital=total_capital,
            available_capital=available_capital,
            opportunities=opportunities,
            allocations=allocations,
            opportunity_cost=opportunity_cost
        )
        
        return result
    
    def compare_with_counterfactual(
        self,
        actual_allocation: list[CapitalAllocation],
        opportunities: list[SignalOpportunity]
    ) -> dict[str, Any]:
        """
        Сравнить фактическое распределение с оптимальным (Section 57).
        
        Args:
            actual_allocation: Фактическое распределение
            opportunities: Возможности сигналов
        
        Returns:
            Сравнение распределений
        """
        # Оптимальное распределение
        total_capital = sum(a.allocated_capital for a in actual_allocation)
        optimal_allocation = self.optimize_capital_allocation(
            opportunities, total_capital
        )
        
        # Сравнить распределения
        comparison = {
            "actual_allocation": [a.to_dict() for a in actual_allocation],
            "optimal_allocation": [a.to_dict() for a in optimal_allocation],
            "differences": [],
        }
        
        # Найти различия
        actual_signal_ids = {a.signal_id for a in actual_allocation}
        optimal_signal_ids = {a.signal_id for a in optimal_allocation}
        
        # Сигналы, которые были выбранны фактически, но не оптимально
        over_allocated = actual_signal_ids - optimal_signal_ids
        
        # Сигналы, которые были оптимальны, но не выбраны
        under_allocated = optimal_signal_ids - actual_signal_ids
        
        comparison["differences"] = {
            "over_allocated": list(over_allocated),
            "under_allocated": list(under_allocated),
        }
        
        # Рассчитать альтернативную стоимость
        opportunity_cost = self.calculate_opportunity_cost(
            opportunities, actual_allocation
        )
        
        comparison["opportunity_cost"] = opportunity_cost
        
        return comparison


# Глобальный экземпляр Opportunity Cost Engine
_opportunity_cost_engine: OpportunityCostEngine | None = None


def get_opportunity_cost_engine() -> OpportunityCostEngine:
    """Получить глобальный Opportunity Cost Engine"""
    global _opportunity_cost_engine
    if _opportunity_cost_engine is None:
        _opportunity_cost_engine = OpportunityCostEngine()
    return _opportunity_cost_engine


def reset_opportunity_cost_engine():
    """Сбросить Opportunity Cost Engine (для тестов)"""
    global _opportunity_cost_engine
    _opportunity_cost_engine = OpportunityCostEngine()
