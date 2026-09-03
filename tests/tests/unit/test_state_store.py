"""StateStore: атомарный checkpoint, версия схемы, восстановление (Этап 3)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest
from astra_bot.core.state_store import StateStore
from tests.integration.test_main_tick import make_bot
from tests.integration.test_meta_strategy_execution import (
    FeedStub,
    gen_candles,
    stop_hit_bar,
)


class TestSnapshotRoundtrip:
    def test_roundtrip_broker_and_risk(self, tmp_path):
        from astra_bot.decision.broker import PaperBroker

        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("1000"),
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        )
        from astra_bot.decision.broker import PaperPosition

        pos = PaperPosition(
            id="pos-1",
            symbol="BTC-USDT",
            direction="long",
            entry_price=Decimal("100"),
            quantity=Decimal("1"),
            stop_loss=Decimal("99"),
            take_profits=[Decimal("102")],
            tp_filled=[False],
            initial_quantity=Decimal("1"),
            risk_distance=Decimal("1"),
            regime="TREND",
            timeframe="1h",
        )
        broker.positions = [pos]
        broker.realized_pnl = Decimal("-5.5")

        class FakeRisk:
            class RS:
                value = "NORMAL"

            risk_state = RS()
            trading_enabled = True
            daily_pnl = Decimal("1.5")
            weekly_pnl = Decimal("-0.5")
            _high_water_mark = Decimal("1100")
            _current_equity = Decimal("1094.5")

            def get_open_positions(self):
                return {"pos-1": None}

        store = StateStore(tmp_path / "state_bundle.json")
        bundle = store.snapshot(broker=broker, risk=FakeRisk(),
                                readiness_info={"score": 42, "ready": False})
        store.save(bundle)

        loaded = store.load()
        assert loaded is not None
        assert loaded.schema_version == StateStore.SCHEMA_VERSION
        assert loaded.paper["open_positions"] == 1
        assert loaded.paper["broker_state"]["realized_pnl"] == "-5.5"
        assert loaded.paper["broker_state"]["positions"][0]["id"] == "pos-1"
        assert loaded.risk["risk_state"] == "NORMAL"
        assert loaded.risk["daily_pnl"] == "1.5"
        assert loaded.readiness["score"] == 42

    def test_atomic_replace_failure_keeps_old_file(self, tmp_path, monkeypatch):
        store = StateStore(tmp_path / "state_bundle.json")
        b1 = store.snapshot()
        store.save(b1)
        first = store.path.read_text()

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr("astra_bot.core.state_store.os.replace", boom)
        with pytest.raises(OSError):
            b1.saved_at = "changed"
            store.save(b1)
        # Старый бандл на месте — частичной записи не существует.
        assert store.path.read_text() == first

    def test_future_schema_version_rejected(self, tmp_path):
        """Неизвестная (высшая) версия схемы → fail-closed, None."""
        store = StateStore(tmp_path / "state_bundle.json")
        store.save(store.snapshot())
        data = json.loads(store.path.read_text())
        data["schema_version"] = StateStore.SCHEMA_VERSION + 1
        store.path.write_text(json.dumps(data))
        assert store.load() is None

    def test_corrupt_file_returns_none(self, tmp_path):
        store = StateStore(tmp_path / "state_bundle.json")
        store.path.write_text("{не json")
        assert store.load() is None

    def test_missing_file_returns_none(self, tmp_path):
        assert StateStore(tmp_path / "nope.json").load() is None


class TestRestoreBroker:
    def test_restores_when_component_file_missing(self, tmp_path):
        from astra_bot.decision.broker import PaperBroker, PaperPosition

        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("1000"),
        )
        pos = PaperPosition(
            id="pos-9",
            symbol="ETH-USDT",
            direction="short",
            entry_price=Decimal("2000"),
            quantity=Decimal("2"),
            stop_loss=Decimal("2020"),
            take_profits=[],
            tp_filled=[],
            initial_quantity=Decimal("2"),
            risk_distance=Decimal("20"),
        )
        broker.positions = [pos]
        broker.realized_pnl = Decimal("12.25")
        broker.save()
        store = StateStore(tmp_path / "state_bundle.json")
        store.save(store.snapshot(broker=broker))
        # Компонентный файл «потерян».
        broker.state_path.unlink()
        broker.positions = []
        broker.realized_pnl = Decimal("0")

        restored = store.restore_broker(broker, store.load())
        assert restored is True
        assert len(broker.positions) == 1
        assert broker.positions[0].id == "pos-9"
        assert broker.positions[0].direction == "short"
        assert broker.realized_pnl == Decimal("12.25")
        # И на «перезапуске брокера» позиции подхватываются.
        broker2 = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("1000"),
        )
        assert len(broker2.positions) == 1

    def test_does_not_override_existing_component_file(self, tmp_path):
        from astra_bot.decision.broker import PaperBroker

        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("1000"),
        )
        broker.save()  # пустой, но существующий файл
        store = StateStore(tmp_path / "state_bundle.json")
        store.save(store.snapshot(broker=broker))
        restored = store.restore_broker(broker, store.load())
        assert restored is False


class TestEngineIntegration:
    def test_bundle_written_on_open_and_close(self, tmp_path, monkeypatch):
        lessons: list[dict] = []
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons",
            lambda trades: lessons.extend(trades) or 1,
        )
        bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)
        eng = bot._trading_engine
        bundle_path = eng.state_store.path

        asyncio.run(bot._tick())
        assert bundle_path.exists()
        b = json.loads(bundle_path.read_text())
        assert b["paper"]["open_positions"] == 1
        assert b["risk"]["trading_enabled"] is True
        pos = eng.broker.positions[0]

        # Стоп пробит → сделка закрыта → бандл обновлён.
        eng.exchange.candles = [*eng.exchange.candles, stop_hit_bar(eng.exchange.candles[-1], pos.stop_loss)]
        bot._last_tick_at = 0.0  # снять троттлинг для следующего тика
        asyncio.run(bot._tick())
        b = json.loads(bundle_path.read_text())
        assert b["risk"]["risk_state"] in {"NORMAL", "REDUCED", "DEFENSIVE", "STOP"}
        # Оригинальная позиция закрыта и попала в уроки (lesson по её id).
        assert any(les.get("id") == pos.id for les in lessons)
        assert all(p.id != pos.id for p in eng.broker.positions)
        # Бандл согласован с broker: столько позиций, сколько в брокере.
        assert b["paper"]["open_positions"] == len(eng.broker.positions)

    def test_restart_restores_from_bundle_when_positions_lost(
        self, tmp_path, monkeypatch
    ):
        bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)
        eng = bot._trading_engine
        asyncio.run(bot._tick())
        assert len(eng.broker.positions) == 1
        pos_id = eng.broker.positions[0].id

        # «Перезапуск CI-сессии»: broker-файл утерян, бандл на месте.
        eng.broker.state_path.unlink()
        eng2 = type(eng)(
            exchange=eng.exchange,
            pipeline=eng.pipeline,
            config=eng.config,
            broker=None,
        )
        assert len(eng2.broker.positions) == 1
        assert eng2.broker.positions[0].id == pos_id

    def test_bundle_path_inside_state_dir(self, tmp_path, monkeypatch):
        bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)
        assert bot._trading_engine.state_store.path.name == "state_bundle.json"
        assert "state" in str(bot._trading_engine.state_store.path)
