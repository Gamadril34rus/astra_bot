"""Hypothesis Engine: lifecycle, переходы, invalidation, persistence (TZ §9/§10/§11)."""

from __future__ import annotations

import pytest
from astra_bot.ml.hypothesis_engine import (
    ALLOWED_TRANSITIONS,
    Hypothesis,
    HypothesisStatus,
    HypothesisStore,
    new_hypothesis,
)


def _validated_hyp(id="hyp-test", **kw) -> Hypothesis:
    """Гипотеза со всеми доказательствами, требуемыми для VALIDATED."""
    hyp = new_hypothesis(
        id=id,
        description="test hypothesis",
        strategy_id="ts_momentum",
        sample_size=100,
        train_metrics={"expectancy": 0.2, "profit_factor": 1.4},
        validation_metrics={"expectancy": 0.15, "profit_factor": 1.3},
        oos_metrics={"expectancy": 0.12, "profit_factor": 1.25},
        walk_forward_metrics={"expectancy": 0.1, "profit_factor": 1.2},
        stress_metrics={"fees_x2": 0.08, "slippage_x3": 0.05, "stable": True},
        # expectancy 0.25 при n=100 → p≈0.006: проходит FDR-гейт (Этап 6)
        # — «все доказательства» теперь включают и значимость сигнала.
        expectancy=0.25,
        profit_factor=1.35,
        win_rate=0.58,
    )
    for k, v in kw.items():
        setattr(hyp, k, v)
    return hyp


class TestLifecycle:
    def test_happy_path(self):
        hyp = _validated_hyp()
        ok, why = hyp.transition(HypothesisStatus.TESTING)
        assert ok, why
        ok, why = hyp.transition(HypothesisStatus.VALIDATED)
        assert ok, why
        ok, why = hyp.transition(HypothesisStatus.ACTIVE)
        assert ok, why
        assert hyp.status is HypothesisStatus.ACTIVE
        # История статусов сохранена (TZ §10).
        statuses = [e["status"] for e in hyp.status_log]
        assert statuses == ["DISCOVERED", "TESTING", "VALIDATED", "ACTIVE"]

    def test_invalid_transition_rejected(self):
        hyp = _validated_hyp()
        ok, why = hyp.transition(HypothesisStatus.ACTIVE)  # DISCOVERED -> ACTIVE
        assert not ok
        assert "запрещён" in why
        assert hyp.status is HypothesisStatus.DISCOVERED

    def test_retired_is_terminal(self):
        hyp = _validated_hyp()
        hyp.transition(HypothesisStatus.TESTING)
        hyp.transition(HypothesisStatus.VALIDATED)
        hyp.transition(HypothesisStatus.INVALIDATED, reason="OOS деградация")
        hyp.transition(HypothesisStatus.RETIRED)
        ok, _ = hyp.transition(HypothesisStatus.DISCOVERED)
        assert not ok
        assert ALLOWED_TRANSITIONS[HypothesisStatus.RETIRED] == set()

    def test_invalidated_requires_reason(self):
        hyp = _validated_hyp()
        hyp.transition(HypothesisStatus.TESTING)
        ok, why = hyp.transition(HypothesisStatus.INVALIDATED)
        assert not ok
        assert "reason" in why.lower()

    def test_invalidation_reason_stored(self):
        hyp = _validated_hyp()
        hyp.transition(HypothesisStatus.TESTING)
        hyp.transition(HypothesisStatus.INVALIDATED, reason="OOS expectancy < 0")
        assert hyp.invalidation_reason == "OOS expectancy < 0"

    def test_weakening_and_recovery(self):
        hyp = _validated_hyp()
        hyp.transition(HypothesisStatus.TESTING)
        hyp.transition(HypothesisStatus.VALIDATED)
        hyp.transition(HypothesisStatus.ACTIVE)
        ok, why = hyp.transition(HypothesisStatus.WEAKENING, reason="live degradation")
        assert ok, why
        ok, why = hyp.transition(HypothesisStatus.ACTIVE, reason="восстановление")
        assert ok, why


class TestValidationRequirements:
    """TZ §11: VALIDATED нельзя получить «на нескольких прибыльных сделках»."""

    def test_small_sample_rejected(self):
        hyp = _validated_hyp(sample_size=5)
        hyp.transition(HypothesisStatus.TESTING)
        ok, why = hyp.transition(HypothesisStatus.VALIDATED)
        assert not ok
        assert "sample_size" in why

    @pytest.mark.parametrize("period", ["train", "validation", "oos", "walk_forward"])
    def test_missing_period_rejected(self, period):
        hyp = _validated_hyp()
        setattr(hyp, f"{period}_metrics", {})
        hyp.transition(HypothesisStatus.TESTING)
        ok, why = hyp.transition(HypothesisStatus.VALIDATED)
        assert not ok
        assert period in why

    def test_missing_stress_rejected(self):
        hyp = _validated_hyp(stress_metrics={})
        hyp.transition(HypothesisStatus.TESTING)
        ok, why = hyp.transition(HypothesisStatus.VALIDATED)
        assert not ok
        assert "stress" in why

    def test_negative_oos_rejected(self):
        hyp = _validated_hyp(oos_metrics={"expectancy": -0.05})
        hyp.transition(HypothesisStatus.TESTING)
        ok, why = hyp.transition(HypothesisStatus.VALIDATED)
        assert not ok
        assert "oos" in why

    def test_three_winning_trades_not_enough(self):
        """Единичный/малый успех — не доказательство (TZ §11, §39)."""
        hyp = new_hypothesis(
            id="hyp-lucky",
            description="3 прибыльных дня",
            strategy_id="scalp",
            sample_size=3,
            train_metrics={"expectancy": 1.0},
            validation_metrics={"expectancy": 1.0},
            oos_metrics={"expectancy": 1.0},
            walk_forward_metrics={"expectancy": 1.0},
            stress_metrics={"stable": True},
        )
        hyp.transition(HypothesisStatus.TESTING)
        ok, why = hyp.transition(HypothesisStatus.VALIDATED)
        assert not ok
        assert "sample_size" in why


class TestStore:
    def test_persistence_roundtrip_with_history(self, tmp_path):
        path = tmp_path / "hypotheses.json"
        store = HypothesisStore(path)
        hyp = _validated_hyp(id="hyp-p1")
        store.add(hyp)
        assert store.transition("hyp-p1", HypothesisStatus.TESTING)[0]
        assert store.transition("hyp-p1", HypothesisStatus.VALIDATED)[0]

        reloaded = HypothesisStore(path)
        got = reloaded.get("hyp-p1")
        assert got is not None
        assert got.status is HypothesisStatus.VALIDATED
        assert len(got.status_log) == 3
        assert got.strategy_id == "ts_momentum"

    def test_duplicate_add_rejected(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        store.add(_validated_hyp(id="hyp-dup"))
        with pytest.raises(ValueError):
            store.add(_validated_hyp(id="hyp-dup"))

    def test_active_for_strategy(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        a = _validated_hyp(id="hyp-a", strategy_id="ts_momentum", confidence=0.6)
        b = _validated_hyp(id="hyp-b", strategy_id="ts_momentum", confidence=0.9)
        c = _validated_hyp(id="hyp-c", strategy_id="scalp", confidence=0.99)
        for h in (a, b, c):
            store.add(h)
            h.transition(HypothesisStatus.TESTING)
            h.transition(HypothesisStatus.VALIDATED)
            h.transition(HypothesisStatus.ACTIVE)
        best = store.active_for("ts_momentum")
        assert best.id == "hyp-b"
        assert store.active_for("unknown") is None

    def test_no_deletion_after_retire(self, tmp_path):
        path = tmp_path / "h.json"
        store = HypothesisStore(path)
        hyp = _validated_hyp(id="hyp-r")
        store.add(hyp)
        hyp.transition(HypothesisStatus.TESTING)
        hyp.transition(HypothesisStatus.VALIDATED)
        hyp.transition(HypothesisStatus.INVALIDATED, reason="edge исчез")
        hyp.transition(HypothesisStatus.RETIRED)
        store.save()
        reloaded = HypothesisStore(path)
        assert reloaded.get("hyp-r") is not None  # история не удаляется
        assert reloaded.get("hyp-r").status is HypothesisStatus.RETIRED


class TestLiveDegradation:
    """TZ §31: статистика ухудшилась -> DEGRADE (ACTIVE -> WEAKENING)."""

    def _active(self, store, expectancy=0.2):
        hyp = _validated_hyp(id="hyp-live", strategy_id="ts_momentum",
                             expectancy=expectancy)
        store.add(hyp)
        hyp.transition(HypothesisStatus.TESTING)
        hyp.transition(HypothesisStatus.VALIDATED)
        hyp.transition(HypothesisStatus.ACTIVE)
        return hyp

    def test_degrades_when_live_halved(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        self._active(store, expectancy=0.2)
        demoted = store.check_live_degradation(
            "ts_momentum", live_expectancy=0.08, live_samples=30
        )
        assert demoted == ["hyp-live"]
        assert store.get("hyp-live").status is HypothesisStatus.WEAKENING

    def test_negative_live_degrades(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        self._active(store, expectancy=0.2)
        demoted = store.check_live_degradation(
            "ts_momentum", live_expectancy=-0.1, live_samples=25
        )
        assert demoted == ["hyp-live"]

    def test_small_live_sample_ignored(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        self._active(store, expectancy=0.2)
        demoted = store.check_live_degradation(
            "ts_momentum", live_expectancy=-1.0, live_samples=5
        )
        assert demoted == []
        assert store.get("hyp-live").status is HypothesisStatus.ACTIVE

    def test_stable_live_kept_active(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        self._active(store, expectancy=0.2)
        demoted = store.check_live_degradation(
            "ts_momentum", live_expectancy=0.18, live_samples=40
        )
        assert demoted == []

    def test_weakening_logged_with_reason(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        self._active(store, expectancy=0.2)
        store.check_live_degradation(
            "ts_momentum", live_expectancy=-0.2, live_samples=22
        )
        last = store.get("hyp-live").status_log[-1]
        assert last["status"] == "WEAKENING"
        assert "live expectancy" in last["reason"]
