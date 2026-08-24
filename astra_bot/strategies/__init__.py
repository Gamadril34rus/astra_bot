"""
ASTRA BOT — Trading Strategies
"""

from .adaptive_grid import AdaptiveGridStrategy
from .base import BaseStrategy, Signal, StrategyConfig
from .book_breakout import BookBreakoutConfig, BookBreakoutStrategy
from .mean_reversion import MeanReversionStrategy
from .momentum import MomentumStrategy
from .multicurrency_mtf import MulticurrencyMTFConfig, MulticurrencyMTFStrategy
from .pullback import PullbackConfig, PullbackStrategy
from .scalp import ScalpConfig, ScalpStrategy
from .scalp5m import Scalp5mConfig, Scalp5mStrategy
from .ts_momentum import (
    TSM_ACTION_FLAT,
    TSM_ACTION_FLIP,
    TimeSeriesMomentumConfig,
    TimeSeriesMomentumStrategy,
)

__all__ = [
    "TSM_ACTION_FLAT",
    "TSM_ACTION_FLIP",
    "AdaptiveGridStrategy",
    "BaseStrategy",
    "BookBreakoutConfig",
    "BookBreakoutStrategy",
    "MeanReversionStrategy",
    "MomentumStrategy",
    "MulticurrencyMTFConfig",
    "MulticurrencyMTFStrategy",
    "PullbackConfig",
    "PullbackStrategy",
    "Scalp5mConfig",
    "Scalp5mStrategy",
    "ScalpConfig",
    "ScalpStrategy",
    "Signal",
    "StrategyConfig",
    "TimeSeriesMomentumConfig",
    "TimeSeriesMomentumStrategy",
]
