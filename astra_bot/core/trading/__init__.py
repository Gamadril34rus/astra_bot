"""
ASTRA BOT - Trading Module

Модуль торговли для 4 приоритетных движков:
- PortfolioOpportunityAllocator: Распределение возможностей портфеля (Приоритет #4)
"""

from .portfolio_allocator import (
    AllocationMethod,
    AllocationResult,
    OpportunitySignal,
    PortfolioAnalysis,
    PortfolioOpportunityAllocator,
    get_portfolio_allocator,
    reset_portfolio_allocator,
)

__all__ = [
    "AllocationMethod",
    "AllocationResult",
    "OpportunitySignal",
    "PortfolioAnalysis",
    "PortfolioOpportunityAllocator",
    "get_portfolio_allocator",
    "reset_portfolio_allocator",
]
