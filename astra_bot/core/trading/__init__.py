"""
ASTRA BOT - Trading Module

Модуль торговли (ТЗ Пункты 8, 12-14, 16-18, 20, 22, 24, 26-28, 31-34, 37-41, 43-48, 53-55, 57-60, 62-70, 72-74, 76-80, 82-84, 86-88, 90-93, 96-100)

Содержит:
- TransactionCostEngine: Расчёт транзакционных издержек
- ExecutionSimulator: Симулятор исполнения
- PositionSizingEngine: Расчёт размера позиции
- MetaStrategyEngine: Мета-стратегии
- ChampionChallengerFramework: Фреймворк Champion-Challenger
- ValidationGates: Ворота валидации
- PortfolioOpportunityAllocator: Распределение возможностей портфеля (Приоритет #4)
"""

from .transaction_cost_engine import TransactionCostEngine, get_transaction_cost_engine, reset_transaction_cost_engine
from .execution_simulator import ExecutionSimulator, get_execution_simulator, reset_execution_simulator
from .position_sizing_engine import PositionSizingEngine, get_position_sizing_engine, reset_position_sizing_engine
from .meta_strategy_engine import MetaStrategyEngine, get_meta_strategy_engine, reset_meta_strategy_engine
from .champion_challenger import ChampionChallengerFramework, get_champion_challenger_framework, reset_champion_challenger_framework
from .validation_gates import ValidationGates, get_validation_gates, reset_validation_gates
from .portfolio_allocator import (
    PortfolioOpportunityAllocator,
    get_portfolio_allocator,
    reset_portfolio_allocator,
    OpportunitySignal,
    AllocationResult,
    PortfolioAnalysis,
    AllocationMethod,
)

__all__ = [
    "TransactionCostEngine",
    "get_transaction_cost_engine",
    "reset_transaction_cost_engine",
    "ExecutionSimulator",
    "get_execution_simulator",
    "reset_execution_simulator",
    "PositionSizingEngine",
    "get_position_sizing_engine",
    "reset_position_sizing_engine",
    "MetaStrategyEngine",
    "get_meta_strategy_engine",
    "reset_meta_strategy_engine",
    "ChampionChallengerFramework",
    "get_champion_challenger_framework",
    "reset_champion_challenger_framework",
    "ValidationGates",
    "get_validation_gates",
    "reset_validation_gates",
    "PortfolioOpportunityAllocator",
    "get_portfolio_allocator",
    "reset_portfolio_allocator",
    "OpportunitySignal",
    "AllocationResult",
    "PortfolioAnalysis",
    "AllocationMethod",
]
