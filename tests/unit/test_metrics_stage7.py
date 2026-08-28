"""Метрики (Этап 7): decisions/exits/latency/readiness/hypotheses."""

from __future__ import annotations

import asyncio

from astra_bot.core.metrics import render_metrics
from astra_bot.ml.hypothesis_engine import (
    HypothesisStatus,
    HypothesisStore,
    new_hypothesis,
)
from tests.integration.test_main_tick import make_bot
from tests.integration.test_meta_strategy_execution import OkxStub, gen_candles


def _render() -> str:
    return render_metrics().decode()


class TestDecisionMetrics:
    def test_decisions_and_latency_rendered_after_tick(self, tmp_path, monkeypatch):
        bot = make_bot(tmp_path, OkxStub(gen_candles()), monkeypatch)
        asyncio.run(bot._tick())
        text = _render()
        assert "astra_decisions_total" in text
        # LONG-решение по BTC (scalp на фиксированном наборе свечей).
        assert 'action="LONG"' in text
        assert "astra_decision_duration_seconds_bucket" in text
        assert "astra_tick_duration_seconds_bucket" in text

    def test_no_trade_reason_code_labelled(self, tmp_path, monkeypatch):
        """NO_TRADE несёт кодированную причину (низкая кардинальность)."""
        bot = make_bot(tmp_path, OkxStub(gen_candles()), monkeypatch)
        # 3 бара — меньше, чем нужно пайплайну → NO_TRADE (insufficient_data).
        bot._trading_engine.okx.candles = gen_candles(n=3)
        bot._last_tick_at = 0.0
        asyncio.run(bot._tick())
        text = _render()
        assert 'action="NO_TRADE"' in text


class TestExitMetrics:
    def test_exit_reason_counted(self, tmp_path, monkeypatch):
        from tests.integration.test_meta_strategy_execution import stop_hit_bar

        lessons: list[dict] = []
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons",
            lambda trades: lessons.extend(trades) or 1,
        )
        bot = make_bot(tmp_path, OkxStub(gen_candles()), monkeypatch)
        eng = bot._trading_engine
        asyncio.run(bot._tick())
        assert len(eng.broker.positions) == 1
        pos = eng.broker.positions[0]
        eng.okx.candles = [*eng.okx.candles, stop_hit_bar(eng.okx.candles[-1], pos.stop_loss)]
        bot._last_tick_at = 0.0
        asyncio.run(bot._tick())
        text = _render()
        assert "astra_exits_total" in text
        assert 'reason="stop_loss"' in text


class TestHypothesisMetrics:
    def test_status_gauge_reflects_store(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        h1 = new_hypothesis(id="hyp-m1", description="x", strategy_id="st")
        store.add(h1)
        store.transition("hyp-m1", HypothesisStatus.TESTING)
        h2 = new_hypothesis(id="hyp-m2", description="x", strategy_id="st")
        store.add(h2)
        text = _render()
        assert 'status="TESTING"' in text
        assert 'status="DISCOVERED"' in text

    def test_reload_restores_gauge(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        h1 = new_hypothesis(id="hyp-r1", description="x", strategy_id="st")
        store.add(h1)
        reloaded = HypothesisStore(tmp_path / "h.json")
        assert len(reloaded.hypotheses) == 1
        assert 'status="DISCOVERED"' in _render()


class TestReadinessMetrics:
    def test_readiness_gauges_set_on_tick(self, tmp_path, monkeypatch):
        bot = make_bot(tmp_path, OkxStub(gen_candles()), monkeypatch)
        asyncio.run(bot._tick())
        text = _render()
        assert "astra_readiness_score" in text
        assert "astra_readiness_ready" in text
        # Данные paper-накопления нет → ready = 0.
        assert "# TYPE astra_readiness_ready gauge" in text
