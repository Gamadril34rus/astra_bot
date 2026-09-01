"""Integration test for P0-1: unified CostModel across all trading contours.

Проверяем:
1. Legacy PaperTradingEngine использует CostModel (запрет fees=0).
2. Инвариант: round-trip fees ≥ entry + exit commissions.
3. PaperBroker с cost_model работает идентично legacy fee_pct/slippage_pct.
4. Paper-PnL без издержек не публикуется (все trades имеют fees > 0).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.decision.broker import PaperBroker
from astra_bot.engines.cost_model import CostModel, cost_model_from_flat
from astra_bot.paperengine.paper_engine import PaperTradingEngine

# ---------- Legacy engine uses CostModel ----------


class TestLegacyEngineCostModel:
    def test_paper_engine_has_cost_model_by_default(self):
        """PaperTradingEngine по умолчанию создаётся с CostModel."""
        engine = PaperTradingEngine()
        assert engine._cost_model is not None
        assert isinstance(engine._cost_model, CostModel)
        assert engine._cost_model.taker_fee_rate > 0

    def test_paper_engine_rejects_zero_fee_cost_model(self):
        """Нельзя создать CostModel с нулевой taker_fee (TZ P0-1)."""
        with pytest.raises(ValueError, match="taker_fee_rate must be > 0"):
            CostModel(taker_fee_rate=Decimal("0"))

    def test_paper_engine_custom_cost_model(self):
        """PaperTradingEngine принимает кастомный CostModel."""
        cm = CostModel(taker_fee_rate=Decimal("0.002"), slippage_pct=Decimal("0.0005"))
        engine = PaperTradingEngine(cost_model=cm)
        assert engine._cost_model.taker_fee_rate == Decimal("0.002")
        assert engine._cost_model.slippage_pct == Decimal("0.0005")


# ---------- PaperBroker with CostModel ----------


class TestPaperBrokerCostModel:
    def test_broker_with_cost_model(self, tmp_path):
        """PaperBroker принимает CostModel напрямую."""
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            cost_model=cm,
        )
        assert broker.cost_model is not None
        assert broker.cost_model.taker_fee_rate == Decimal("0.001")

    def test_broker_backward_compat_fee_pct(self, tmp_path):
        """Старый интерфейс fee_pct/slippage_pct продолжает работать."""
        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            fee_pct=Decimal("0.002"),
            slippage_pct=Decimal("0.001"),
        )
        assert broker.cost_model is not None
        assert broker.cost_model.taker_fee_rate == Decimal("0.002")

    def test_broker_legacy_zero_fees_still_works(self, tmp_path):
        """Legacy test mode (zero fees) для проверки механики."""
        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        )
        # In legacy mode, cost_model is None (bypassed).
        assert broker.cost_model is None
        assert broker.fee_pct == Decimal("0")


# ---------- Round-trip fee invariant ----------


class TestRoundTripInvariant:
    def test_round_trip_fees_both_sides(self):
        """Round-trip fees включают ОБЕ стороны (вход + выход)."""
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))

        # Long: entry at 100, exit at 102
        rt_fees = cm.round_trip_fees(Decimal("100"), Decimal("102"), Decimal("1"), "long")

        # Entry: fill = 100.1, fee = 100.1 * 0.001 = 0.1001
        # Exit: fill = 102 * 0.999 = 101.898, fee = 101.898 * 0.001 = 0.101898
        # Total = 0.1001 + 0.101898 = 0.201998
        assert rt_fees > Decimal("0.2")  # > 2 * 100 * 0.001 = 0.2

    def test_invariant_with_cost_model_broker(self, tmp_path):
        """PaperBroker с CostModel: все закрытые сделки имеют fees > 0."""
        from astra_bot.core import models

        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            cost_model=cm,
        )

        broker.open_position(
            symbol="BTC-USDT",
            direction="long",
            entry_price=Decimal("100"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("102"),
            quantity=Decimal("1"),
        )

        bar = models.Candle(
            exchange="test",
            symbol="BTC-USDT",
            timeframe="5m",
            open_time=0,
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("98"),
            close=Decimal("99"),
            volume=Decimal("10"),
            quote_volume=Decimal("1"),
        )
        closed = broker.on_bar(bar)
        assert len(closed) == 1
        assert closed[0].fees > 0, "Trade must have fees > 0 (TZ P0-1)"

    def test_no_paper_pnl_without_costs(self, tmp_path):
        """P0-1 инвариант: paper-PnL без издержек не публикуется.
        Все trades в PaperBroker имеют fees > 0 при ненулевом cost_model."""
        import json

        from astra_bot.core import models

        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            cost_model=cm,
        )

        # Open + close (stop loss)
        broker.open_position(
            symbol="ETH-USDT",
            direction="short",
            entry_price=Decimal("2000"),
            stop_loss=Decimal("2010"),
            take_profit=Decimal("1980"),
            quantity=Decimal("0.1"),
        )
        bar = models.Candle(
            exchange="test",
            symbol="ETH-USDT",
            timeframe="5m",
            open_time=0,
            open=Decimal("2000"),
            high=Decimal("2011"),
            low=Decimal("1999"),
            close=Decimal("2010"),
            volume=Decimal("10"),
            quote_volume=Decimal("1"),
        )
        closed = broker.on_bar(bar)
        assert len(closed) == 1
        assert closed[0].fees > 0, "Every trade must have non-zero fees"

        # Check the trades file has fees recorded
        lines = (tmp_path / "trades.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        trade_data = json.loads(lines[0])
        assert trade_data["fees"] > 0


# ---------- cost_model_from_flat ----------


class TestCostModelFromFlat:
    def test_creates_valid_cost_model(self):
        cm = cost_model_from_flat(Decimal("0.001"), Decimal("0.001"))
        assert cm.taker_fee_rate == Decimal("0.001")
        assert cm.maker_fee_rate == Decimal("0.001")
        assert cm.slippage_pct == Decimal("0.001")
        assert cm.min_fee_rate == Decimal("0.001")
