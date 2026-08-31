"""
ASTRA BOT - Engines Package

Contains all trading and analysis engines:

Core Engines:
- risk_engine: Risk management (existing)
- execution_engine: Trade execution (existing)
- regime_detector: Market regime detection (existing)

New Engines (Master Specification v2):
- uncertainty_engine: Uncertainty estimation (Phase B)
- probabilistic_forecast: Probabilistic forecasting (Phase B)
- alpha_decay_engine: Signal decay tracking (Phase D)
- execution_optimizer: Optimal execution (Phase D)
- signal_correlation_engine: Signal correlation analysis (Phase E)
- portfolio_exposure_engine: Portfolio risk (Phase E)
- tail_risk_engine: Tail risk metrics (Phase E)
- mfe_mae_engine: MFE/MAE tracking (Phase F)
- counterfactual_engine: Counterfactual analysis (Phase F)
- loss_attribution_engine: Loss classification (Phase F)
- opportunity_cost_engine: Opportunity cost calculation (Phase C)
- regime_similarity_engine: Regime similarity (Phase B)
- market_state_clusterer: Unsupervised clustering (Phase G)
"""

# Existing engines
from .execution_engine import ExecutionEngine
from .regime_detector import MarketRegimeDetector as RegimeDetector
from .risk_engine import RiskEngine, get_risk_engine, reset_risk_engine

# New engines - Phase A (Statistical Robustness)
# (Statistical tests are in research.statistical_tests)

# New engines - Phase B (Prediction Quality)
from .uncertainty_engine import UncertaintyEngine, get_uncertainty_engine, reset_uncertainty_engine
from .probabilistic_forecast import ProbabilisticForecastEngine, get_forecast_engine, reset_forecast_engine
from .regime_similarity_engine import RegimeSimilarityEngine, get_regime_similarity_engine, reset_regime_similarity_engine

# New engines - Phase C (Decision Intelligence)
from .opportunity_cost_engine import OpportunityCostEngine, get_opportunity_cost_engine, reset_opportunity_cost_engine

# New engines - Phase D (Execution)
from .alpha_decay_engine import AlphaDecayEngine, get_alpha_decay_engine, reset_alpha_decay_engine
from .execution_optimizer import ExecutionOptimizer, get_execution_optimizer, reset_execution_optimizer

# New engines - Phase E (Portfolio)
from .signal_correlation_engine import SignalCorrelationEngine, get_signal_correlation_engine, reset_signal_correlation_engine
from .portfolio_exposure_engine import PortfolioExposureEngine, get_portfolio_exposure_engine, reset_portfolio_exposure_engine
from .tail_risk_engine import TailRiskEngine, get_tail_risk_engine, reset_tail_risk_engine

# New engines - Phase F (Learning)
from .mfe_mae_engine import MFEMAEEngine, get_mfe_mae_engine, reset_mfe_mae_engine
from .counterfactual_engine import CounterfactualEngine, get_counterfactual_engine, reset_counterfactual_engine
from .loss_attribution_engine import LossAttributionEngine, get_loss_attribution_engine, reset_loss_attribution_engine

# New engines - Phase G (Discovery)
from .market_state_clusterer import MarketStateClusterer, get_market_state_clusterer, reset_market_state_clusterer

__all__ = [
    # Existing
    "RiskEngine",
    "get_risk_engine",
    "reset_risk_engine",
    "ExecutionEngine",
    "RegimeDetector",
    
    # Phase B
    "UncertaintyEngine",
    "get_uncertainty_engine",
    "reset_uncertainty_engine",
    "ProbabilisticForecastEngine",
    "get_forecast_engine",
    "reset_forecast_engine",
    "RegimeSimilarityEngine",
    "get_regime_similarity_engine",
    "reset_regime_similarity_engine",
    
    # Phase C
    "OpportunityCostEngine",
    "get_opportunity_cost_engine",
    "reset_opportunity_cost_engine",
    
    # Phase D
    "AlphaDecayEngine",
    "get_alpha_decay_engine",
    "reset_alpha_decay_engine",
    "ExecutionOptimizer",
    "get_execution_optimizer",
    "reset_execution_optimizer",
    
    # Phase E
    "SignalCorrelationEngine",
    "get_signal_correlation_engine",
    "reset_signal_correlation_engine",
    "PortfolioExposureEngine",
    "get_portfolio_exposure_engine",
    "reset_portfolio_exposure_engine",
    "TailRiskEngine",
    "get_tail_risk_engine",
    "reset_tail_risk_engine",
    
    # Phase F
    "MFEMAEEngine",
    "get_mfe_mae_engine",
    "reset_mfe_mae_engine",
    "CounterfactualEngine",
    "get_counterfactual_engine",
    "reset_counterfactual_engine",
    "LossAttributionEngine",
    "get_loss_attribution_engine",
    "reset_loss_attribution_engine",
    
    # Phase G
    "MarketStateClusterer",
    "get_market_state_clusterer",
    "reset_market_state_clusterer",
]
