"""
ASTRA BOT — Backtester Module
Event-driven бэктестер для тестирования стратегий
"""

from .analyzer import BacktestAnalyzer
from .data_loader import HistoricalDataLoader
from .engine import BacktestConfig, BacktestEngine, BacktestResult

__all__ = [
    "BacktestAnalyzer",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "HistoricalDataLoader",
]
