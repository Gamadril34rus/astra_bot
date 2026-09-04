"""
ASTRA BOT - Trading Module

Модуль торговли для 4 приоритетных движков:
- PortfolioOpportunityAllocator: Распределение возможностей портфеля (Приоритет #4)
"""

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
    "PortfolioOpportunityAllocator",
    "get_portfolio_allocator",
    "reset_portfolio_allocator",
    "OpportunitySignal",
    "AllocationResult",
    "PortfolioAnalysis",
    "AllocationMethod",
]
