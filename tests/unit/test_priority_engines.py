"""
Tests for 4 priority trading engines:
- Microstructure Flow Engine
- Liquidity Map Engine
- Liquidation Cascade Engine
- Portfolio Opportunity Allocator
"""

from datetime import datetime, timezone

from astra_bot.core.market_analysis import (
    LiquidationCascadeEngine,
    LiquidationDirection,
    LiquidityMapEngine,
    MicrostructureFlowEngine,
    OrderBookSnapshot,
    get_liquidation_cascade_engine,
    get_liquidity_map_engine,
    get_microstructure_flow_engine,
)
from astra_bot.core.trading import (
    AllocationMethod,
    OpportunitySignal,
    PortfolioOpportunityAllocator,
    get_portfolio_allocator,
)


class TestMicrostructureFlowEngine:
    """Tests for Microstructure Flow Engine."""

    def test_initialization(self):
        engine = MicrostructureFlowEngine()
        assert engine is not None
        assert get_microstructure_flow_engine() is not None

    def test_process_snapshot_and_flow_analysis(self):
        engine = MicrostructureFlowEngine()
        now = datetime.now(timezone.utc)

        engine.add_order_book_snapshot(
            symbol="BTC-USDT",
            timestamp=now,
            bids=[(50000.0, 10.0), (49990.0, 5.0)],
            asks=[(50010.0, 2.0), (50020.0, 5.0)],
        )

        engine.add_order_print(
            symbol="BTC-USDT",
            timestamp=now,
            price=50005.0,
            volume=1.5,
            side="buy",
            aggressive=True,
        )

        analysis = engine.analyze_microstructure("BTC-USDT", timestamp=now)
        assert analysis is not None
        assert analysis.symbol == "BTC-USDT"
        assert analysis.flow_metrics.flow_imbalance >= 0


class TestLiquidityMapEngine:
    """Tests for Liquidity Map Engine."""

    def test_initialization(self):
        engine = LiquidityMapEngine()
        assert engine is not None
        assert get_liquidity_map_engine() is not None

    def test_liquidity_map_analysis(self):
        engine = LiquidityMapEngine()
        now = datetime.now(timezone.utc)

        snapshot = OrderBookSnapshot(
            symbol="BTC-USDT",
            timestamp=now,
            bids=[(50000.0, 20.0), (49900.0, 50.0)],
            asks=[(50100.0, 15.0), (50200.0, 40.0)],
        )
        analysis = engine.analyze_liquidity("BTC-USDT", snapshot, current_price=50005.0)
        assert analysis is not None
        assert analysis.symbol == "BTC-USDT"


class TestLiquidationCascadeEngine:
    """Tests for Liquidation Cascade Engine."""

    def test_initialization(self):
        engine = LiquidationCascadeEngine()
        assert engine is not None
        assert get_liquidation_cascade_engine() is not None

    def test_liquidation_event_processing(self):
        engine = LiquidationCascadeEngine()
        now = datetime.now(timezone.utc)

        # Process multiple liquidation events to trigger cascade detection logic
        for i in range(5):
            engine.add_liquidation_event(
                symbol="BTC-USDT",
                timestamp=now,
                price=50000.0 - i * 10,
                volume=10.0 + i,
                direction=LiquidationDirection.LONG_LIQUIDATION,
            )

        analysis = engine.analyze_cascades("BTC-USDT", timestamp=now)
        assert analysis is not None
        assert analysis.symbol == "BTC-USDT"


class TestPortfolioOpportunityAllocator:
    """Tests for Portfolio Opportunity Allocator."""

    def test_initialization(self):
        allocator = PortfolioOpportunityAllocator()
        assert allocator is not None
        assert get_portfolio_allocator() is not None

    def test_allocate_optimal(self):
        allocator = PortfolioOpportunityAllocator()

        signals = [
            OpportunitySignal(
                signal_id="sig1",
                symbol="BTC-USDT",
                direction="long",
                expected_return=0.05,
                risk=0.02,
                confidence=0.8,
                max_drawdown=0.03,
            ),
            OpportunitySignal(
                signal_id="sig2",
                symbol="ETH-USDT",
                direction="long",
                expected_return=0.04,
                risk=0.015,
                confidence=0.75,
                max_drawdown=0.025,
            ),
        ]

        result = allocator.allocate_optimal(
            portfolio_id="test_port",
            signals=signals,
            total_capital=100000.0,
            method=AllocationMethod.EQUAL,
        )
        assert result is not None
        assert len(result.selected_signals) == 2
        assert result.total_capital == 100000.0
