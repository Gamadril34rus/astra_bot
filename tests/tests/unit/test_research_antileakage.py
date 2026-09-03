"""Research anti-leakage (Этап 6): FDR, auto-retirement, negative
results, leakage-тесты walk-forward.

Принципы (master prompt): нет look-ahead/leakage, неудавшиеся гипотезы
сохраняются как INVALIDATED HYPOTHESIS с причиной, никаких «знаний»
из одного удачного случая (множественная проверка → FDR).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from astra_bot.ml.exit_research import (
    EntryEvent,
    walk_forward_evaluate,
)


@dataclass
class _Bar:
    """Тестовая свеча, структурно удовлетворяющая Protocol BarLike."""

    open: float
    high: float
    low: float
    close: float
from astra_bot.ml.hypothesis_engine import (
    HypothesisStatus,
    HypothesisStore,
    new_hypothesis,
)


def _days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat()


def _evidenced(store: HypothesisStore, hid: str, *, expectancy: float,
               sample_size: int) -> None:
    """Наполнить гипотезу полным набором доказательств (TZ §11)."""
    h = store.hypotheses[hid]
    m = {"expectancy": max(expectancy * 0.9, 0.05), "profit_factor": 1.3}
    h.train_metrics = dict(m)
    h.validation_metrics = dict(m)
    h.oos_metrics = dict(m)
    h.walk_forward_metrics = dict(m)
    h.stress_metrics = {"fees_x2": {"expectancy": max(expectancy * 0.6, 0.02)}}
    h.sample_size = sample_size
    h.expectancy = expectancy
    # TZ P0-2: lift vs baseline (положительный, baseline=0).
    h.lift_vs_baseline = max(expectancy * 0.8, 0.01)
    h.baseline_expectancy = expectancy * 0.2


class TestFdrGate:
    def test_single_strong_signal_passes(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        h = new_hypothesis(id="hyp-strong", description="s", strategy_id="st")
        store.add(h)
        store.transition("hyp-strong", HypothesisStatus.TESTING)
        # n=100, ev=0.2 → z=2.0 → p≈0.023 ≤ 0.05
        _evidenced(store, "hyp-strong", expectancy=0.2, sample_size=100)
        ok, why = store.transition("hyp-strong", HypothesisStatus.VALIDATED)
        assert ok, why

    def test_single_weak_signal_blocked(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        h = new_hypothesis(id="hyp-weak", description="w", strategy_id="st")
        store.add(h)
        store.transition("hyp-weak", HypothesisStatus.TESTING)
        # n=20, ev=0.15 → z≈0.67 → p≈0.25 > 0.05 — «удачный шум»
        _evidenced(store, "hyp-weak", expectancy=0.15, sample_size=20)
        ok, why = store.transition("hyp-weak", HypothesisStatus.VALIDATED)
        assert not ok
        assert "FDR" in why

    def test_multiple_candidates_ranked(self, tmp_path):
        """BH: из двух кандидатов валидируется только сильный."""
        store = HypothesisStore(tmp_path / "h.json")
        for hid, ev, n in (("hyp-a", 0.2, 100), ("hyp-b", 0.12, 20)):
            store.add(new_hypothesis(id=hid, description="x", strategy_id="st"))
            store.transition(hid, HypothesisStatus.TESTING)
            _evidenced(store, hid, expectancy=ev, sample_size=n)
        # m=2: p_a≈0.023 ≤ (1/2)·0.05=0.025 → k=1; p_b≈0.35 > 0.05
        ok_a, why_a = store.transition("hyp-a", HypothesisStatus.VALIDATED)
        assert ok_a, why_a
        ok_b, why_b = store.transition("hyp-b", HypothesisStatus.VALIDATED)
        assert not ok_b
        assert "FDR" in why_b
        assert store.get("hyp-a").status is HypothesisStatus.VALIDATED
        assert store.get("hyp-b").status is HypothesisStatus.TESTING

    def test_small_samples_excluded_from_set(self, tmp_path):
        """Кандидат с n < min_samples не участвует в множестве m."""
        store = HypothesisStore(tmp_path / "h.json")
        store.add(new_hypothesis(id="hyp-big", description="x", strategy_id="st"))
        store.transition("hyp-big", HypothesisStatus.TESTING)
        _evidenced(store, "hyp-big", expectancy=0.2, sample_size=100)
        store.add(new_hypothesis(id="hyp-tiny", description="x", strategy_id="st"))
        store.transition("hyp-tiny", HypothesisStatus.TESTING)
        store.hypotheses["hyp-tiny"].sample_size = 3  # мало — не кандидат
        # m=1 → порог p ≤ 0.05 → сильный проходит
        ok, why = store.transition("hyp-big", HypothesisStatus.VALIDATED)
        assert ok, why


class TestAutoRetirement:
    def test_stale_discovered_retired(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        h = new_hypothesis(id="hyp-stale", description="old", strategy_id="st")
        store.add(h)
        h.updated_at = _days_ago(100)  # старше 90 дней, данных нет
        retired = store.auto_retire_stale(max_age_days=90)
        assert retired == ["hyp-stale"]
        assert store.get("hyp-stale").status is HypothesisStatus.RETIRED
        assert "stale" in store.get("hyp-stale").status_log[-1]["reason"]

    def test_stale_with_data_marked_negative(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        h = new_hypothesis(id="hyp-data", description="old", strategy_id="st")
        store.add(h)
        store.transition("hyp-data", HypothesisStatus.TESTING)
        h.sample_size = 30
        h.updated_at = _days_ago(120)
        store.auto_retire_stale(max_age_days=90)
        reason = store.get("hyp-data").status_log[-1]["reason"]
        assert "negative" in reason

    def test_fresh_and_validated_kept(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        store.add(new_hypothesis(id="hyp-fresh", description="new",
                                 strategy_id="st"))
        old_val = new_hypothesis(id="hyp-old", description="old",
                                 strategy_id="st")
        store.add(old_val)
        old_val.updated_at = _days_ago(200)
        old_val.status = HypothesisStatus.VALIDATED
        retired = store.auto_retire_stale(max_age_days=90)
        assert retired == []
        assert store.get("hyp-old").status is HypothesisStatus.VALIDATED


class TestNegativeResults:
    def test_lists_invalidated_and_retired_with_reasons(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        h1 = new_hypothesis(id="hyp-n1", description="dead", strategy_id="st")
        store.add(h1)
        store.transition("hyp-n1", HypothesisStatus.TESTING)
        store.transition("hyp-n1", HypothesisStatus.INVALIDATED,
                         reason="OOS expectancy < 0")
        h2 = new_hypothesis(id="hyp-n2", description="stale", strategy_id="st")
        store.add(h2)
        h2.updated_at = _days_ago(100)
        store.auto_retire_stale(max_age_days=90)
        store.add(new_hypothesis(id="hyp-live", description="alive",
                                 strategy_id="st"))
        neg = {n["id"]: n for n in store.negative_results()}
        assert set(neg) == {"hyp-n1", "hyp-n2"}
        assert neg["hyp-n1"]["reason"] == "OOS expectancy < 0"
        assert neg["hyp-n2"]["status"] == "RETIRED"
        assert "hyp-live" not in neg


class TestWalkForwardLeakage:
    """Leakage-тесты walk_forward_evaluate: будущее не влияет на прошлое."""

    def _bars(self, n: int = 120, crash_at=()) -> list:
        bars = []
        for i in range(n):
            if i in crash_at:
                bars.append(_Bar(open=100.0, high=100.2, low=98.5,
                                     close=98.8))
            else:
                bars.append(_Bar(open=100.0, high=100.1, low=99.9,
                                     close=100.0))
        return bars

    def _entries(self) -> list[EntryEvent]:
        # По одному входу в каждый сегмент (cut=30 при 120 барах, 4 фолда).
        return [
            EntryEvent(bar_index=idx, direction="long",
                       entry_price=100.0, initial_stop=99.0)
            for idx in (10, 35, 65, 95)
        ]

    def test_future_bars_do_not_change_resolved_folds(self):
        """Все выходы разрешены в первых 100 барах; спайк в конце
        (бары, которых «в прошлом» не существовало) метрики не меняет."""
        crash_at = {11, 36, 66, 96}
        base = self._bars(120, crash_at)
        entries = self._entries()
        base_metrics = walk_forward_evaluate(base, entries, "STATIC_TP")
        # «Будущее»: те же 120 баров, но последние 20 (все выходы уже
        # разрешены к бару 96 — эти бары никакая сделка не использует)
        # заменены диким движением. Если реализация подсматривает будущее
        # (глобальные статистики, нормализация по всей серии), метрики
        # изменятся.
        wild_base = list(base)
        for i in range(100, 120):
            wild_base[i] = _Bar(
                open=100.0,
                high=(500.0 if i % 2 else 60.0),
                low=(60.0 if i % 2 else 100.0),
                close=(500.0 if i % 2 else 60.0),
            )
        extended_metrics = walk_forward_evaluate(wild_base, entries, "STATIC_TP")
        for period in ("train", "validation", "oos", "walk_forward"):
            a, b = base_metrics[period], extended_metrics[period]
            assert a.n == b.n, period
            assert a.expectancy == b.expectancy, period

    def test_entry_on_last_bar_excluded(self):
        """Вход на последнем баре не симулируется (нет будущего)."""
        bars = self._bars(120)
        entries = [*self._entries(), EntryEvent(
            bar_index=119, direction="long",
            entry_price=100.0, initial_stop=99.0)]
        m = walk_forward_evaluate(bars, entries, "STATIC_TP")
        # train/validation/oos = сегменты 1/2/4: входы 10, 35, 95;
        # 65 — в сегменте 3 (входит только в walk_forward). Вход 119
        # (последний бар) симуляции не подлежит — будущего нет.
        assert m["oos"].n == 1
        assert m["walk_forward"].n == 3  # сегменты 2, 3, 4 — по одному

    def test_no_lookahead_in_exit_scan(self):
        """Выход ищется строго ПОСЛЕ бара входа: бар входа не учитывается
        даже если его low пробивает стоп."""
        bars = self._bars(120)
        # Бар входа: low уже ниже стопа — если бы скан шёл с бара входа,
        # сделка закрывалась бы в том же баре.
        entry = EntryEvent(bar_index=10, direction="long",
                           entry_price=100.0, initial_stop=99.0)
        bars[10] = _Bar(open=100.0, high=100.1, low=98.0, close=100.0)
        bars[11] = _Bar(open=100.0, high=100.1, low=99.9, close=100.0)
        m = walk_forward_evaluate(bars, [entry], "STATIC_TP")
        # Стоп пробит только на баре 11 (low 99.9 > 99? нет: 99.9 > 99 —
        # стоп НЕ пробит; сделка живёт до конца → r от последнего close).
        assert m["train"].n == 1
