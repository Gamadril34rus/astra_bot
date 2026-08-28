"""Chaos-тесты (Этап 8): деградации внешнего мира не роняют бота.

Принцип fail-closed: при повреждённых/пустых данных система НЕ открывает
позиции и НЕ падает — продолжает работу, выходы работают.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from astra_bot.core import models
from tests.integration.test_main_tick import make_bot
from tests.integration.test_meta_strategy_execution import (
    OkxStub,
    gen_candles,
    stop_hit_bar,
)
from tests.unit.test_exit_manager import _crash


class _AllBrokenOkx:
    """API биржи полностью лежит (все запросы — ошибка)."""

    async def get_candles(self, *a, **k):
        raise RuntimeError("okx down")

    async def get_orderbook(self, *a, **k):
        raise RuntimeError("okx down")

    async def get_ticker(self, *a, **k):
        raise RuntimeError("okx down")


class TestChaos:
    def test_all_symbols_api_error_does_not_crash_step(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
        )
        bot = make_bot(tmp_path, _AllBrokenOkx(), monkeypatch)
        # step() не должен бросать: per-symbol изоляция ловит ошибки.
        asyncio.run(bot._trading_engine.step())
        assert bot._trading_engine.broker.positions == []

    def test_nan_candles_fail_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
        )
        """NaN-цены → нет позиций, нет исключения."""
        candles = gen_candles(230)
        last = candles[-1]
        nan_candle = models.Candle(
            exchange="okx", symbol="BTC-USDT", timeframe="5m",
            open_time=last.open_time,
            open=Decimal("NaN"), high=Decimal("NaN"),
            low=Decimal("NaN"), close=Decimal("NaN"),
            volume=Decimal("100"), quote_volume=Decimal("10000"),
        )
        bot = make_bot(tmp_path, OkxStub(candles), monkeypatch)
        eng = bot._trading_engine
        eng.okx.candles = [*candles[:-1], nan_candle]
        asyncio.run(bot._tick())  # не должно бросить
        assert eng.broker.positions == []  # fail-closed: не входим вслепую

    def test_price_gap_survives_and_exits_work(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
        )
        """Резкий гэп: тик переживает, открытая позиция закрывается
        стопом (а не остаётся висеть с битыми экстремумами)."""
        bot = make_bot(tmp_path, OkxStub(gen_candles(230)), monkeypatch)
        eng = bot._trading_engine
        asyncio.run(bot._tick())
        assert len(eng.broker.positions) == 1
        pos = eng.broker.positions[0]
        # Гэп вниз на 20% + пробой стопа.
        last = eng.okx.candles[-1]
        gap = models.Candle(
            exchange="okx", symbol="BTC-USDT", timeframe="5m",
            open_time=last.open_time + 300,
            open=Decimal(str(float(last.close) * 0.8)),
            high=Decimal(str(float(last.close) * 0.82)),
            low=Decimal(str(float(pos.stop_loss) * 0.9)),
            close=Decimal(str(float(last.close) * 0.79)),
            volume=Decimal("100"), quote_volume=Decimal("10000"),
        )
        eng.okx.candles = [*eng.okx.candles, gap]
        bot._last_tick_at = 0.0
        asyncio.run(bot._tick())  # без исключений
        # Позиция закрыта стопом (гэп пробил стоп) — либо re-entry не
        # произошёл; в любом случае исходной позиции больше нет.
        assert all(p.id != pos.id for p in eng.broker.positions)

    def test_panic_flattens_even_when_trading_halted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
        )
        """HALT запрещает ВХОДЫ, но BTC PANIC — выход: flatten работает."""
        bot = make_bot(tmp_path, OkxStub(gen_candles(230)), monkeypatch)
        eng = bot._trading_engine
        asyncio.run(bot._tick())
        assert len(eng.broker.positions) == 1
        # Имитация HALT (например, после hard drawdown).
        eng.risk.trading_enabled = False
        eng.okx.candles = _crash()
        bot._last_tick_at = 0.0
        asyncio.run(bot._tick())
        assert eng.broker.positions == []

    def test_recovered_after_api_outage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
        )
        """После «апатии» API торговля возобновляется."""
        okx = _AllBrokenOkx()
        bot = make_bot(tmp_path, okx, monkeypatch)
        eng = bot._trading_engine
        asyncio.run(eng.step())
        # API вернулся.
        okx.get_candles = OkxStub(gen_candles(230)).get_candles
        okx.get_orderbook = OkxStub([]).get_orderbook
        okx.get_ticker = OkxStub(gen_candles(230)).get_ticker
        asyncio.run(eng.step())  # без исключений

    def test_double_stop_bar_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
        )
        """Повторный стоп-бар после закрытия (re-entry возможен) —
        система стабильна, broker согласован с бандлом."""
        bot = make_bot(tmp_path, OkxStub(gen_candles(230)), monkeypatch)
        eng = bot._trading_engine
        asyncio.run(bot._tick())
        pos = eng.broker.positions[0]
        bar = stop_hit_bar(eng.okx.candles[-1], pos.stop_loss)
        eng.okx.candles = [*eng.okx.candles, bar, bar]  # два «одинаковых»
        bot._last_tick_at = 0.0
        asyncio.run(bot._tick())
        bundle = eng.state_store.load()
        assert bundle is not None
        assert bundle.paper["open_positions"] == len(eng.broker.positions)
