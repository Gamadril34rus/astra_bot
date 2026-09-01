"""
ASTRA BOT Core Module

Содержит базовые компоненты системы:
- data_quality: Контроль качества данных
- market_analysis: Анализ рынка
- research: Исследовательские компоненты
- trading: Торговые компоненты
- memory: Система памяти
"""

# Re-export ключевых компонентов
from .data_quality import get_data_quality_engine, DataQualityEngine
from .market_analysis import (
    get_market_microstructure_engine,
    get_market_regime_engine,
    get_volatility_engine,
    get_market_structure_engine,
    get_microstructure_flow_engine,
    get_liquidity_map_engine,
    get_liquidation_cascade_engine,
    MarketMicrostructureEngine,
    MarketRegimeEngine,
    VolatilityEngine,
    MarketStructureEngine,
    MicrostructureFlowEngine,
    LiquidityMapEngine,
    LiquidationCascadeEngine,
)
from .research import (
    get_event_response_engine,
    get_causality_research_engine,
    get_academic_research_engine,
    EventResponseEngine,
    CausalityResearchEngine,
    AcademicResearchEngine,
)
from .trading import (
    get_meta_strategy_engine,
    get_transaction_cost_engine,
    get_execution_simulator,
    get_position_sizing_engine,
    get_champion_challenger_framework,
    get_validation_gates,
    get_portfolio_allocator,
    MetaStrategyEngine,
    TransactionCostEngine,
    ExecutionSimulator,
    PositionSizingEngine,
    ChampionChallengerFramework,
    ValidationGates,
    PortfolioOpportunityAllocator,
)
from .memory import get_memory_system, MemorySystem

__all__ = [
    # Data Quality
    "DataQualityEngine",
    "get_data_quality_engine",
    
    # Market Analysis
    "MarketMicrostructureEngine",
    "MarketRegimeEngine", 
    "VolatilityEngine",
    "MarketStructureEngine",
    "MicrostructureFlowEngine",
    "LiquidityMapEngine",
    "LiquidationCascadeEngine",
    "get_market_microstructure_engine",
    "get_market_regime_engine",
    "get_volatility_engine",
    "get_market_structure_engine",
    "get_microstructure_flow_engine",
    "get_liquidity_map_engine",
    "get_liquidation_cascade_engine",
    
    # Research
    "EventResponseEngine",
    "CausalityResearchEngine",
    "AcademicResearchEngine",
    "get_event_response_engine",
    "get_causality_research_engine",
    "get_academic_research_engine",
    
    # Trading
    "MetaStrategyEngine",
    "TransactionCostEngine",
    "ExecutionSimulator",
    "PositionSizingEngine",
    "ChampionChallengerFramework",
    "ValidationGates",
    "PortfolioOpportunityAllocator",
    "get_meta_strategy_engine",
    "get_transaction_cost_engine",
    "get_execution_simulator",
    "get_position_sizing_engine",
    "get_champion_challenger_framework",
    "get_validation_gates",
    "get_portfolio_allocator",
    
    # Memory
    "MemorySystem",
    "get_memory_system",
]
