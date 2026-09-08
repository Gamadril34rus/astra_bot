"""Тесты перпетуал-механики PaperBroker: фандинг, mark price, ликвидации."""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.decision.broker import PaperBroker
from astra_bot.engines.cost_model import (
    BINGX_PERPS_MAKER_FEE,
    BINGX_PERPS_TAKER_FEE,
    bingx_perps_cost_model,
)


def _mk_broker(tmp_path, **kw) -> PaperBroker:
    defaults = dict(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        initial_capital=Decimal("10000"),
        max_leverage=Decimal("100"),
    )
    defaults.update(kw)
    return PaperBroker(**defaults)


def test_default_fees_are_perps_tariff(tmp_path):
    """Дефолт брокера — тариф перпов BingX: тейкер 0.05%, мейкер 0.02%."""
    b = _mk_broker(tmp_path)
    assert b.cost_model is not None
    assert b.cost_model.taker_fee_rate == BINGX_PERPS_TAKER_FEE == Decimal("0.0005")
    assert b.cost_model.maker_fee_rate == BINGX_PERPS_MAKER_FEE == Decimal("0.0002")


def test_perps_cost_model_preset():
    cm = bingx_perps_cost_model()
    assert cm.taker_fee_rate == Decimal("0.0005")
    assert cm.maker_fee_rate == Decimal("0.0002")
    assert cm.slippage_pct == Decimal("0.001")
    cm2 = bingx_perps_cost_model(slippage_pct=Decimal("0.0002"))
    assert cm2.slippage_pct == Decimal("0.0002")


class TestLiquidationOnMark:
    def test_long_liquidated_when_mark_crosses_liq(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("94"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=10, timeframe="1h",
        )
        # liq ≈ 100*(1-0.1+0.005) = 90.5; стоп 94 — выше, открылось ок.
        b.update_mark_price("BTC-USDT", Decimal("90.4"))
        closed = b.check_liquidations("BTC-USDT")
        assert len(closed) == 1
        assert closed[0].exit_reason == "liquidation"
        assert closed[0].exit_price == pytest.approx(90.5, abs=1e-9)
        # Ликвидация с 10x почти съедает маржу (~10): PnL глубоко отрицательный.
        assert closed[0].pnl < -5
        assert not b.positions
        assert pos.id == closed[0].id

    def test_short_liquidated_when_mark_crosses_liq(self, tmp_path):
        b = _mk_broker(tmp_path)
        b.open_position(
            symbol="ETH-USDT", direction="short",
            entry_price=Decimal("100"), stop_loss=Decimal("106"),
            take_profit=Decimal("94"), quantity=Decimal("1"),
            leverage=10, timeframe="1h",
        )
        # liq_short ≈ 100*(1+0.1-0.005) = 109.5
        b.update_mark_price("ETH-USDT", Decimal("109.6"))
        closed = b.check_liquidations("ETH-USDT")
        assert len(closed) == 1
        assert closed[0].exit_reason == "liquidation"

    def test_no_liquidation_without_mark(self, tmp_path):
        """Без mark price — fail-closed: позицию не трогаем."""
        b = _mk_broker(tmp_path)
        b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("94"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=10, timeframe="1h",
        )
        assert b.check_liquidations("BTC-USDT") == []
        assert len(b.positions) == 1

    def test_no_liquidation_at_leverage_1(self, tmp_path):
        """Плечо 1 ликвидировать нечего, даже при обвале mark."""
        b = _mk_broker(tmp_path)
        b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("50"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=1, timeframe="1h",
        )
        b.update_mark_price("BTC-USDT", Decimal("10"))
        assert b.check_liquidations("BTC-USDT") == []
        assert len(b.positions) == 1

    def test_other_symbols_not_touched(self, tmp_path):
        b = _mk_broker(tmp_path)
        b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("94"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=10, timeframe="1h",
        )
        b.open_position(
            symbol="ETH-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("94"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=10, timeframe="1h",
        )
        b.update_mark_price("BTC-USDT", Decimal("90.0"))
        b.update_mark_price("ETH-USDT", Decimal("99.0"))
        closed = b.check_liquidations("BTC-USDT")
        assert len(closed) == 1
        assert len(b.positions) == 1
        assert b.positions[0].symbol == "ETH-USDT"


class TestLiveFundingRate:
    def test_set_funding_rate_overrides_default(self, tmp_path):
        b = _mk_broker(tmp_path, funding_rate=Decimal("0.0001"))
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("97"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=2, timeframe="1h",
        )
        pos.bars_held = 8  # 1 интервал
        fill = pos.fill_price or pos.entry_price
        default_pay = b.funding_payment(pos, Decimal("1"), fill)
        # Живая ставка с биржи выше дефолта → платёж растёт.
        b.set_funding_rate("BTC-USDT", Decimal("0.0005"))
        live_pay = b.funding_payment(pos, Decimal("1"), fill)
        assert live_pay == pytest.approx(default_pay * 5)
        assert live_pay > 0

    def test_negative_rate_long_receives(self, tmp_path):
        """Отрицательная ставка: лонг получает, шорт платит."""
        b = _mk_broker(tmp_path)
        b.set_funding_rate("BTC-USDT", Decimal("-0.0002"))
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("97"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=2, timeframe="1h",
        )
        pos.bars_held = 8
        fill = pos.fill_price or pos.entry_price
        assert b.funding_payment(pos, Decimal("1"), fill) < 0

    def test_zero_bars_zero_funding(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("97"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=2, timeframe="1h",
        )
        fill = pos.fill_price or pos.entry_price
        assert b.funding_payment(pos, Decimal("1"), fill) == Decimal("0")


class TestEngineSyncHook:
    @staticmethod
    async def _engine_with_fake_exchange(tmp_path, monkeypatch):
        """Минимальный движок с фейковой биржей, отдающей mark/фандинг."""
        from unittest.mock import AsyncMock, MagicMock

        from astra_bot.decision.trading_engine import (
            TradingEngine,
            TradingEngineConfig,
        )

        exchange = MagicMock()
        exchange.get_mark_and_funding = AsyncMock(return_value=(Decimal("100"), Decimal("0.0003")))
        cfg = TradingEngineConfig(
            symbols=("BTC-USDT",),
            state_path=str(tmp_path / "pos.json"),
            trades_path=str(tmp_path / "trades.jsonl"),
            stats_path=str(tmp_path / "stats.json"),
            no_trade_observations_path=str(tmp_path / "obs.jsonl"),
            no_trade_outcomes_path=str(tmp_path / "outcomes.json"),
            hypotheses_path=str(tmp_path / "hyp.json"),
        )
        eng = TradingEngine(exchange=exchange, config=cfg)
        return eng

    async def test_sync_pushes_mark_and_funding_to_broker(self, tmp_path, monkeypatch):
        eng = await self._engine_with_fake_exchange(tmp_path, monkeypatch)
        await eng._sync_perps_state("BTC-USDT", Decimal("99"))
        assert eng.broker._mark_prices["BTC-USDT"] == Decimal("100")
        assert eng.broker._funding_rates["BTC-USDT"] == Decimal("0.0003")

    async def test_sync_falls_back_to_bar_close(self, tmp_path, monkeypatch):
        """Адаптер без mark-методов: mark = close бара, фандинг дефолтный."""
        from unittest.mock import MagicMock

        from astra_bot.decision.trading_engine import (
            TradingEngine,
            TradingEngineConfig,
        )

        exchange = MagicMock(spec=[])  # нет вообще никаких методов
        cfg = TradingEngineConfig(
            symbols=("BTC-USDT",),
            state_path=str(tmp_path / "pos.json"),
            trades_path=str(tmp_path / "trades.jsonl"),
            stats_path=str(tmp_path / "stats.json"),
            no_trade_observations_path=str(tmp_path / "obs.jsonl"),
            no_trade_outcomes_path=str(tmp_path / "outcomes.json"),
            hypotheses_path=str(tmp_path / "hyp.json"),
        )
        eng = TradingEngine(exchange=exchange, config=cfg)
        await eng._sync_perps_state("BTC-USDT", Decimal("101.5"))
        assert eng.broker._mark_prices["BTC-USDT"] == Decimal("101.5")
        assert "BTC-USDT" not in eng.broker._funding_rates
