"""
ASTRA BOT - Engines Module

Модуль движков (ТЗ Пункты 4, 9, 10, 29, 35, 49-51, 94-95)

Содержит:
- NewsIntelligenceEngine: Движок анализа новостей
- UncertaintyEngine: Движок оценки неопределённости
- ProbabilisticForecastEngine: Движок вероятностного прогнозирования
- AlphaDecayEngine: Движок затухания альфа
- ExecutionEngine: Движок исполнения
- ExecutionOptimizer: Оптимизатор исполнения
- SignalCorrelationEngine: Движок корреляции сигналов
- PortfolioExposureEngine: Движок экспозиции портфеля
- TailRiskEngine: Движок хвостового риска
- MFEMAEEngine: Движок MFE/MAE
- CounterfactualEngine: Контрфактный движок
- LossAttributionEngine: Движок атрибуции убытков
- OpportunityCostEngine: Движок упущенной выгоды
- RegimeSimilarityEngine: Движок подобия режимов
- MarketStateClusterer: Движок кластеризации состояний рынка
- RiskEngine: Движок управления рисками
- RegimeDetector: Движок обнаружения режимов
"""

from .news_intelligence_engine import NewsIntelligenceEngine, get_news_intelligence_engine
from .uncertainty_engine import UncertaintyEngine, get_uncertainty_engine, reset_uncertainty_engine
from .probabilistic_forecast import ProbabilisticForecastEngine, get_forecast_engine, reset_forecast_engine
from .alpha_decay_engine import AlphaDecayEngine, get_alpha_decay_engine, reset_alpha_decay_engine
from .execution_engine import ExecutionEngine, get_execution_engine, reset_execution_engine
from .execution_optimizer import ExecutionOptimizer, get_execution_optimizer, reset_execution_optimizer
from .signal_correlation_engine import SignalCorrelationEngine, get_signal_correlation_engine, reset_signal_correlation_engine
from .portfolio_exposure_engine import PortfolioExposureEngine, get_portfolio_exposure_engine, reset_portfolio_exposure_engine
from .tail_risk_engine import TailRiskEngine, get_tail_risk_engine, reset_tail_risk_engine
from .mfe_mae_engine import MFEMAEEngine, get_mfe_mae_engine, reset_mfe_mae_engine
from .counterfactual_engine import CounterfactualEngine, get_counterfactual_engine, reset_counterfactual_engine
from .loss_attribution_engine import LossAttributionEngine, get_loss_attribution_engine, reset_loss_attribution_engine
from .opportunity_cost_engine import OpportunityCostEngine, get_opportunity_cost_engine, reset_opportunity_cost_engine
from .regime_similarity_engine import RegimeSimilarityEngine, get_regime_similarity_engine, reset_regime_similarity_engine
from .market_state_clusterer import MarketStateClusterer, get_market_state_clusterer, reset_market_state_clusterer
from .risk_engine import RiskEngine, get_risk_engine, reset_risk_engine
from .regime_detector import MarketRegimeDetector as RegimeDetector, get_regime_detector, reset_regime_detector

__all__ = [
    # News Intelligence
    "NewsIntelligenceEngine",
    "get_news_intelligence_engine",
    
    # Uncertainty
    "UncertaintyEngine",
    "get_uncertainty_engine",
    "reset_uncertainty_engine",
    
    # Forecast
    "ProbabilisticForecastEngine",
    "get_forecast_engine",
    "reset_forecast_engine",
    
    # Alpha Decay
    "AlphaDecayEngine",
    "get_alpha_decay_engine",
    "reset_alpha_decay_engine",
    
    # Execution
    "ExecutionEngine",
    "get_execution_engine",
    "reset_execution_engine",
    
    # Execution Optimizer
    "ExecutionOptimizer",
    "get_execution_optimizer",
    "reset_execution_optimizer",
    
    # Signal Correlation
    "SignalCorrelationEngine",
    "get_signal_correlation_engine",
    "reset_signal_correlation_engine",
    
    # Portfolio Exposure
    "PortfolioExposureEngine",
    "get_portfolio_exposure_engine",
    "reset_portfolio_exposure_engine",
    
    # Tail Risk
    "TailRiskEngine",
    "get_tail_risk_engine",
    "reset_tail_risk_engine",
    
    # MFE/MAE
    "MFEMAEEngine",
    "get_mfe_mae_engine",
    "reset_mfe_mae_engine",
    
    # Counterfactual
    "CounterfactualEngine",
    "get_counterfactual_engine",
    "reset_counterfactual_engine",
    
    # Loss Attribution
    "LossAttributionEngine",
    "get_loss_attribution_engine",
    "reset_loss_attribution_engine",
    
    # Opportunity Cost
    "OpportunityCostEngine",
    "get_opportunity_cost_engine",
    "reset_opportunity_cost_engine",
    
    # Regime Similarity
    "RegimeSimilarityEngine",
    "get_regime_similarity_engine",
    "reset_regime_similarity_engine",
    
    # Market State Clusterer
    "MarketStateClusterer",
    "get_market_state_clusterer",
    "reset_market_state_clusterer",
    
    # Risk Engine
    "RiskEngine",
    "get_risk_engine",
    "reset_risk_engine",
    
    # Regime Detector
    "RegimeDetector",
    "get_regime_detector",
    "reset_regime_detector",
]
