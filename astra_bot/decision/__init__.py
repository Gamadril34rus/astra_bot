"""
ASTRA BOT — Decision pipeline.

Data → Regime → Strategy → ML → EV → Risk → Execution.

Ни один индикатор не открывает сделку сам по себе. Каждый движок —
это независимо тестируемый компонент, который выставляет оценки;
финальное решение принимает ``Pipeline.decide``.
"""

from .config import DecisionConfig
from .context import MarketContext, SignalCandidate
from .correlation_engine import CorrelationEngine
from .derivatives_engine import DerivativesEngine
from .ev_engine import EVEngine
from .feature_engine import FeatureEngine
from .liquidity_engine import LiquidityEngine, LiquidityReport
from .news_engine import NewsEngine, NewsReport
from .onchain_engine import OnChainEngine
from .orderbook_engine import OrderBookEngine
from .pipeline import Decision, DecisionPipeline
from .regime_axes import (
    CrossMarketContext,
    LiquidityAxis,
    RegimeAxes,
    TrendAxis,
    VolatilityAxis,
    derive_axes,
)
from .regime_engine import MarketRegime, RegimeEngine
from .scoring import SignalScorer
from .structure_engine import StructureEngine
from .technical_engine import TechnicalEngine

__all__ = [
    "CorrelationEngine",
    "CrossMarketContext",
    "Decision",
    "DecisionConfig",
    "DecisionPipeline",
    "DerivativesEngine",
    "EVEngine",
    "FeatureEngine",
    "LiquidityAxis",
    "LiquidityEngine",
    "LiquidityReport",
    "MarketContext",
    "MarketRegime",
    "NewsEngine",
    "NewsReport",
    "OnChainEngine",
    "OrderBookEngine",
    "RegimeAxes",
    "RegimeEngine",
    "SignalCandidate",
    "SignalScorer",
    "StructureEngine",
    "TechnicalEngine",
    "TrendAxis",
    "VolatilityAxis",
    "derive_axes",
]
