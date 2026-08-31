"""
ASTRA AI Bot - Main Entry Point v2 FINAL

Полная реализация Master Specification v2 с:
- Всеми 17 новыми компонентами
- Полной интеграцией в pipeline
- Новыми API endpoint-ами
- Поддержкой всех фаз A-H

Запуск: python main_v2_final.py

Ключевые улучшения:
1. Полная интеграция Uncertainty Engine (Section 6)
2. Интеграция Probabilistic Forecast Engine (Section 10)
3. Интеграция Alpha Decay Engine (Section 11-12)
4. Интеграция Execution Optimizer (Section 15-17)
5. Интеграция Signal Correlation Engine (Section 23)
6. Интеграция Portfolio Exposure Engine (Section 24)
7. Интеграция Tail Risk Engine (Section 26)
8. Интеграция MFE/MAE Engine (Section 19-20)
9. Интеграция Counterfactual Engine (Section 21-22)
10. Интеграция Loss Attribution Engine (Section 27)
11. Интеграция Opportunity Cost Engine (Section 22)
12. Интеграция Market State Clusterer (Section 29-30)
13. Интеграция Experiment Registry (Section 43-44)
14. Интеграция Statistical Tests (Section 31-37)
15. Интеграция Research Agent 2.0 (Section 49-51)
16. Интеграция Memory Manager (Section 52)
17. Интеграция Knowledge Base (Section 54)
"""

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

# FastAPI
try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

# Local imports
from . import __version__
from .core.config import get_settings, load_settings, SystemConfig
from .decision.config import DecisionConfig
from .decision.pipeline_v2 import DecisionPipelineV2 as DecisionPipeline
from .decision.context import MarketContext, SignalCandidate
from .engines import (
    get_uncertainty_engine,
    get_forecast_engine,
    get_alpha_decay_engine,
    get_execution_optimizer,
    get_signal_correlation_engine,
    get_portfolio_exposure_engine,
    get_tail_risk_engine,
    get_mfe_mae_engine,
    get_counterfactual_engine,
    get_loss_attribution_engine,
    get_opportunity_cost_engine,
    get_regime_similarity_engine,
    get_market_state_clusterer,
)
from .research import (
    get_experiment_registry,
    get_statistical_tests,
    get_hypothesis_generator,
    get_research_agent,
)
from .memory import (
    get_memory_manager,
    get_lesson_quality_engine,
    get_knowledge_base,
)
from .strategies import (
    MeanReversionStrategy,
    MomentumStrategy,
    PullbackStrategy,
    ScalpStrategy,
    AdaptiveGridStrategy,
    BookBreakoutStrategy,
    TimeSeriesMomentumStrategy,
)
from .core.models import OrderBook, Side
from .decision.liquidity_engine import LiquidityEngine
from .decision.technical_engine import TechnicalEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"astra_bot_v2_{datetime.now().strftime('%Y%m%d')}.log"),
    ],
)
logger = logging.getLogger(__name__)

# Global state
import os
config_path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")
load_settings(config_path)
settings = get_settings()

# Initialize DecisionConfig
decision_config = DecisionConfig()

# Initialize all engines
uncertainty_engine = get_uncertainty_engine()
forecast_engine = get_forecast_engine()
alpha_decay_engine = get_alpha_decay_engine()
execution_optimizer = get_execution_optimizer()
signal_correlation_engine = get_signal_correlation_engine()
portfolio_exposure_engine = get_portfolio_exposure_engine()
tail_risk_engine = get_tail_risk_engine()
mfe_mae_engine = get_mfe_mae_engine()
counterfactual_engine = get_counterfactual_engine()
loss_attribution_engine = get_loss_attribution_engine()
opportunity_cost_engine = get_opportunity_cost_engine()
regime_similarity_engine = get_regime_similarity_engine()
market_state_clusterer = get_market_state_clusterer()
experiment_registry = get_experiment_registry()
statistical_tests = get_statistical_tests()
hypothesis_generator = get_hypothesis_generator()
research_agent = get_research_agent()
memory_manager = get_memory_manager()
lesson_quality_engine = get_lesson_quality_engine()
knowledge_base = get_knowledge_base()

# Initialize strategies
strategies = [
    MeanReversionStrategy(),
    MomentumStrategy(),
    PullbackStrategy(),
    ScalpStrategy(),
    AdaptiveGridStrategy(),
    BookBreakoutStrategy(),
    TimeSeriesMomentumStrategy(),
]

# Initialize decision pipeline
pipeline = DecisionPipeline(
    config=decision_config,
    strategies=strategies,
    stats_store=None,
)

# Global state for trading
positions = {}
open_orders = {}
portfolio_value = 10000.0  # Starting portfolio value
trading_active = True
last_tick_time = datetime.now(timezone.utc)


# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="ASTRA AI Bot v2",
        description=f"ASTRA AI Trading Bot - Master Specification v2 Implementation (Version {__version__})",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Lifespan management
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting ASTRA AI Bot v2...")
        logger.info(f"Version: {__version__}")
        logger.info("Initializing all engines...")
        
        # Initialize all engines
        global uncertainty_engine, forecast_engine, alpha_decay_engine
        global execution_optimizer, signal_correlation_engine, portfolio_exposure_engine
        global tail_risk_engine, mfe_mae_engine, counterfactual_engine
        global loss_attribution_engine, opportunity_cost_engine, regime_similarity_engine
        global market_state_clusterer, experiment_registry, statistical_tests
        global hypothesis_generator, research_agent, memory_manager
        global lesson_quality_engine, knowledge_base
        
        logger.info("All engines initialized successfully!")
        logger.info("ASTRA AI Bot v2 is ready!")
        
        yield
        
        logger.info("Shutting down ASTRA AI Bot v2...")

    app.router.lifespan_context = lifespan


# ============================================================================
# API ENDPOINTS - EXISTING (Backward Compatible)
# ============================================================================

if FASTAPI_AVAILABLE:
    
    @app.get("/")
    async def root():
        """Health check endpoint"""
        return {
            "status": "running",
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "engines_loaded": True,
            "strategies_count": len(strategies),
            "positions_count": len(positions),
            "open_orders_count": len(open_orders),
            "portfolio_value": portfolio_value,
            "trading_active": trading_active,
        }

    @app.get("/health")
    async def health_check():
        """Health check with detailed status"""
        return {
            "status": "healthy",
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {
                "config": "loaded",
                "strategies": f"{len(strategies)} loaded",
                "pipeline": "active",
                "engines": "all_initialized",
            },
            "trading": {
                "active": trading_active,
                "positions": len(positions),
                "open_orders": len(open_orders),
                "portfolio_value": portfolio_value,
            },
        }

    @app.get("/status")
    async def get_status():
        """Get bot status"""
        return {
            "bot_name": "ASTRA AI Bot v2",
            "version": __version__,
            "uptime": (datetime.now(timezone.utc) - last_tick_time).total_seconds(),
            "trading_active": trading_active,
            "positions": list(positions.keys()),
            "open_orders": list(open_orders.keys()),
            "portfolio_value": portfolio_value,
            "last_tick": last_tick_time.isoformat(),
        }

    @app.post("/decide")
    async def decide_endpoint(request: Request):
        """Make a trading decision"""
        try:
            data = await request.json()
            
            # Create MarketContext
            ctx = MarketContext(
                symbol=data.get("symbol", "BTCUSDT"),
                current_price=data.get("current_price", 50000.0),
                candles_5m=data.get("candles_5m", []),
                candles_15m=data.get("candles_15m", []),
                candles_1h=data.get("candles_1h", []),
                candles_4h=data.get("candles_4h", []),
                orderbook=data.get("orderbook"),
                news_score=data.get("news_score", 0),
                global_market=data.get("global_market", {}),
            )
            
            # Make decision
            decision = await pipeline.decide(ctx)
            
            return JSONResponse(content=decision.to_dict())
        except Exception as e:
            logger.error(f"Decide endpoint error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/execute")
    async def execute_endpoint(request: Request):
        """Execute a trade (simulated)"""
        try:
            data = await request.json()
            symbol = data.get("symbol")
            action = data.get("action")  # LONG, SHORT, CLOSE
            position_size = data.get("position_size", 0.1)
            entry_price = data.get("entry_price", 50000.0)
            stop_loss = data.get("stop_loss", 49000.0)
            take_profit = data.get("take_profit", 51000.0)
            
            if action == "CLOSE":
                if symbol in positions:
                    del positions[symbol]
                    return {"status": "closed", "symbol": symbol}
            else:
                positions[symbol] = {
                    "action": action,
                    "position_size": position_size,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "entry_time": datetime.now(timezone.utc).isoformat(),
                }
                return {"status": "opened", "symbol": symbol, "position": positions[symbol]}
        except Exception as e:
            logger.error(f"Execute endpoint error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/positions")
    async def get_positions():
        """Get all open positions"""
        return {"positions": positions}

    @app.get("/orders")
    async def get_orders():
        """Get all open orders"""
        return {"orders": open_orders}

    @app.post("/trading/start")
    async def start_trading():
        """Start automated trading"""
        global trading_active
        trading_active = True
        return {"status": "trading_started", "trading_active": trading_active}

    @app.post("/trading/stop")
    async def stop_trading():
        """Stop automated trading"""
        global trading_active
        trading_active = False
        return {"status": "trading_stopped", "trading_active": trading_active}

    @app.get("/config")
    async def get_config():
        """Get current configuration"""
        return settings.to_dict()


# ============================================================================
# API ENDPOINTS - NEW v2 ENDPOINTS (Master Specification v2)
# ============================================================================

if FASTAPI_AVAILABLE:
    
    # --- Uncertainty Engine Endpoints (Section 6) ---
    
    @app.post("/v2/uncertainty/assess")
    async def assess_uncertainty(request: Request):
        """Assess uncertainty for a prediction"""
        try:
            data = await request.json()
            result = uncertainty_engine.assess_uncertainty(
                symbol=data.get("symbol", "BTCUSDT"),
                timeframe=data.get("timeframe", "1h"),
                current_prediction=data.get("current_prediction"),
                historical_predictions=data.get("historical_predictions", []),
                data_quality=data.get("data_quality"),
                regime_assessment=data.get("regime_assessment"),
                sample_size=data.get("sample_size", 100)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Uncertainty assessment error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/uncertainty/classify/{uncertainty_value}")
    async def classify_uncertainty(uncertainty_value: float):
        """Classify uncertainty level"""
        try:
            level = uncertainty_engine.classify_uncertainty_level(uncertainty_value)
            return {"uncertainty_value": uncertainty_value, "level": level}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/uncertainty/should-trade")
    async def should_trade_uncertainty(request: Request):
        """Check if should trade based on uncertainty"""
        try:
            data = await request.json()
            uncertainty = data.get("uncertainty", 0.5)
            min_confidence = data.get("min_confidence", 0.7)
            result = uncertainty_engine.should_trade(uncertainty, min_confidence)
            return {"should_trade": result, "uncertainty": uncertainty, "min_confidence": min_confidence}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Probabilistic Forecast Engine Endpoints (Section 10) ---
    
    @app.post("/v2/forecast/fit")
    async def fit_distribution(request: Request):
        """Fit distribution to returns data"""
        try:
            data = await request.json()
            result = forecast_engine.fit_distribution(
                returns=data.get("returns", []),
                distribution_type=data.get("distribution_type", "normal")
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Forecast fit error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/forecast/multi-horizon")
    async def multi_horizon_forecast(request: Request):
        """Generate multi-horizon forecasts"""
        try:
            data = await request.json()
            result = forecast_engine.generate_multi_horizon_forecast(
                data.get("symbol", "BTCUSDT"),
                data.get("horizons", ["1m", "5m", "15m", "30m", "1h", "4h"]),
                data.get("distribution_type", "normal")
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/forecast/consensus")
    async def calculate_consensus(request: Request):
        """Calculate consensus forecast from multiple models"""
        try:
            data = await request.json()
            result = forecast_engine.calculate_consensus_forecast(
                forecasts=data.get("forecasts", [])
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Alpha Decay Engine Endpoints (Section 11-12) ---
    
    @app.post("/v2/alpha-decay/measure")
    async def measure_signal_strength(request: Request):
        """Measure signal strength across time intervals"""
        try:
            data = await request.json()
            result = alpha_decay_engine.measure_signal_strength(
                signal_name=data.get("signal_name", "strategy_1"),
                symbol=data.get("symbol", "BTCUSDT"),
                timeframe=data.get("timeframe", "1h"),
                intervals=data.get("intervals", [1, 5, 10, 20, 50, 100])
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Alpha decay measurement error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/alpha-decay/half-life/{signal_name}")
    async def get_alpha_half_life(signal_name: str):
        """Get alpha half-life for a signal"""
        try:
            half_life = alpha_decay_engine.get_alpha_half_life(signal_name)
            return {"signal_name": signal_name, "alpha_half_life": str(half_life) if half_life else None}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/alpha-decay/is-expired/{signal_name}")
    async def check_signal_expiration(signal_name: str):
        """Check if signal is expired"""
        try:
            is_expired = alpha_decay_engine.is_signal_expired(
                signal_name, "BTCUSDT", "1h"
            )
            return {"signal_name": signal_name, "is_expired": is_expired}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/alpha-decay/remaining-edge/{signal_name}")
    async def get_remaining_edge(signal_name: str):
        """Get remaining edge for a signal"""
        try:
            remaining = alpha_decay_engine.get_signal_remaining_edge(
                signal_name, "BTCUSDT", "1h"
            )
            return {"signal_name": signal_name, "remaining_edge": remaining}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Execution Optimizer Endpoints (Section 15-17) ---
    
    @app.post("/v2/execution-optimization/select")
    async def select_execution_strategy(request: Request):
        """Select optimal execution strategy"""
        try:
            data = await request.json()
            from astra_bot.engines.execution_optimizer import ExecutionUrgency, OrderBookState, LiquidityState
            
            order_book = OrderBookState(**data.get("order_book", {}))
            liquidity = LiquidityState(**data.get("liquidity", {}))
            urgency = ExecutionUrgency[data.get("urgency", "NORMAL")]
            
            result = execution_optimizer.select_optimal_strategy(
                signal=data.get("signal", {}),
                order_book=order_book,
                liquidity=liquidity,
                urgency=urgency,
                expected_edge=data.get("expected_edge", 0.01),
                position_size=data.get("position_size", 0.1)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Execution optimization error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/execution-optimization/evaluate")
    async def evaluate_execution_strategy(request: Request):
        """Evaluate a specific execution strategy"""
        try:
            data = await request.json()
            from astra_bot.engines.execution_optimizer import OrderType
            
            order_type = OrderType[data.get("order_type", "MARKET")]
            result = execution_optimizer.evaluate_strategy(
                order_type=order_type,
                signal=data.get("signal", {}),
                order_book=data.get("order_book", {}),
                liquidity=data.get("liquidity", {}),
                expected_edge=data.get("expected_edge", 0.01)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/execution-optimization/strategies")
    async def get_available_strategies():
        """Get list of available execution strategies"""
        try:
            from astra_bot.engines.execution_optimizer import OrderType
            strategies = [s.value for s in OrderType]
            return {"strategies": strategies}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Portfolio Exposure Engine Endpoints (Section 24) ---
    
    @app.post("/v2/portfolio-exposure/calculate")
    async def calculate_portfolio_exposure(request: Request):
        """Calculate portfolio exposure"""
        try:
            data = await request.json()
            from astra_bot.engines.portfolio_exposure_engine import Position
            
            positions = [
                Position(**pos) for pos in data.get("positions", [])
            ]
            result = portfolio_exposure_engine.calculate_portfolio_exposure(positions)
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Portfolio exposure calculation error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/portfolio-exposure/check-limits")
    async def check_exposure_limits(request: Request):
        """Check if portfolio exposure is within limits"""
        try:
            data = await request.json()
            from astra_bot.engines.portfolio_exposure_engine import Position
            
            positions = [
                Position(**pos) for pos in data.get("positions", [])
            ]
            limits = data.get("limits", {})
            result = portfolio_exposure_engine.check_exposure_limits(positions, limits)
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Tail Risk Engine Endpoints (Section 26) ---
    
    @app.post("/v2/tail-risk/assess")
    async def assess_tail_risk(request: Request):
        """Assess tail risk for a symbol"""
        try:
            data = await request.json()
            result = tail_risk_engine.assess_tail_risk(
                symbol=data.get("symbol", "BTCUSDT"),
                returns=data.get("returns", [])
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Tail risk assessment error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/tail-risk/monte-carlo")
    async def monte_carlo_var(request: Request):
        """Calculate VaR using Monte Carlo simulation"""
        try:
            data = await request.json()
            result = tail_risk_engine.monte_carlo_var(
                symbol=data.get("symbol", "BTCUSDT"),
                returns=data.get("returns", []),
                n_simulations=data.get("n_simulations", 10000),
                confidence_level=data.get("confidence_level", 0.95)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/tail-risk/liquidation")
    async def calculate_liquidation_risk(request: Request):
        """Calculate liquidation risk"""
        try:
            data = await request.json()
            result = tail_risk_engine.calculate_liquidation_risk(
                symbol=data.get("symbol", "BTCUSDT"),
                current_price=data.get("current_price", 50000.0),
                liquidation_price=data.get("liquidation_price", 45000.0),
                volatility=data.get("volatility", 0.02),
                time_horizon=data.get("time_horizon", 1.0)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Signal Correlation Engine Endpoints (Section 23) ---
    
    @app.post("/v2/signal-correlation/analyze")
    async def analyze_signal_correlation(request: Request):
        """Analyze correlation between signals"""
        try:
            data = await request.json()
            from astra_bot.engines.signal_correlation_engine import SignalFeatures
            
            signal_features = [
                SignalFeatures(**sf) for sf in data.get("signal_features", [])
            ]
            result = signal_correlation_engine.analyze_signal_correlation(signal_features)
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Signal correlation analysis error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/signal-correlation/matrix")
    async def get_correlation_matrix(request: Request):
        """Get correlation matrix for signals"""
        try:
            data = await request.json()
            from astra_bot.engines.signal_correlation_engine import SignalFeatures
            
            signal_features = [
                SignalFeatures(**sf) for sf in data.get("signal_features", [])
            ]
            result = signal_correlation_engine.get_correlation_matrix(signal_features)
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/signal-correlation/independent")
    async def find_independent_signals(request: Request):
        """Find independent signals"""
        try:
            data = await request.json()
            from astra_bot.engines.signal_correlation_engine import SignalFeatures
            
            signal_features = [
                SignalFeatures(**sf) for sf in data.get("signal_features", [])
            ]
            result = signal_correlation_engine.find_independent_signals(
                signal_features,
                correlation_threshold=data.get("correlation_threshold", 0.8)
            )
            return JSONResponse(content=result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- MFE/MAE Engine Endpoints (Section 19-20) ---
    
    @app.post("/v2/mfe-mae/track")
    async def track_mfe_mae(request: Request):
        """Track MFE and MAE for a trade"""
        try:
            data = await request.json()
            result = mfe_mae_engine.track_mfe_mae(
                trade_id=data.get("trade_id", "trade_1"),
                entry_price=data.get("entry_price", 50000.0),
                current_price=data.get("current_price", 50500.0),
                stop_loss=data.get("stop_loss", 49000.0),
                take_profit=data.get("take_profit", 51000.0),
                direction=data.get("direction", "long")
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"MFE/MAE tracking error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/mfe-mae/history/{trade_id}")
    async def get_mfe_mae_history(trade_id: str):
        """Get MFE/MAE history for a trade"""
        try:
            history = mfe_mae_engine.get_mfe_mae_history(trade_id)
            return JSONResponse(content=[h.to_dict() for h in history])
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/mfe-mae/classify")
    async def classify_trade_outcome(request: Request):
        """Classify trade outcome based on MFE/MAE"""
        try:
            data = await request.json()
            result = mfe_mae_engine.classify_trade_outcome(
                trade_id=data.get("trade_id", "trade_1")
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Counterfactual Engine Endpoints (Section 21-22) ---
    
    @app.post("/v2/counterfactual/simulate")
    async def simulate_counterfactual(request: Request):
        """Simulate counterfactual scenarios"""
        try:
            data = await request.json()
            result = counterfactual_engine.simulate_counterfactual_scenarios(
                trade=data.get("trade", {}),
                scenarios=data.get("scenarios", [])
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Counterfactual simulation error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/counterfactual/delayed-entry")
    async def simulate_delayed_entry(request: Request):
        """Simulate delayed entry scenario"""
        try:
            data = await request.json()
            result = counterfactual_engine.simulate_delayed_entry(
                trade=data.get("trade", {}),
                delay_minutes=data.get("delay_minutes", 5)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/counterfactual/opportunity-cost")
    async def calculate_opportunity_cost(request: Request):
        """Calculate opportunity cost of not taking a trade"""
        try:
            data = await request.json()
            result = counterfactual_engine.calculate_opportunity_cost(
                signal=data.get("signal", {}),
                portfolio_value=data.get("portfolio_value", 10000.0)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Loss Attribution Engine Endpoints (Section 27) ---
    
    @app.post("/v2/loss-attribution/classify")
    async def classify_loss_cause(request: Request):
        """Classify cause of a losing trade"""
        try:
            data = await request.json()
            result = loss_attribution_engine.classify_loss_cause(
                trade=data.get("trade", {})
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Loss attribution error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/loss-attribution/analyze")
    async def analyze_loss_trend(request: Request):
        """Analyze loss trends"""
        try:
            data = await request.json()
            result = loss_attribution_engine.analyze_loss_trends(
                trades=data.get("trades", [])
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/loss-attribution/causes")
    async def get_loss_causes():
        """Get list of loss causes"""
        try:
            causes = loss_attribution_engine.get_loss_causes()
            return {"causes": causes}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Opportunity Cost Engine Endpoints (Section 22) ---
    
    @app.post("/v2/opportunity-cost/calculate")
    async def calculate_opportunity_cost_endpoint(request: Request):
        """Calculate opportunity cost for signals"""
        try:
            data = await request.json()
            from astra_bot.engines.opportunity_cost_engine import SignalOpportunity
            
            opportunities = [
                SignalOpportunity(**opp) for opp in data.get("opportunities", [])
            ]
            result = opportunity_cost_engine.calculate_opportunity_cost(
                opportunities=opportunities,
                capital=data.get("capital", 10000.0),
                max_positions=data.get("max_positions", 5)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Opportunity cost calculation error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/opportunity-cost/optimize")
    async def optimize_capital_allocation(request: Request):
        """Optimize capital allocation across opportunities"""
        try:
            data = await request.json()
            from astra_bot.engines.opportunity_cost_engine import SignalOpportunity
            
            opportunities = [
                SignalOpportunity(**opp) for opp in data.get("opportunities", [])
            ]
            result = opportunity_cost_engine.optimize_capital_allocation(
                opportunities=opportunities,
                total_capital=data.get("total_capital", 10000.0)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Regime Similarity Engine Endpoints (Section 9) ---
    
    @app.post("/v2/regime-similarity/assess")
    async def assess_regime_similarity(request: Request):
        """Assess regime similarity"""
        try:
            data = await request.json()
            from astra_bot.engines.regime_similarity_engine import MarketState
            
            state = MarketState(**data.get("state", {}))
            result = regime_similarity_engine.assess_regime_similarity(state)
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Regime similarity assessment error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/regime-similarity/compare")
    async def compare_regimes(request: Request):
        """Compare current state with historical regimes"""
        try:
            data = await request.json()
            from astra_bot.engines.regime_similarity_engine import MarketState
            
            state = MarketState(**data.get("state", {}))
            result = regime_similarity_engine.compare_with_historical_states(state)
            return JSONResponse(content=result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Market State Clusterer Endpoints (Section 29-30) ---
    
    @app.post("/v2/market-clusters/cluster")
    async def cluster_market_states(request: Request):
        """Cluster market states"""
        try:
            data = await request.json()
            result = market_state_clusterer.cluster_market_states(
                data.get("states", [])
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Market clustering error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/market-clusters/analyze")
    async def analyze_clusters(request: Request):
        """Analyze clustered market states"""
        try:
            data = await request.json()
            result = market_state_clusterer.analyze_clusters(
                data.get("states", []),
                n_clusters=data.get("n_clusters", 5)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/market-clusters/optimal/{n_states}")
    async def find_optimal_clusters(n_states: int):
        """Find optimal number of clusters"""
        try:
            import numpy as np
            states = list(np.random.rand(n_states, 10))  # Demo data
            optimal = market_state_clusterer.find_optimal_cluster_count(states)
            return {"n_states": n_states, "optimal_clusters": optimal}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Experiment Registry Endpoints (Section 43-44) ---
    
    @app.post("/v2/experiments/register")
    async def register_experiment(request: Request):
        """Register a new experiment"""
        try:
            data = await request.json()
            result = experiment_registry.register_experiment(
                experiment_name=data.get("experiment_name", "test_experiment"),
                config=data.get("config", {}),
                dataset_hash=data.get("dataset_hash", "hash_1"),
                description=data.get("description", "")
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Experiment registration error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/experiments/{experiment_id}")
    async def get_experiment(experiment_id: str):
        """Get experiment details"""
        try:
            exp = experiment_registry.get_experiment(experiment_id)
            if exp:
                return JSONResponse(content=exp.to_dict())
            raise HTTPException(status_code=404, detail="Experiment not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/experiments/search")
    async def search_experiments(query: str = ""):
        """Search experiments"""
        try:
            results = experiment_registry.search_experiments(query)
            return JSONResponse(content=[e.to_dict() for e in results])
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/experiments/stats")
    async def get_experiment_stats():
        """Get experiment statistics"""
        try:
            stats = experiment_registry.get_experiment_statistics()
            return JSONResponse(content=stats)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Statistical Tests Endpoints (Section 31-37) ---
    
    @app.post("/v2/statistical-tests/cpcv")
    async def run_cpcv_test(request: Request):
        """Run CPCV test"""
        try:
            data = await request.json()
            result = statistical_tests.run_cpcv_test(
                strategy_returns=data.get("strategy_returns", []),
                benchmark_returns=data.get("benchmark_returns", []),
                n_folds=data.get("n_folds", 10)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"CPCV test error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/statistical-tests/pbo")
    async def run_pbo_test(request: Request):
        """Run PBO test"""
        try:
            data = await request.json()
            result = statistical_tests.run_pbo_test(
                strategy_returns=data.get("strategy_returns", []),
                bootstrap_samples=data.get("bootstrap_samples", 1000)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/statistical-tests/dsr")
    async def run_dsr_test(request: Request):
        """Run DSR test"""
        try:
            data = await request.json()
            result = statistical_tests.run_dsr_test(
                strategy_returns=data.get("strategy_returns", []),
                benchmark_returns=data.get("benchmark_returns", []),
                window_size=data.get("window_size", 30)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/statistical-tests/whites-reality-check")
    async def run_whites_reality_check(request: Request):
        """Run White's Reality Check"""
        try:
            data = await request.json()
            result = statistical_tests.run_whites_reality_check(
                model_returns=data.get("model_returns", []),
                benchmark_returns=data.get("benchmark_returns", [])
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/statistical-tests/spa")
    async def run_spa_test(request: Request):
        """Run SPA test"""
        try:
            data = await request.json()
            result = statistical_tests.run_spa_test(
                strategy_returns=data.get("strategy_returns", []),
                benchmark_returns=data.get("benchmark_returns", []),
                n_models=data.get("n_models", 10)
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/statistical-tests/stability")
    async def run_stability_test(request: Request):
        """Run stability test"""
        try:
            data = await request.json()
            result = statistical_tests.run_stability_test(
                strategy_returns=data.get("strategy_returns", []),
                window_size=data.get("window_size", 30),
                metric=data.get("metric", "sharpe")
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Research Agent Endpoints (Section 49-51) ---
    
    @app.post("/v2/research/plan")
    async def create_research_plan(request: Request):
        """Create a research plan"""
        try:
            data = await request.json()
            result = research_agent.create_research_plan(
                objective=data.get("objective", ""),
                constraints=data.get("constraints", {}),
                available_data=data.get("available_data", [])
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Research plan creation error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/research/execute")
    async def execute_research_step(request: Request):
        """Execute a research step"""
        try:
            data = await request.json()
            result = research_agent.execute_research_step(
                step_id=data.get("step_id", "step_1")
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/research/status")
    async def get_research_status():
        """Get research status"""
        try:
            status = research_agent.get_research_status()
            return JSONResponse(content=status)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/research/learn")
    async def learn_from_results(request: Request):
        """Learn from research results"""
        try:
            data = await request.json()
            result = research_agent.learn_from_results(
                results=data.get("results", {})
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Hypothesis Generator Endpoints (Section 49-51) ---
    
    @app.post("/v2/hypothesis/generate")
    async def generate_hypotheses(request: Request):
        """Generate hypotheses from knowledge gaps"""
        try:
            data = await request.json()
            result = hypothesis_generator.generate_hypotheses(
                knowledge_gaps=data.get("knowledge_gaps", []),
                market_context=data.get("market_context", {})
            )
            return JSONResponse(content=[h.to_dict() for h in result])
        except Exception as e:
            logger.error(f"Hypothesis generation error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/hypothesis/prioritize")
    async def prioritize_hypotheses(request: Request):
        """Prioritize hypotheses"""
        try:
            data = await request.json()
            from astra_bot.engines.hypothesis_generator import Hypothesis
            
            hypotheses = [Hypothesis(**h) for h in data.get("hypotheses", [])]
            result = hypothesis_generator.prioritize_hypotheses(hypotheses)
            return JSONResponse(content=[h.to_dict() for h in result])
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Memory Manager Endpoints (Section 52) ---
    
    @app.post("/v2/memory/store")
    async def store_memory(request: Request):
        """Store a memory"""
        try:
            data = await request.json()
            result = memory_manager.store_memory(
                memory_type=data.get("memory_type", "OBSERVATION"),
                content=data.get("content", ""),
                metadata=data.get("metadata", {})
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Memory storage error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/memory/search")
    async def search_memory(query: str = "", memory_type: str = ""):
        """Search memories"""
        try:
            results = memory_manager.search_memories(query, memory_type)
            return JSONResponse(content=[m.to_dict() for m in results])
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/memory/stats")
    async def get_memory_stats():
        """Get memory statistics"""
        try:
            stats = memory_manager.get_memory_statistics()
            return JSONResponse(content=stats)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Lesson Quality Engine Endpoints (Section 53) ---
    
    @app.post("/v2/lessons/assess")
    async def assess_lesson_quality(request: Request):
        """Assess lesson quality"""
        try:
            data = await request.json()
            result = lesson_quality_engine.assess_lesson_quality(
                lesson=data.get("lesson", {})
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Lesson quality assessment error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/lessons/grading-system")
    async def get_grading_system():
        """Get lesson grading system"""
        try:
            system = lesson_quality_engine.get_grading_system()
            return JSONResponse(content=system)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Knowledge Base Endpoints (Section 54) ---
    
    @app.post("/v2/knowledge/store")
    async def store_knowledge(request: Request):
        """Store knowledge"""
        try:
            data = await request.json()
            result = knowledge_base.store_knowledge(
                knowledge_type=data.get("knowledge_type", "VALIDATED"),
                content=data.get("content", ""),
                source=data.get("source", ""),
                confidence=data.get("confidence", 0.8),
                evidence=data.get("evidence", "")
            )
            return JSONResponse(content=result.to_dict())
        except Exception as e:
            logger.error(f"Knowledge storage error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/knowledge/search")
    async def search_knowledge(query: str = "", knowledge_type: str = ""):
        """Search knowledge base"""
        try:
            results = knowledge_base.search_knowledge(query, knowledge_type)
            return JSONResponse(content=[k.to_dict() for k in results])
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v2/knowledge/stats")
    async def get_knowledge_stats():
        """Get knowledge base statistics"""
        try:
            stats = knowledge_base.get_knowledge_statistics()
            return JSONResponse(content=stats)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v2/knowledge/check")
    async def check_knowledge_repetition(request: Request):
        """Check if knowledge has been validated before"""
        try:
            data = await request.json()
            result = knowledge_base.check_knowledge_repetition(
                knowledge_hash=data.get("knowledge_hash", "")
            )
            return JSONResponse(content=result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# STANDALONE RUNNER
# ============================================================================

async def run_standalone():
    """Run bot in standalone mode (without FastAPI)"""
    logger.info("Running ASTRA AI Bot v2 in standalone mode...")
    
    # Demo: Process a sample symbol
    from astra_bot.decision.context import MarketContext
    
    # Create sample context
    ctx = MarketContext(
        symbol="BTCUSDT",
        current_price=50000.0,
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        candles_4h=[],
        orderbook=None,
        news_score=10,
        global_market={"btc_regime": "BULL"}
    )
    
    # Make decision
    decision = await pipeline.decide(ctx)
    logger.info(f"Decision for BTCUSDT: {decision.to_dict()}")
    
    # Test new engines
    logger.info("\n=== Testing New Engines ===")
    
    # Test Uncertainty Engine
    from astra_bot.engines.uncertainty_engine import ModelPrediction, MarketDataQuality, RegimeAssessment
    prediction = ModelPrediction(
        direction="long",
        probability=0.75,
        expected_return=0.02,
        model_name="test_model",
        model_version="1.0",
        features_used=["feature_1", "feature_2"],
        sample_size=100
    )
    data_quality = MarketDataQuality(
        spread_pct=0.001,
        depth=10000,
        volume=100000,
        volatility=0.02,
        data_gaps=0,
        latency_ms=10
    )
    regime_assessment = RegimeAssessment(
        current_regime="BULL",
        regime_confidence=0.8,
        regime_stability=0.7,
        transition_probability=0.1,
        historical_coverage=500
    )
    uncertainty_result = uncertainty_engine.assess_uncertainty(
        symbol="BTCUSDT",
        timeframe="1h",
        current_prediction=prediction,
        historical_predictions=[],
        data_quality=data_quality,
        regime_assessment=regime_assessment,
        sample_size=100
    )
    logger.info(f"Uncertainty Assessment: {uncertainty_result.to_dict()}")
    
    # Test Alpha Decay Engine
    alpha_decay_result = alpha_decay_engine.measure_signal_strength(
        signal_name="test_signal",
        symbol="BTCUSDT",
        timeframe="1h",
        intervals=[1, 5, 10, 20, 50, 100]
    )
    logger.info(f"Alpha Decay Measurement: {alpha_decay_result.to_dict()}")
    
    # Test Execution Optimizer
    from astra_bot.engines.execution_optimizer import OrderType, ExecutionUrgency, OrderBookState, LiquidityState
    order_book = OrderBookState(
        symbol="BTCUSDT",
        bids=[(49999.5, 1000), (49999.0, 2000)],
        asks=[(50000.5, 1000), (50001.0, 2000)],
        mid_price=50000.0,
        spread=1.0,
        spread_pct=0.00002,
        depth=6000,
        best_bid=49999.5,
        best_ask=50000.5
    )
    liquidity = LiquidityState(
        symbol="BTCUSDT",
        volume_24h=1000000,
        volume_current=100000,
        order_book_liquidity=50000,
        market_depth=100000,
        volatility=0.02
    )
    execution_result = execution_optimizer.select_optimal_strategy(
        signal={"symbol": "BTCUSDT", "direction": "long", "entry_price": 50000.0, "position_size": 0.1},
        order_book=order_book,
        liquidity=liquidity,
        urgency=ExecutionUrgency.NORMAL,
        expected_edge=0.01,
        position_size=0.1
    )
    logger.info(f"Execution Optimization: {execution_result.to_dict()}")
    
    logger.info("\n=== All Tests Completed ===")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="ASTRA AI Bot v2")
    parser.add_argument("--mode", type=str, default="standalone", choices=["standalone", "api", "both"], help="Run mode")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="API host")
    parser.add_argument("--port", type=int, default=8000, help="API port")
    args = parser.parse_args()
    
    if args.mode in ["api", "both"] and FASTAPI_AVAILABLE:
        import uvicorn
        
        if args.mode == "api":
            logger.info(f"Starting FastAPI server on {args.host}:{args.port}")
            uvicorn.run(app, host=args.host, port=args.port)
        else:  # both
            # Run FastAPI in a separate thread
            def run_api():
                uvicorn.run(app, host=args.host, port=args.port)
            
            api_thread = threading.Thread(target=run_api, daemon=True)
            api_thread.start()
            
            # Run standalone
            asyncio.run(run_standalone())
    else:
        asyncio.run(run_standalone())
