"""Integration: Exit Controller в реальном production path (TZ §16/§17/§34).

- ACTIVE-гипотеза BREAKEVEN -> стоп в точку входа после 0.8R -> сделка
  закрывается в 0R вместо -1R;
- без ACTIVE-гипотезы поведение НЕ меняется (дефолт STATIC_TP: -1R);
- ACTIVE TIME_STOP -> вынужденное закрытие через N баров.

Реальные объекты: TradingEngine, DecisionPipeline, ScalpStrategy,
RiskEngine, PaperBroker, HypothesisStore, ExitController.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.decision.strategy_stats import StrategyStatsStore
from astra_bot.ml.exit_research import exit_hypothesis_id
from astra_bot.ml.hypothesis_engine import (
    HypothesisStatus,
    HypothesisStore,
    new_hypothesis,
)
from tests.integration.test_meta_strategy_execution import (
    FeedStub,
    gen_candles,
    make_engine,
    make_pipeline,
)

STEP = 900


def _bar_after(prev: models.Candle, o, h, lo, c) -> models.Candle:
    return models.Candle(
        exchange="feed",
        symbol="BTC-USDT",
        timeframe="5m",
        open_time=prev.open_time + STEP,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
    )


def make_active_exit_hypothesis(
    store: HypothesisStore, strategy: str, variant: str,
    params: dict, regime: str = "ANY",
) -> str:
    """Гипотеза выхода со всеми доказательствами, доведённая до ACTIVE."""
    hid = exit_hypothesis_id(variant, strategy, regime, params)
    hyp = new_hypothesis(
        id=hid,
        description=f"Exit {variant} {params} for {strategy} ({regime})",
        strategy_id=strategy,
        features={"exit_variant": variant, "exit_params": params},
        conditions={"exit_variant": variant, "regime": regime},
        sample_size=100,
        train_metrics={"expectancy": 0.3},
        validation_metrics={"expectancy": 0.25},
        oos_metrics={"expectancy": 0.2},
        walk_forward_metrics={"expectancy": 0.22},
        stress_metrics={"fees_x2": 0.15, "stable": True},
        expectancy=0.2,
        confidence=0.8,
        # TZ P0-2: lift vs baseline (positive for VALIDATED).
        baseline_expectancy=0.05,
        lift_vs_baseline=0.15,
    )
    store.add(hyp)
    for status in (HypothesisStatus.TESTING, HypothesisStatus.VALIDATED,
                   HypothesisStatus.ACTIVE):
        ok, why = store.transition(hid, status)
        assert ok, why
    return hid


class TestExitControllerLive:
    def _setup(self, tmp_path, monkeypatch):
        lessons: list[dict] = []
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons",
            lambda trades: lessons.extend(trades) or 1,
        )
        store = StrategyStatsStore(tmp_path / "stats.json")
        feed = FeedStub(gen_candles())
        eng = make_engine(tmp_path, feed, make_pipeline(tmp_path, store), lessons)
        return eng, lessons

    def test_breakeven_plan_protects_trade(self, tmp_path, monkeypatch):
        eng, lessons = self._setup(tmp_path, monkeypatch)
        make_active_exit_hypothesis(
            eng.hypotheses, "scalp", "BREAKEVEN", {"trigger_r": 0.8}
        )

        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert closed == []
        pos = eng.broker.positions[0]
        entry = float(pos.entry_price)
        risk = float(pos.risk_distance)
        assert risk > 0

        # Рост на 0.9R (ниже TP в 1R scalp) -> BREAKEVEN активируется,
        # стоп = entry.
        candles = eng.exchange.candles
        rally = _bar_after(
            candles[-1], float(candles[-1].close),
            entry + 0.9 * risk,
            float(candles[-1].close) + 0.01,  # low выше entry
            entry + 0.85 * risk,
        )
        candles = [*candles, rally]
        eng.exchange.candles = candles
        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert closed == []  # стоп в entry ещё не пробит
        assert float(eng.broker.positions[0].stop_loss) == pytest.approx(entry, abs=1e-9)

        # Откат ниже entry -> выход в 0R (не -1R).
        down = _bar_after(
            candles[-1], float(candles[-1].close),
            float(candles[-1].close) + 0.01,
            entry - 0.05,
            entry - 0.04,
        )
        eng.exchange.candles = [*candles, down]
        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert len(closed) == 1
        trade = closed[0]
        assert trade.exit_reason == "stop_loss"
        assert trade.r_multiple == pytest.approx(0.0, abs=0.02)
        assert eng.broker.positions == []
        # Сделка попала в уроки/статистику (реальный путь учёта).
        assert any(ls.get("id") == trade.id for ls in lessons)

    def test_default_behavior_unchanged_without_hypothesis(self, tmp_path, monkeypatch):
        eng, _ = self._setup(tmp_path, monkeypatch)
        assert eng.hypotheses.for_strategy("scalp") == []

        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        pos = eng.broker.positions[0]
        entry = float(pos.entry_price)
        risk = float(pos.risk_distance)

        candles = eng.exchange.candles
        rally = _bar_after(
            candles[-1], float(candles[-1].close),
            entry + 0.9 * risk,
            float(candles[-1].close) + 0.01,  # low выше entry
            entry + 0.85 * risk,
        )
        eng.exchange.candles = [*candles, rally]
        asyncio.run(eng.process_symbol("BTC-USDT"))
        # Дефолт: стоп НЕ двигается (нет ACTIVE-гипотезы).
        assert float(eng.broker.positions[0].stop_loss) == pytest.approx(
            entry - risk, abs=1e-9
        )

        down = _bar_after(
            eng.exchange.candles[-1], float(eng.exchange.candles[-1].close),
            float(eng.exchange.candles[-1].close) + 0.01,
            entry - 1.1 * risk,
            entry - 1.0 * risk,
        )
        eng.exchange.candles = [*eng.exchange.candles, down]
        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert len(closed) == 1
        assert closed[0].exit_reason == "stop_loss"
        assert closed[0].r_multiple == pytest.approx(-1.0, abs=0.02)

    def test_time_stop_forced_close(self, tmp_path, monkeypatch):
        eng, _ = self._setup(tmp_path, monkeypatch)
        make_active_exit_hypothesis(
            eng.hypotheses, "scalp", "TIME_STOP", {"bars": 3}
        )
        asyncio.run(eng.process_symbol("BTC-USDT"))
        assert len(eng.broker.positions) == 1

        # 3 плоских бара внутри стопа/тейка.
        closed = []
        for _ in range(3):
            last = eng.exchange.candles[-1]
            o = float(last.close)
            flat = _bar_after(last, o, o + 0.01, o - 0.01, o)
            eng.exchange.candles = [*eng.exchange.candles, flat]
            closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert len(closed) == 1
        assert closed[0].exit_reason == "time_stop"
        assert closed[0].r_multiple == pytest.approx(0.0, abs=0.02)
        assert eng.broker.positions == []
