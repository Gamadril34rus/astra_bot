"""Integration: main.py _tick — оркестратор paper-пути (Этап 1).

Проверяем реальный поток: AstraBot._tick → TradingEngine.step →
pipeline → risk → PaperBroker. Без сети: биржевой стуб, как в
test_meta_strategy_execution.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from astra_bot.core.config import load_settings
from astra_bot.core.market_safety import SafetyVerdict
from astra_bot.main import AstraBot
from tests.integration.test_meta_strategy_execution import (
    FeedStub,
    gen_candles,
)


def _stub_safety(eng) -> None:
    """Внешние проверки (новости и т.п.) — не в предмете теста."""
    eng.safety.check = lambda *a, **k: SafetyVerdict(allowed=True)


STEP = 900


def make_bot(tmp_path, feed, monkeypatch) -> AstraBot:
    """AstraBot с modern paper-путём, state изолирован в tmp."""
    monkeypatch.setenv("ASTRA_STATE_DIR", str(tmp_path / "state"))
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "system:\n"
        "  environment: paper\n"
        "  paper_trading: true\n"
        "  trading_enabled: false\n"
        "trading:\n"
        "  instruments:\n"
        "    - BTC/USDT\n",
        encoding="utf-8",
    )
    load_settings(str(cfg))
    bot = AstraBot(config_path=str(cfg))
    bot._exchange_client = feed
    bot._init_trading_engine()
    _stub_safety(bot._trading_engine)
    return bot


class TestTickOrchestration:
    def test_tick_trades_on_real_path(self, tmp_path, monkeypatch):
        lessons: list[dict] = []
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons",
            lambda trades: lessons.extend(trades) or 1,
        )
        bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)
        assert bot._trading_engine is not None
        eng = bot._trading_engine

        # Тик 1: полный поток → позиция открыта через risk-контур.
        asyncio.run(bot._tick())
        assert len(eng.broker.positions) == 1
        pos = eng.broker.positions[0]
        assert pos.strategy == "scalp"
        # Риск-слой учёл позицию (независимый контур).
        assert len(eng.risk._open_positions) == 1

        # Тик 2 сразу после: троттлинг (tick_interval_seconds).
        asyncio.run(bot._tick())
        # Позиция та же — повторного входа на том же баре нет.
        assert len(eng.broker.positions) == 1

    def test_tick_without_engine_is_fail_closed(self, tmp_path, monkeypatch):
        cfg = tmp_path / "settings.yaml"
        cfg.write_text("trading:\n  instruments:\n    - BTC/USDT\n", encoding="utf-8")
        load_settings(str(cfg))
        bot = AstraBot(config_path=str(cfg))
        bot._exchange_client = None
        bot._init_trading_engine()
        assert bot._trading_engine is None
        # Не падает и ничего не делает.
        asyncio.run(bot._tick())

    def test_symbol_error_does_not_stop_others(self, tmp_path, monkeypatch):
        """Один символ упал → остальные обрабатываются (per-symbol)."""
        from tests.integration.test_meta_strategy_execution import gen_candles as g

        class PartialOkx:
            """Второй символ падает с ошибкой API, первый работает."""

            def __init__(self, candles):
                self.candles = candles

            async def get_candles(self, symbol, **kwargs):
                if symbol == "BROKEN-USDT":
                    raise RuntimeError("API error")
                return self.candles

            async def get_orderbook(self, symbol, depth=20):
                return None

            async def get_ticker(self, symbol):
                last = float(self.candles[-1].close)
                return {"last": str(last), "high_24h": str(last + 1),
                        "low_24h": str(last - 1)}

        cfg = tmp_path / "settings.yaml"
        cfg.write_text(
            "trading:\n  instruments:\n    - BTC/USDT\n    - BROKEN/USDT\n",
            encoding="utf-8",
        )
        load_settings(str(cfg))
        monkeypatch.setenv("ASTRA_STATE_DIR", str(tmp_path / "state"))
        bot = AstraBot(config_path=str(cfg))
        bot._exchange_client = PartialOkx(g())
        bot._init_trading_engine()
        _stub_safety(bot._trading_engine)
        assert bot._trading_engine.config.symbols == ("BTC-USDT", "BROKEN-USDT")

        # Тик не падает, BTC обработан (позиция открыта).
        asyncio.run(bot._tick())
        assert len(bot._trading_engine.broker.positions) == 1
        assert bot._trading_engine.broker.positions[0].symbol == "BTC-USDT"

    def test_total_tick_error_propagates_to_run_loop(self, tmp_path, monkeypatch):
        """Ошибку всего тика _run ловит и не роняет бота."""
        bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)

        async def boom():
            raise RuntimeError("total failure")

        bot._trading_engine.step = boom
        with pytest.raises(RuntimeError):
            asyncio.run(bot._tick())

    def test_start_disables_legacy_paper_loop_when_modern_active(
        self, tmp_path, monkeypatch
    ):
        from astra_bot.paperengine.paper_engine import PaperTradingEngine

        bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)
        bot._paper_engine = PaperTradingEngine(initial_capital=Decimal("1000"))
        bot._exchange_websocket = None

        async def _noop_close():
            return None

        bot._exchange_client.close = _noop_close

        async def scenario():
            task = asyncio.create_task(bot.start())
            await asyncio.sleep(0.2)
            assert bot._running is True
            # Legacy-цикл НЕ запущен: современный путь активен.
            assert not bot._paper_engine.is_running
            # _tick отработал (позиция по scalp-фикстуре).
            assert len(bot._trading_engine.broker.positions) == 1
            bot._running = False
            await asyncio.wait_for(task, timeout=10)

        asyncio.run(scenario())
