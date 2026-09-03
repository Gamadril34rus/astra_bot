"""Meta-Strategy: выбор по EV в режиме, sample size, NO_TRADE (TZ §5/§6)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.decision.config import DecisionConfig
from astra_bot.decision.context import SignalCandidate
from astra_bot.decision.meta_strategy import (
    MetaStrategy,
    NoTradeReason,
    candidate_prior_r,
)
from astra_bot.decision.strategy_stats import StrategyStatsStore


def _candidate(strategy="scalp", direction="long", confidence=0.7, score=50.0):
    return SignalCandidate(
        symbol="BTC-USDT",
        direction=direction,
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("103"),
        timeframe="1h",
        strategy=strategy,
        confidence=confidence,
    )


@pytest.fixture()
def store(tmp_path):
    return StrategyStatsStore(tmp_path / "stats.json", shrinkage_k=30, min_samples=30)


@pytest.fixture()
def config():
    cfg = DecisionConfig()
    cfg.min_ev_r = 0.05
    cfg.min_ev_confidence = 0.3
    return cfg


def _seed(store, strategy, regime, tf, values):
    for r in values:
        store.record(strategy=strategy, regime=regime, timeframe=tf, r_multiple=r)


class TestSelectionByEV:
    def test_selects_higher_ev_not_higher_score(self, store, config):
        meta = MetaStrategy(store, config)
        # Оба кандидата со score 50; EV различается историей в режиме.
        _seed(store, "trend_a", "RANGE", "1h", [1.0] * 50)   # expectancy +1R
        _seed(store, "trend_b", "RANGE", "1h", [0.1] * 50)   # expectancy +0.1R
        a = _candidate("trend_a")
        b = _candidate("trend_b")
        decision = meta.select([a, b], regime="RANGE")
        assert decision.chosen is a
        assert decision.reason_code is None
        evs = {e.strategy: e.ev_r for e in decision.evaluations}
        assert evs["trend_a"] > evs["trend_b"]

    def test_regime_dependence(self, store, config):
        meta = MetaStrategy(store, config)
        # Стратегия прибыльна в тренде, убыточна в диапазоне.
        _seed(store, "trend_a", "WEAK_BULL_TREND", "1h", [1.0] * 50)
        _seed(store, "trend_a", "RANGE", "1h", [-1.0] * 50)
        bull = meta.select([_candidate("trend_a")], regime="WEAK_BULL_TREND")
        range_dec = meta.select([_candidate("trend_a")], regime="RANGE")
        assert bull.chosen is not None
        assert range_dec.chosen is None
        assert range_dec.reason_code == NoTradeReason.LOW_EV

    def test_negative_ev_blocks(self, store, config):
        meta = MetaStrategy(store, config)
        _seed(store, "loser", "RANGE", "1h", [-0.9] * 40)
        # Консервативный prior (p=0.4): 40 убытков доминируют → EV < 0.
        decision = meta.select([_candidate("loser", confidence=0.4)], regime="RANGE")
        assert decision.chosen is None
        assert decision.reason_code == NoTradeReason.LOW_EV
        assert decision.evaluations[0].ev_r < 0

    def test_no_data_uses_prior(self, store, config):
        meta = MetaStrategy(store, config)
        # Без истории: prior из P(win) и RR (RR=3, p=0.7 → >0).
        cand = _candidate("new_strategy", confidence=0.7)
        decision = meta.select([cand], regime="RANGE")
        assert decision.chosen is cand
        ev = decision.evaluations[0]
        assert ev.sample_size == 0
        assert ev.ev_r == pytest.approx(ev.prior_r)
        assert ev.confidence == 0.0

    def test_small_sample_cannot_override_prior(self, store, config):
        """«3 сделки, 3 выигрыша» не является доказательством (TZ §11).

        Консервативный prior (p=0.4, RR=3 -> EV=0.4R): три выигрыша по +3R
        сжимаются к prior с весом w=3/33=0.09 - оценка движется к 0.64R,
        а не к эмпирическим 3R. Жёсткий порог (0.7R) её отклоняет.
        """
        for _ in range(3):
            store.record(
                strategy="lucky", regime="RANGE", timeframe="1h", r_multiple=3.0
            )
        strict = MetaStrategy(store, config)
        strict.config.min_ev_r = 0.7
        cand = _candidate("lucky", confidence=0.4)
        decision = strict.select([cand], regime="RANGE")
        ev = decision.evaluations[0]
        assert ev.sample_size == 3
        assert ev.confidence == pytest.approx(3 / 33)
        # Оценка близка к prior (0.4), а не к sample mean (3.0).
        assert ev.ev_r < 1.0
        assert ev.ev_r < strict.config.min_ev_r
        assert decision.chosen is None
        assert decision.reason_code == NoTradeReason.LOW_EV

    def test_confidence_gate_at_min_samples(self, store, config):
        store2 = StrategyStatsStore(
            store.path.parent / "s2.json", shrinkage_k=30, min_samples=30
        )
        for _ in range(30):
            store2.record(
                strategy="edge", regime="RANGE", timeframe="1h", r_multiple=1.0
            )
        # confidence = 30/60 = 0.5 < 0.6 → LOW_CONFIDENCE.
        cfg_strict = DecisionConfig()
        cfg_strict.min_ev_r = 0.0
        cfg_strict.min_ev_confidence = 0.6
        meta = MetaStrategy(store2, cfg_strict)
        decision = meta.select([_candidate("edge")], regime="RANGE")
        assert decision.chosen is None
        assert decision.reason_code == NoTradeReason.LOW_CONFIDENCE
        # При 60 выборке confidence 0.667 ≥ 0.6 → проходит.
        for _ in range(30):
            store2.record(
                strategy="edge", regime="RANGE", timeframe="1h", r_multiple=1.0
            )
        decision2 = meta.select([_candidate("edge")], regime="RANGE")
        assert decision2.chosen is not None


class TestHardGatesMapped:
    def test_rejected_candidate_reason_mapped(self, store, config):
        meta = MetaStrategy(store, config)
        cand = _candidate("scalp")
        cand.reject("liquidity_too_thin")
        decision = meta.select([cand], regime="RANGE")
        assert decision.chosen is None
        assert decision.reason_code == NoTradeReason.LOW_LIQUIDITY
        assert decision.evaluations[0].rejection == NoTradeReason.LOW_LIQUIDITY

    def test_dominant_reason_low_ev_first(self, store, config):
        meta = MetaStrategy(store, config)
        a = _candidate("a")
        a.reject("rr_too_low")  # → LOW_EV
        b = _candidate("b")
        b.reject("liquidity_too_thin")
        decision = meta.select([a, b], regime="RANGE")
        assert decision.chosen is None
        assert decision.reason_code == NoTradeReason.LOW_EV


class TestCandidatePrior:
    def test_prior_with_tp(self):
        cand = _candidate(confidence=0.7)  # RR = 3 (103/99/100)
        # p=0.7, win=3R, loss=1R, costs=(0.1%+0.1%)/1% = 0.2R
        # EV = 0.7*3 - 0.3*1 - 0.2 = 1.6
        assert candidate_prior_r(cand) == pytest.approx(1.6)

    def test_prior_no_tp_uses_one_r(self):
        cand = _candidate(confidence=0.7)
        cand.take_profit = Decimal("0")  # flip-стратегия без тейка
        # EV = 0.7*1 - 0.3*1 - 0.2 = 0.2
        assert candidate_prior_r(cand) == pytest.approx(0.2)

    def test_prior_zero_risk(self):
        cand = _candidate(confidence=0.7)
        cand.stop_loss = cand.entry_price
        assert candidate_prior_r(cand) == 0.0
