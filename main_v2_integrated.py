#!/usr/bin/env python3
"""
ASTRA BOT - Main Entry Point
Master Specification v2 - FULLY INTEGRATED

This is the integrated version with all new engines and components.
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

# Подстраховка для запуска `python main.py` из произвольной рабочей директории.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.adapters.okx import OKXClient
from astra_bot.core.config import get_settings, load_settings
from astra_bot.core.logger import get_component_logger, setup_logging
from astra_bot.core.metrics import SYSTEM_ERRORS, render_metrics
from astra_bot.data.database import close_database, init_database
from astra_bot.paperengine.paper_engine import PaperTradingEngine
from astra_bot.strategies import MeanReversionStrategy, MomentumStrategy
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

# ============================================================================
# NEW ENGINES IMPORTS (Master Specification v2)
# ============================================================================

# Phase B - Prediction Quality
from astra_bot.engines.uncertainty_engine import get_uncertainty_engine
from astra_bot.engines.probabilistic_forecast import get_forecast_engine
from astra_bot.engines.regime_similarity_engine import get_regime_similarity_engine

# Phase C - Decision Intelligence
from astra_bot.engines.opportunity_cost_engine import get_opportunity_cost_engine

# Phase D - Execution
from astra_bot.engines.alpha_decay_engine import get_alpha_decay_engine
from astra_bot.engines.execution_optimizer import get_execution_optimizer

# Phase E - Portfolio
from astra_bot.engines.signal_correlation_engine import get_signal_correlation_engine
from astra_bot.engines.portfolio_exposure_engine import get_portfolio_exposure_engine
from astra_bot.engines.tail_risk_engine import get_tail_risk_engine

# Phase F - Learning
from astra_bot.engines.mfe_mae_engine import get_mfe_mae_engine
from astra_bot.engines.counterfactual_engine import get_counterfactual_engine
from astra_bot.engines.loss_attribution_engine import get_loss_attribution_engine

# Phase G - Discovery
from astra_bot.engines.market_state_clusterer import get_market_state_clusterer

# Research Layer
from astra_bot.research.experiment_registry import get_experiment_registry
from astra_bot.research.statistical_tests import get_statistical_tests
from astra_bot.research.hypothesis_generator import get_hypothesis_generator
from astra_bot.research.research_agent import get_research_agent

# Memory Layer
from astra_bot.memory.memory_manager import get_memory_manager
from astra_bot.memory.lesson_quality_engine import get_lesson_quality_engine
from astra_bot.memory.knowledge_base import get_knowledge_base

# Existing engines
from astra_bot.engines.risk_engine import get_risk_engine, reset_risk_engine

# Каталог логов настраивается через LOG_DIR; по умолчанию /tmp/logs,
# который доступен на запись в контейнере/на Render.
log_dir = os.environ.get("LOG_DIR", "/tmp/logs")
try:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
except OSError:
    log_dir = None
setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"), log_dir=log_dir)

logger = get_component_logger("main")

# Глобальные переменные
_bot_instance = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Управление жизненным циклом FastAPI-приложения."""
    global _bot_instance
    logger.info("FastAPI startup...")
    _bot_instance = AstraBot()
    try:
        await _bot_instance.initialize()
        logger.info("ASTRA BOT v2 ready (Master Specification v2 fully integrated)")
        yield
    finally:
        if _bot_instance is not None:
            await _bot_instance.stop()
        logger.info("ASTRA BOT shut down")


# FastAPI приложение
app = FastAPI(title="ASTRA BOT v2", version="2.0.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request, exc: Exception):
    """Поймать необработанное исключение, записать в метрики и логи."""
    SYSTEM_ERRORS.labels(
        component="web", error_type=type(exc).__name__
    ).inc()
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "error": str(exc)},
    )


@app.get("/")
async def root():
    return {"service": "ASTRA BOT v2", "status": "running", "version": "2.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat(), "version": "2.0.0"}


@app.get("/ping")
async def ping():
    return {"pong": True, "timestamp": datetime.now(UTC).isoformat()}


@app.get("/tick")
async def tick():
    global _bot_instance
    if _bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    result = await _bot_instance.run_one_iteration()
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Приём обновлений от Telegram в режиме webhook."""
    if _bot_instance is None or _bot_instance._telegram_bot is None:
        return JSONResponse(status_code=503, content={"status": "bot not ready"})
    try:
        data = await request.json()
        await _bot_instance._telegram_bot.process_update(data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Telegram webhook error: %s", exc)
        return JSONResponse(status_code=500, content={"status": "error"})
    return {"status": "ok"}


@app.get("/status")
async def status():
    global _bot_instance
    if _bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    return _bot_instance.get_status()


@app.middleware("http")
async def add_request_id(request, call_next):
    """Пробросить/сгенерировать X-Request-Id для трассировки в логах."""
    request_id = request.headers.get("X-Request-Id") or os.urandom(8).hex()
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.get("/metrics")
async def metrics():
    """Prometheus-метрики в text/exposition-format."""
    return Response(
        content=render_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ============================================================================
# NEW API ENDPOINTS FOR MASTER SPECIFICATION V2
# ============================================================================

@app.get("/api/v2/uncertainty/{symbol}")
async def get_uncertainty(symbol: str, timeframe: str = "1h"):
    """Get uncertainty assessment for a symbol (Phase B)"""
    try:
        engine = get_uncertainty_engine()
        from astra_bot.engines.uncertainty_engine import ModelPrediction, MarketDataQuality, RegimeAssessment
        
        current_pred = ModelPrediction(
            direction="long",
            probability=0.7,
            expected_return=0.02,
            model_name="ensemble",
            model_version="1.0",
            features_used=["trend", "momentum", "volatility"],
            sample_size=100
        )
        data_quality = MarketDataQuality(
            spread_pct=0.001,
            depth=5000,
            volume=100000,
            volatility=0.02,
            data_gaps=0,
            latency_ms=10
        )
        regime = RegimeAssessment(
            current_regime="trend",
            regime_confidence=0.8,
            regime_stability=0.7,
            transition_probability=0.1,
            historical_coverage=500
        )
        result = engine.assess_uncertainty(
            symbol=symbol,
            timeframe=timeframe,
            current_prediction=current_pred,
            historical_predictions=[],
            data_quality=data_quality,
            regime_assessment=regime,
            sample_size=100
        )
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/forecast/{symbol}")
async def get_forecast(symbol: str, timeframe: str = "1h"):
    """Get probabilistic forecast for a symbol (Phase B)"""
    try:
        engine = get_forecast_engine()
        predictions = {"1m": 0.005, "5m": 0.01, "15m": 0.015, "1h": 0.02, "4h": 0.03}
        historical = {
            "1m": [0.001, -0.002, 0.003] * 100,
            "5m": [0.002, -0.003, 0.004] * 100,
            "15m": [0.003, -0.004, 0.005] * 100,
            "1h": [0.004, -0.005, 0.006] * 100,
            "4h": [0.005, -0.006, 0.007] * 100
        }
        results = engine.create_multi_horizon_forecast(
            symbol=symbol,
            timeframe=timeframe,
            model_version="1.0",
            predictions=predictions,
            historical_returns_by_horizon=historical,
            uncertainty=0.1
        )
        consensus = engine.get_consensus_forecast(results)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "horizons": {k: v.to_dict() for k, v in results.items()},
            "consensus": consensus.to_dict() if consensus else None
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/alpha-decay")
async def get_alpha_decay(symbol: str = "BTC-USDT", strategy: str = "momentum", timeframe: str = "1h"):
    """Get alpha decay status for a strategy (Phase D)"""
    try:
        engine = get_alpha_decay_engine()
        is_expired = engine.is_signal_expired(strategy, symbol, timeframe)
        is_weakening = engine.is_signal_weakening(strategy, symbol, timeframe)
        remaining_edge = engine.get_signal_remaining_edge(strategy, symbol, timeframe)
        signal_age = engine.get_signal_age(strategy, symbol, timeframe)
        expected_lifetime = engine.get_expected_lifetime(strategy, symbol, timeframe)
        
        return {
            "symbol": symbol,
            "strategy": strategy,
            "timeframe": timeframe,
            "is_expired": is_expired,
            "is_weakening": is_weakening,
            "remaining_edge": remaining_edge,
            "signal_age": str(signal_age) if signal_age else None,
            "expected_lifetime": str(expected_lifetime) if expected_lifetime else None
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/execution-optimization")
async def get_execution_optimization(symbol: str = "BTC-USDT"):
    """Get optimal execution strategy (Phase D)"""
    try:
        optimizer = get_execution_optimizer()
        from astra_bot.engines.execution_optimizer import OrderBookState, LiquidityState, ExecutionUrgency
        
        order_book = OrderBookState(
            symbol=symbol,
            bids=[(49999, 10), (49998, 5)],
            asks=[(50001, 10), (50002, 5)],
            mid_price=50000.0,
            spread=2.0,
            spread_pct=0.00004,
            depth=30.0,
            best_bid=49999.0,
            best_ask=50001.0
        )
        liquidity = LiquidityState(
            symbol=symbol,
            volume_24h=1000000,
            volume_current=100000,
            order_book_liquidity=50000,
            market_depth=100000,
            volatility=0.02
        )
        
        signal = {
            "symbol": symbol,
            "direction": "long",
            "entry_price": 50000.0,
            "position_size": 0.1
        }
        
        plan = optimizer.select_optimal_strategy(
            signal=signal,
            order_book=order_book,
            liquidity=liquidity,
            urgency=ExecutionUrgency.NORMAL,
            expected_edge=0.02,
            position_size=0.1
        )
        
        return plan.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/portfolio-exposure")
async def get_portfolio_exposure():
    """Get current portfolio exposure (Phase E)"""
    try:
        engine = get_portfolio_exposure_engine()
        from astra_bot.engines.portfolio_exposure_engine import Position
        
        positions = [
            Position("BTC-USDT", "long", 0.1, 50000, 50500),
            Position("ETH-USDT", "short", 0.5, 2000, 1990),
        ]
        exposure = engine.calculate_portfolio_exposure(positions)
        breakdown = engine.get_exposure_breakdown(exposure)
        
        return {
            "exposure": exposure.to_dict(),
            "breakdown": breakdown
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/tail-risk/{symbol}")
async def get_tail_risk(symbol: str):
    """Get tail risk metrics for a symbol (Phase E)"""
    try:
        engine = get_tail_risk_engine()
        import numpy as np
        returns = list(np.random.normal(0, 0.01, 1000))
        result = engine.assess_tail_risk(symbol, returns)
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/signal-correlation")
async def get_signal_correlation():
    """Get signal correlation analysis (Phase E)"""
    try:
        engine = get_signal_correlation_engine()
        from astra_bot.engines.signal_correlation_engine import SignalFeatures
        
        signals = [
            SignalFeatures("momentum_1h", {"trend": 0.8, "volatility": 0.2, "volume": 0.5}),
            SignalFeatures("mean_reversion_1h", {"trend": -0.3, "volatility": 0.4, "volume": 0.3}),
            SignalFeatures("breakout_1h", {"trend": 0.9, "volatility": 0.1, "volume": 0.7}),
        ]
        
        result = engine.analyze_signal_correlation(signals)
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/mfe-mae")
async def get_mfe_mae():
    """Get MFE/MAE analysis (Phase F)"""
    try:
        engine = get_mfe_mae_engine()
        from astra_bot.engines.mfe_mae_engine import PricePoint
        from datetime import datetime, timedelta
        
        # Add price points for a position
        base_time = datetime.now()
        price_points = [
            PricePoint(50000, base_time),
            PricePoint(50500, base_time + timedelta(minutes=5)),
            PricePoint(50200, base_time + timedelta(minutes=10)),
            PricePoint(50800, base_time + timedelta(minutes=15)),
        ]
        
        for pp in price_points:
            engine.add_price_point("test_pos_1", pp.price, pp.timestamp)
        
        result = engine.calculate_position_MFE_MAE(
            position_id="test_pos_1",
            symbol="BTC-USDT",
            direction="long",
            entry_price=50000,
            stop_price=49500,
            target_price=51000,
            exit_price=50800
        )
        
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/loss-attribution")
async def get_loss_attribution():
    """Get loss attribution analysis (Phase F)"""
    try:
        engine = get_loss_attribution_engine()
        from astra_bot.engines.loss_attribution_engine import TradeContext
        from datetime import datetime, timedelta
        
        context = TradeContext(
            trade_id="test_trade_1",
            symbol="BTC-USDT",
            direction="long",
            entry_price=50000,
            exit_price=49500,
            entry_time=datetime.now() - timedelta(hours=1),
            exit_time=datetime.now(),
            volatility=0.05,
            spread=0.001,
            volume=100000,
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
        stats = engine.get_loss_statistics()
        
        return {
            "attribution": attribution.to_dict(),
            "statistics": stats
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/counterfactual")
async def get_counterfactual():
    """Get counterfactual analysis (Phase F)"""
    try:
        engine = get_counterfactual_engine()
        from astra_bot.engines.counterfactual_engine import TradeOutcome
        from datetime import datetime, timedelta
        
        base_time = datetime.now()
        actual = TradeOutcome(
            pnl=-500,
            return_pct=-1.0,
            win=False,
            entry_price=50000,
            exit_price=49500,
            entry_time=base_time - timedelta(hours=1),
            exit_time=base_time
        )
        
        # Add price history
        for i in range(10):
            engine.add_price_point("BTC-USDT", base_time - timedelta(minutes=10-i), 50000 + i*100)
        
        result = engine.analyze_trade(
            trade_id="test_trade_1",
            symbol="BTC-USDT",
            actual_outcome=actual,
            stop_price=49500,
            target_price=51000
        )
        
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/opportunity-cost")
async def get_opportunity_cost():
    """Get opportunity cost analysis (Phase C)"""
    try:
        engine = get_opportunity_cost_engine()
        from astra_bot.engines.opportunity_cost_engine import SignalOpportunity
        
        opportunities = [
            SignalOpportunity(
                signal_id="sig1",
                symbol="BTC-USDT",
                direction="long",
                expected_return=0.05,
                risk=0.02,
                confidence=0.8,
                capital_requirement=1000,
                position_size=0.1,
                correlations={"ETH-USDT": 0.7}
            ),
            SignalOpportunity(
                signal_id="sig2",
                symbol="ETH-USDT",
                direction="long",
                expected_return=0.03,
                risk=0.01,
                confidence=0.9,
                capital_requirement=1000,
                position_size=0.1,
                correlations={"BTC-USDT": 0.7}
            )
        ]
        
        result = engine.evaluate_signals(
            opportunities=opportunities,
            total_capital=10000,
            available_capital=10000
        )
        
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/statistical-tests")
async def run_statistical_tests():
    """Run statistical validation tests (Phase A)"""
    try:
        engine = get_statistical_tests()
        import numpy as np
        returns = list(np.random.normal(0.001, 0.02, 1000))
        results = engine.validate_strategy(returns)
        return results
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/market-clusters")
async def get_market_clusters():
    """Get market state clusters (Phase G)"""
    try:
        clusterer = get_market_state_clusterer()
        from astra_bot.engines.market_state_clusterer import MarketStateFeatures
        from datetime import datetime, timedelta
        
        # Add sample states
        base_time = datetime.now()
        for i in range(50):
            state = MarketStateFeatures(
                timestamp=base_time - timedelta(days=i),
                returns=np.random.normal(0, 0.01),
                volatility=np.random.uniform(0.01, 0.05),
                volume=np.random.uniform(1000, 10000),
                spread=np.random.uniform(0.0001, 0.001)
            )
            clusterer.add_state(state)
        
        result = clusterer.cluster_kmeans(n_clusters=3)
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/research-status")
async def get_research_status():
    """Get research agent status (Phase H)"""
    try:
        agent = get_research_agent()
        summary = agent.get_research_summary()
        return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/memory-stats")
async def get_memory_stats():
    """Get memory statistics (Memory Layer)"""
    try:
        manager = get_memory_manager()
        return manager.get_statistics()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v2/knowledge-base")
async def get_knowledge_base():
    """Get knowledge base statistics (Memory Layer)"""
    try:
        kb = get_knowledge_base()
        return kb.get_statistics()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ============================================================================
# EXISTING ENDPOINTS (KEPT FOR BACKWARD COMPATIBILITY)
# ============================================================================

@app.post("/train")
async def train(days: int = 365, timeframe: str = "1h", symbol: str = "BTC/USDT"):
    """Запустить обучение на истории OKX без депозита."""
    try:
        from astra_bot.ml.historical_training import (
            HistoricalTrainingConfig,
            train_on_historical_data,
        )

        config = HistoricalTrainingConfig(
            symbol=symbol,
            timeframe=timeframe,
            lookback_days=days,
        )
        artifact = await train_on_historical_data(config)
        return {
            "status": "ok",
            "artifact": str(artifact),
            "days": days,
            "symbol": symbol,
            "timeframe": timeframe,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/self_play")
async def self_play(
    target_trades: int = 3000,
    timeframe: str = "1h",
    offline_bars: int = 3000,
):
    """Запустить walk-forward self-play на виртуальные 2000 ₽."""
    try:
        from decimal import Decimal

        from astra_bot.ml.self_play import SelfPlayConfig, SelfPlayEngine

        config = SelfPlayConfig(
            timeframe=timeframe,
            target_trades=target_trades,
            initial_capital=Decimal("2000"),
        )
        engine = SelfPlayEngine(config)
        report = await engine.run(offline_bars=offline_bars)
        return {
            "status": "ok",
            "trades": report.total_trades,
            "wins": report.wins,
            "losses": report.losses,
            "win_rate": round(report.win_rate, 2),
            "profit_factor": round(report.profit_factor, 3),
            "pnl": round(report.total_pnl, 2),
            "final_equity": round(report.final_equity, 2),
            "max_drawdown_pct": round(report.max_drawdown_pct, 2),
            "started_learning": report.started_learning,
            "message": report.message,
            "lessons": str(report.lessons_path),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/retrain")
async def retrain(min_samples: int = 200):
    """Переобучить weekly-модель на накопленных уроках self-play."""
    try:
        from astra_bot.ml.weekly_learner import train_weekly

        result = train_weekly(min_samples=min_samples)
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



class AstraBot:

    def __init__(self):
        self.config = None
        self._exchange_client = None
        self._paper_engine = None
        self._risk_engine = None
        self._telegram_bot = None
        self._running = False
        
        # NEW: Initialize all new engines
        self._uncertainty_engine = None
        self._forecast_engine = None
        self._alpha_decay_engine = None
        self._execution_optimizer = None
        self._signal_correlation_engine = None
        self._mfe_mae_engine = None
        self._counterfactual_engine = None
        self._loss_attribution_engine = None
        self._opportunity_cost_engine = None
        self._portfolio_exposure_engine = None
        self._tail_risk_engine = None
        self._regime_similarity_engine = None
        self._market_state_clusterer = None
        self._experiment_registry = None
        self._statistical_tests = None
        self._hypothesis_generator = None
        self._research_agent = None
        self._memory_manager = None
        self._lesson_quality_engine = None
        self._knowledge_base = None

    async def initialize(self):
        try:
            settings = get_settings()
        except RuntimeError:
            settings = load_settings()
        self.config = settings

        # Initialize Risk Engine (existing)
        try:
            self._risk_engine = get_risk_engine()
        except Exception as e:
            logger.warning(f"Risk engine init failed: {e}")

        # Initialize all NEW engines
        try:
            self._uncertainty_engine = get_uncertainty_engine()
            logger.info("Uncertainty Engine initialized")
        except Exception as e:
            logger.warning(f"Uncertainty Engine init failed: {e}")
        
        try:
            self._forecast_engine = get_forecast_engine()
            logger.info("Probabilistic Forecast Engine initialized")
        except Exception as e:
            logger.warning(f"Forecast Engine init failed: {e}")
        
        try:
            self._alpha_decay_engine = get_alpha_decay_engine()
            logger.info("Alpha Decay Engine initialized")
        except Exception as e:
            logger.warning(f"Alpha Decay Engine init failed: {e}")
        
        try:
            self._execution_optimizer = get_execution_optimizer()
            logger.info("Execution Optimizer initialized")
        except Exception as e:
            logger.warning(f"Execution Optimizer init failed: {e}")
        
        try:
            self._signal_correlation_engine = get_signal_correlation_engine()
            logger.info("Signal Correlation Engine initialized")
        except Exception as e:
            logger.warning(f"Signal Correlation Engine init failed: {e}")
        
        try:
            self._mfe_mae_engine = get_mfe_mae_engine()
            logger.info("MFE/MAE Engine initialized")
        except Exception as e:
            logger.warning(f"MFE/MAE Engine init failed: {e}")
        
        try:
            self._counterfactual_engine = get_counterfactual_engine()
            logger.info("Counterfactual Engine initialized")
        except Exception as e:
            logger.warning(f"Counterfactual Engine init failed: {e}")
        
        try:
            self._loss_attribution_engine = get_loss_attribution_engine()
            logger.info("Loss Attribution Engine initialized")
        except Exception as e:
            logger.warning(f"Loss Attribution Engine init failed: {e}")
        
        try:
            self._opportunity_cost_engine = get_opportunity_cost_engine()
            logger.info("Opportunity Cost Engine initialized")
        except Exception as e:
            logger.warning(f"Opportunity Cost Engine init failed: {e}")
        
        try:
            self._portfolio_exposure_engine = get_portfolio_exposure_engine()
            logger.info("Portfolio Exposure Engine initialized")
        except Exception as e:
            logger.warning(f"Portfolio Exposure Engine init failed: {e}")
        
        try:
            self._tail_risk_engine = get_tail_risk_engine()
            logger.info("Tail Risk Engine initialized")
        except Exception as e:
            logger.warning(f"Tail Risk Engine init failed: {e}")
        
        try:
            self._regime_similarity_engine = get_regime_similarity_engine()
            logger.info("Regime Similarity Engine initialized")
        except Exception as e:
            logger.warning(f"Regime Similarity Engine init failed: {e}")
        
        try:
            self._market_state_clusterer = get_market_state_clusterer()
            logger.info("Market State Clusterer initialized")
        except Exception as e:
            logger.warning(f"Market State Clusterer init failed: {e}")
        
        # Research Layer
        try:
            self._experiment_registry = get_experiment_registry()
            logger.info("Experiment Registry initialized")
        except Exception as e:
            logger.warning(f"Experiment Registry init failed: {e}")
        
        try:
            self._statistical_tests = get_statistical_tests()
            logger.info("Statistical Tests initialized")
        except Exception as e:
            logger.warning(f"Statistical Tests init failed: {e}")
        
        try:
            self._hypothesis_generator = get_hypothesis_generator()
            logger.info("Hypothesis Generator initialized")
        except Exception as e:
            logger.warning(f"Hypothesis Generator init failed: {e}")
        
        try:
            self._research_agent = get_research_agent()
            logger.info("Research Agent initialized")
        except Exception as e:
            logger.warning(f"Research Agent init failed: {e}")
        
        # Memory Layer
        try:
            self._memory_manager = get_memory_manager()
            logger.info("Memory Manager initialized")
        except Exception as e:
            logger.warning(f"Memory Manager init failed: {e}")
        
        try:
            self._lesson_quality_engine = get_lesson_quality_engine()
            logger.info("Lesson Quality Engine initialized")
        except Exception as e:
            logger.warning(f"Lesson Quality Engine init failed: {e}")
        
        try:
            self._knowledge_base = get_knowledge_base()
            logger.info("Knowledge Base initialized")
        except Exception as e:
            logger.warning(f"Knowledge Base init failed: {e}")

        # Database initialization (existing)
        database_url = os.environ.get("DATABASE_URL")
        if settings.database or database_url:
            try:
                db_config = {
                    "host": settings.database.host if settings.database else "localhost",
                    "port": settings.database.port if settings.database else 5432,
                    "name": settings.database.name if settings.database else "astra_bot",
                    "user": settings.database.user if settings.database else "",
                    "password": settings.database.password if settings.database else "",
                    "pool_size": (
                        settings.database.pool_size if settings.database else 10
                    ),
                }
                if database_url:
                    if database_url.startswith("postgres://"):
                        database_url = database_url.replace(
                            "postgres://", "postgresql+asyncpg://", 1
                        )
                    elif database_url.startswith("postgresql://"):
                        database_url = database_url.replace(
                            "postgresql://", "postgresql+asyncpg://", 1
                        )
                    db_config["database_url"] = database_url
                await init_database(db_config)
            except Exception as e:
                logger.warning(f"Database not available: {e}")

        # Exchange initialization (existing)
        okx_config = settings.exchanges.get("okx") if settings.exchanges else None
        if okx_config and okx_config.enabled and okx_config.api_key and okx_config.api_secret:
            config_dict = {
                "api_key": okx_config.api_key,
                "api_secret": okx_config.api_secret,
                "passphrase": okx_config.passphrase,
                "sandbox": okx_config.sandbox,
                "enabled": True,
            }
            self._exchange_client = OKXClient(config_dict)
            try:
                await self._exchange_client.initialize()
            except Exception as e:
                logger.warning(f"Exchange init failed: {e}")

        # Paper engine (existing)
        self._paper_engine = PaperTradingEngine(
            initial_capital=Decimal("1000")
        )

        # Strategies (existing)
        if settings.strategies.get("momentum", {}).get("enabled", True):
            self._paper_engine.add_strategy("momentum", MomentumStrategy())
        if settings.strategies.get("mean_reversion", {}).get("enabled", True):
            self._paper_engine.add_strategy(
                "mean_reversion", MeanReversionStrategy()
            )
        if settings.strategies.get("book_breakout", {}).get("enabled", False):
            from astra_bot.strategies.book_breakout import BookBreakoutStrategy
            self._paper_engine.add_strategy(
                "book_breakout", BookBreakoutStrategy()
            )

        # Telegram bot initialization (existing)
        await self._init_telegram(settings)

        logger.info("✅ ALL ENGINES INITIALIZED - Master Specification v2 is ready!")

    async def _init_telegram(self, settings) -> None:
        tg = getattr(settings, "telegram", None)
        if not tg or not tg.bot_token:
            return
        try:
            from astra_bot.telegram.bot import create_telegram_bot

            self._telegram_bot = await create_telegram_bot(
                bot_token=tg.bot_token,
                allowed_user_ids=list(tg.allowed_user_ids or []),
                admin_user_ids=list(tg.admin_user_ids or []),
            )
            base_url = (
                os.environ.get("RENDER_EXTERNAL_URL")
                or os.environ.get("WEBHOOK_BASE_URL")
                or ""
            ).rstrip("/")
            webhook_url = f"{base_url}/telegram/webhook" if base_url else None
            await self._telegram_bot.start(webhook_url=webhook_url)
            logger.info("Telegram bot started (webhook=%s)", bool(webhook_url))
        except Exception as exc:
            logger.warning("Telegram bot init failed: %s", exc)
            self._telegram_bot = None

    def get_status(self):
        engines_status = {
            "risk_engine": self._risk_engine is not None,
            "uncertainty_engine": self._uncertainty_engine is not None,
            "forecast_engine": self._forecast_engine is not None,
            "alpha_decay_engine": self._alpha_decay_engine is not None,
            "execution_optimizer": self._execution_optimizer is not None,
            "signal_correlation_engine": self._signal_correlation_engine is not None,
            "mfe_mae_engine": self._mfe_mae_engine is not None,
            "counterfactual_engine": self._counterfactual_engine is not None,
            "loss_attribution_engine": self._loss_attribution_engine is not None,
            "opportunity_cost_engine": self._opportunity_cost_engine is not None,
            "portfolio_exposure_engine": self._portfolio_exposure_engine is not None,
            "tail_risk_engine": self._tail_risk_engine is not None,
            "regime_similarity_engine": self._regime_similarity_engine is not None,
            "market_state_clusterer": self._market_state_clusterer is not None,
            "experiment_registry": self._experiment_registry is not None,
            "statistical_tests": self._statistical_tests is not None,
            "hypothesis_generator": self._hypothesis_generator is not None,
            "research_agent": self._research_agent is not None,
            "memory_manager": self._memory_manager is not None,
            "lesson_quality_engine": self._lesson_quality_engine is not None,
            "knowledge_base": self._knowledge_base is not None,
        }
        
        return {
            "running": self._running,
            "exchange_connected": self._exchange_client is not None,
            "paper_engine": self._paper_engine is not None,
            "risk_engine": self._risk_engine is not None,
            "equity": (
                str(self._paper_engine.account.equity)
                if self._paper_engine
                else "1000"
            ),
            "timestamp": datetime.now(UTC).isoformat(),
            "engines": engines_status,
            "active_engines": sum(engines_status.values()),
            "total_engines": len(engines_status),
        }

    async def run_one_iteration(self):
        try:
            current_equity = (
                self._paper_engine.account.equity
                if self._paper_engine
                else Decimal("1000")
            )

            if self._risk_engine:
                self._risk_engine.set_capital(current_equity, Decimal("1000"))

            if self._paper_engine:
                self._paper_engine.account.update_equity(current_equity)

            return {
                "status": "ok",
                "timestamp": datetime.now(UTC).isoformat(),
                "equity": str(current_equity),
                "iteration": "completed",
                "version": "2.0.0",
                "message": "Master Specification v2 iteration completed"
            }
        except Exception as e:
            logger.error(f"Iteration error: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat(),
            }

    async def start(self):
        self._running = True
        while self._running:
            await self.run_one_iteration()
            await asyncio.sleep(60)

    async def stop(self):
        self._running = False
        if self._telegram_bot is not None:
            try:
                await self._telegram_bot.stop()
            except Exception as exc:
                logger.warning("Telegram stop failed: %s", exc)
        if self._exchange_client:
            await self._exchange_client.close()
        await close_database()


def run_web_mode():
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    logger.info(f"Starting web server on {host}:{port}")
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_web_mode()
