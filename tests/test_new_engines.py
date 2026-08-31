"""
Tests for new engines implemented for Master Specification v2
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from astra_bot.engines.uncertainty_engine import (
    UncertaintyEngine, 
    UncertaintyType, 
    UncertaintyComponent,
    UncertaintyResult,
    ModelPrediction,
    MarketDataQuality,
    RegimeAssessment,
    get_uncertainty_engine
)

from astra_bot.engines.probabilistic_forecast import (
    ProbabilisticForecastEngine,
    ForecastDistribution,
    ForecastResult,
    get_forecast_engine
)

from astra_bot.engines.alpha_decay_engine import (
    AlphaDecayEngine,
    AlphaDecayProfile,
    SignalStrength,
    DecayMeasurement,
    get_alpha_decay_engine
)

from astra_bot.engines.execution_optimizer import (
    ExecutionOptimizer,
    OrderType,
    ExecutionUrgency,
    OrderBookState,
    LiquidityState,
    ExecutionStrategy,
    ExecutionPlan,
    get_execution_optimizer
)

from astra_bot.engines.signal_correlation_engine import (
    SignalCorrelationEngine,
    SignalFeatures,
    CorrelationMatrix,
    FactorGroup,
    SignalCorrelationResult,
    get_signal_correlation_engine
)

from astra_bot.engines.mfe_mae_engine import (
    MFEMAEEngine,
    MFEMAEResult,
    PricePoint,
    get_mfe_mae_engine
)

from astra_bot.engines.counterfactual_engine import (
    CounterfactualEngine,
    TradeOutcome,
    CounterfactualScenario,
    CounterfactualResult,
    PriceHistory,
    get_counterfactual_engine
)

from astra_bot.engines.loss_attribution_engine import (
    LossAttributionEngine,
    LossCause,
    LossAttribution,
    TradeContext,
    get_loss_attribution_engine
)

from astra_bot.engines.opportunity_cost_engine import (
    OpportunityCostEngine,
    SignalOpportunity,
    CapitalAllocation,
    OpportunityCostResult,
    get_opportunity_cost_engine
)

from astra_bot.engines.portfolio_exposure_engine import (
    PortfolioExposureEngine,
    Position,
    PortfolioExposure,
    get_portfolio_exposure_engine
)

from astra_bot.engines.tail_risk_engine import (
    TailRiskEngine,
    VaRResult,
    CVaRResult,
    TailRiskMetrics,
    TailRiskResult,
    get_tail_risk_engine
)

from astra_bot.engines.regime_similarity_engine import (
    RegimeSimilarityEngine,
    MarketState,
    SimilarityResult,
    RegimeSimilarityAssessment,
    get_regime_similarity_engine
)

from astra_bot.engines.market_state_clusterer import (
    MarketStateClusterer,
    MarketStateFeatures,
    ClusterResult,
    ClusteringResult,
    get_market_state_clusterer
)


class TestUncertaintyEngine:
    """Tests for Uncertainty Engine"""
    
    def test_uncertainty_engine_initialization(self):
        engine = UncertaintyEngine()
        assert engine is not None
        assert hasattr(engine, 'weights')
        assert hasattr(engine, 'thresholds')
    
    def test_prediction_confidence(self):
        engine = get_uncertainty_engine()
        component = engine.calculate_prediction_confidence(0.8, 0.9)
        assert component.type == UncertaintyType.PREDICTION_CONFIDENCE
        assert 0 <= component.value <= 1
    
    def test_data_uncertainty(self):
        engine = get_uncertainty_engine()
        data_quality = MarketDataQuality(
            spread_pct=0.001,
            depth=10000,
            volume=5000,
            volatility=0.01,
            data_gaps=0,
            latency_ms=10
        )
        component = engine.calculate_data_uncertainty(data_quality)
        assert component.type == UncertaintyType.DATA_UNCERTAINTY
        assert 0 <= component.value <= 1
    
    def test_regime_uncertainty(self):
        engine = get_uncertainty_engine()
        regime = RegimeAssessment(
            current_regime="trend",
            regime_confidence=0.8,
            regime_stability=0.7,
            transition_probability=0.2,
            historical_coverage=100
        )
        component = engine.calculate_regime_uncertainty(regime)
        assert component.type == UncertaintyType.REGIME_UNCERTAINTY
        assert 0 <= component.value <= 1
    
    def test_total_uncertainty(self):
        engine = get_uncertainty_engine()
        components = {
            UncertaintyType.PREDICTION_CONFIDENCE: UncertaintyComponent(
                type=UncertaintyType.PREDICTION_CONFIDENCE,
                value=0.3,
                description="test"
            ),
            UncertaintyType.MODEL_UNCERTAINTY: UncertaintyComponent(
                type=UncertaintyType.MODEL_UNCERTAINTY,
                value=0.4,
                description="test"
            ),
        }
        total = engine.calculate_total_uncertainty(components)
        assert 0 <= total <= 1


class TestProbabilisticForecastEngine:
    """Tests for Probabilistic Forecast Engine"""
    
    def test_forecast_engine_initialization(self):
        engine = ProbabilisticForecastEngine()
        assert engine is not None
        assert hasattr(engine, 'supported_horizons')
    
    def test_normal_distribution(self):
        engine = get_forecast_engine()
        returns = [0.01, -0.01, 0.02, -0.02, 0.03] * 20
        distribution = engine.fit_normal_distribution(returns, 0.01)
        assert distribution.expected_return is not None
        assert distribution.return_std is not None
    
    def test_multi_horizon_forecast(self):
        engine = get_forecast_engine()
        predictions = {"1m": 0.01, "5m": 0.02, "1h": 0.03}
        historical = {"1m": [0.01, -0.01] * 50, "5m": [0.02, -0.02] * 50, "1h": [0.03, -0.03] * 50}
        results = engine.create_multi_horizon_forecast(
            symbol="BTC/USDT",
            timeframe="1h",
            model_version="1.0",
            predictions=predictions,
            historical_returns_by_horizon=historical,
            uncertainty=0.1
        )
        assert len(results) > 0


class TestAlphaDecayEngine:
    """Tests for Alpha Decay Engine"""
    
    def test_alpha_decay_engine_initialization(self):
        engine = AlphaDecayEngine()
        assert engine is not None
        assert hasattr(engine, 'time_intervals')
    
    def test_signal_strength(self):
        engine = get_alpha_decay_engine()
        predictions = [0.01, 0.02, 0.03] * 100
        actuals = [0.015, 0.025, 0.035] * 100
        strength = engine.measure_signal_strength(
            signal_name="test",
            symbol="BTC",
            timeframe="1h",
            predictions=predictions,
            actuals=actuals
        )
        assert strength.sample_size == 100


class TestExecutionOptimizer:
    """Tests for Execution Optimizer"""
    
    def test_execution_optimizer_initialization(self):
        optimizer = ExecutionOptimizer()
        assert optimizer is not None
        assert hasattr(optimizer, 'thresholds')
    
    def test_market_order_evaluation(self):
        optimizer = get_execution_optimizer()
        order_book = OrderBookState(
            symbol="BTC/USDT",
            bids=[(10000, 1), (9999, 1)],
            asks=[(10001, 1), (10002, 1)],
            mid_price=10000.5,
            spread=1.0,
            spread_pct=0.0001,
            depth=2.0,
            best_bid=10000,
            best_ask=10001
        )
        strategy = optimizer.evaluate_market_order(
            order_book, 10000.5, 1.0
        )
        assert strategy.order_type == OrderType.MARKET
        assert strategy.expected_slippage >= 0


class TestSignalCorrelationEngine:
    """Tests for Signal Correlation Engine"""
    
    def test_correlation_engine_initialization(self):
        engine = SignalCorrelationEngine()
        assert engine is not None
        assert hasattr(engine, 'correlation_thresholds')
    
    def test_correlation_matrix(self):
        engine = get_signal_correlation_engine()
        signals = [
            SignalFeatures("signal1", {"feature1": 1.0, "feature2": 2.0}),
            SignalFeatures("signal2", {"feature1": 1.5, "feature2": 2.5}),
        ]
        matrix = engine.calculate_correlation_matrix(signals)
        assert matrix.matrix is not None


class TestMFEMAEEngine:
    """Tests for MFE/MAE Engine"""
    
    def test_mfe_mae_engine_initialization(self):
        engine = MFEMAEEngine()
        assert engine is not None
    
    def test_calculate_MFE_MAE(self):
        engine = get_mfe_mae_engine()
        price_points = [
            PricePoint(100, datetime.now()),
            PricePoint(105, datetime.now() + timedelta(minutes=1)),
            PricePoint(95, datetime.now() + timedelta(minutes=2)),
        ]
        MFE, MAE, _, _, _, _ = engine.calculate_MFE_MAE_from_reference(
            price_points, 100, "long"
        )
        assert MFE >= 0
        assert MAE >= 0


class TestCounterfactualEngine:
    """Tests for Counterfactual Engine"""
    
    def test_counterfactual_engine_initialization(self):
        engine = CounterfactualEngine()
        assert engine is not None
    
    def test_simulate_delayed_entry(self):
        engine = get_counterfactual_engine()
        engine.add_price_point("BTC", datetime.now(), 10000)
        engine.add_price_point("BTC", datetime.now() + timedelta(minutes=1), 10005)
        engine.add_price_point("BTC", datetime.now() + timedelta(minutes=2), 10010)
        
        actual = TradeOutcome(
            pnl=5,
            return_pct=0.5,
            win=True,
            entry_price=10000,
            exit_price=10005,
            entry_time=datetime.now(),
            exit_time=datetime.now() + timedelta(minutes=2)
        )
        
        delayed = engine.simulate_delayed_entry(
            actual.entry_time,
            actual.entry_price,
            actual.exit_time,
            actual.exit_price,
            "BTC",
            timedelta(minutes=1)
        )
        assert delayed is not None


class TestLossAttributionEngine:
    """Tests for Loss Attribution Engine"""
    
    def test_loss_attribution_engine_initialization(self):
        engine = LossAttributionEngine()
        assert engine is not None
    
    def test_classify_loss(self):
        engine = get_loss_attribution_engine()
        context = TradeContext(
            trade_id="test1",
            symbol="BTC/USDT",
            direction="long",
            entry_price=10000,
            exit_price=9900,
            entry_time=datetime.now(),
            exit_time=datetime.now() + timedelta(minutes=10),
            volatility=0.05,
            spread=0.001,
            volume=10000,
            signal_strength=0.1,
            signal_confidence=0.4,
            regime="trend",
            regime_confidence=0.8,
            execution_type="MARKET",
            slippage=0.002,
            fees=0.001,
            news_score=30
        )
        attribution = engine.classify_loss(context)
        assert attribution.primary_cause is not None


class TestOpportunityCostEngine:
    """Tests for Opportunity Cost Engine"""
    
    def test_opportunity_cost_engine_initialization(self):
        engine = OpportunityCostEngine()
        assert engine is not None
    
    def test_optimize_capital_allocation(self):
        engine = get_opportunity_cost_engine()
        opportunities = [
            SignalOpportunity(
                signal_id="sig1",
                symbol="BTC",
                direction="long",
                expected_return=0.05,
                risk=0.02,
                confidence=0.8,
                capital_requirement=1000,
                position_size=1
            ),
            SignalOpportunity(
                signal_id="sig2",
                symbol="ETH",
                direction="long",
                expected_return=0.03,
                risk=0.01,
                confidence=0.9,
                capital_requirement=1000,
                position_size=1
            )
        ]
        allocations = engine.optimize_capital_allocation(
            opportunities, 10000
        )
        assert len(allocations) > 0


class TestPortfolioExposureEngine:
    """Tests for Portfolio Exposure Engine"""
    
    def test_portfolio_exposure_engine_initialization(self):
        engine = PortfolioExposureEngine()
        assert engine is not None
    
    def test_calculate_portfolio_exposure(self):
        engine = get_portfolio_exposure_engine()
        positions = [
            Position("BTC", "long", 1, 10000, 10050),
            Position("ETH", "short", 0.5, 2000, 1990),
        ]
        exposure = engine.calculate_portfolio_exposure(positions)
        assert exposure.gross_exposure > 0
        assert exposure.net_exposure is not None


class TestTailRiskEngine:
    """Tests for Tail Risk Engine"""
    
    def test_tail_risk_engine_initialization(self):
        engine = TailRiskEngine()
        assert engine is not None
    
    def test_calculate_var(self):
        engine = get_tail_risk_engine()
        returns = [0.01, -0.01, 0.02, -0.02, 0.03] * 100
        var = engine.calculate_var(returns, 0.95, "historical")
        assert var.value is not None
    
    def test_calculate_cvar(self):
        engine = get_tail_risk_engine()
        returns = [0.01, -0.01, 0.02, -0.02, 0.03] * 100
        cvar = engine.calculate_cvar(returns, 0.95, "historical")
        assert cvar.value is not None


class TestRegimeSimilarityEngine:
    """Tests for Regime Similarity Engine"""
    
    def test_regime_similarity_engine_initialization(self):
        engine = RegimeSimilarityEngine()
        assert engine is not None
    
    def test_add_and_find_similar_states(self):
        engine = get_regime_similarity_engine()
        
        # Add historical states
        for i in range(10):
            state = MarketState(
                timestamp=datetime.now() - timedelta(days=i),
                features={"volatility": 0.01 + i * 0.001, "volume": 1000 + i * 100},
                regime="trend"
            )
            engine.add_historical_state(state)
        
        # Find similar states
        current_state = MarketState(
            timestamp=datetime.now(),
            features={"volatility": 0.015, "volume": 1500},
            regime="trend"
        )
        
        result = engine.find_similar_states(current_state, limit=5)
        assert result.num_historical_observations == 10
        assert result.similarity_score >= 0


class TestMarketStateClusterer:
    """Tests for Market State Clusterer"""
    
    def test_market_state_clusterer_initialization(self):
        clusterer = MarketStateClusterer()
        assert clusterer is not None
    
    def test_cluster_kmeans(self):
        clusterer = get_market_state_clusterer()
        
        # Add states
        for i in range(50):
            state = MarketStateFeatures(
                timestamp=datetime.now() - timedelta(days=i),
                returns=np.random.normal(0, 0.01),
                volatility=np.random.uniform(0.01, 0.05),
                volume=np.random.uniform(1000, 10000)
            )
            clusterer.add_state(state)
        
        result = clusterer.cluster_kmeans(n_clusters=3)
        assert result.num_clusters == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
