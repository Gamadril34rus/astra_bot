"""Exit Research: оценщики вариантов, walk-forward, gating (TZ §16/§17)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from astra_bot.ml.exit_research import (
    EXIT_VARIANTS,
    EntryEvent,
    evaluate_exit,
    register_exit_hypothesis,
    walk_forward_evaluate,
)
from astra_bot.ml.hypothesis_engine import HypothesisStore


@dataclass
class B:
    high: float
    low: float
    close: float
    open_time: int = 0


def _bar(h, lo, c):
    return B(h, lo, c)


# ---------------------------------------------------------------------------
# Оценщики (детерминированные пути)
# ---------------------------------------------------------------------------

class TestVariants:
    def test_all_eight_variants_defined(self):
        assert set(EXIT_VARIANTS) == {
            "STATIC_TP", "ATR_STOP", "STRUCTURE_STOP", "TRAILING",
            "BREAKEVEN", "TIME_STOP", "MOMENTUM_EXIT", "REGIME_EXIT",
        }

    def test_static_tp_exits_at_take(self):
        bars = [_bar(100.5, 99.9, 100.0), _bar(102.0, 99.8, 101.0)]
        e = EntryEvent(0, "long", 100.0, 99.0, take_profit=102.0)
        m = evaluate_exit(bars, [e], "STATIC_TP")
        assert m.n == 1
        assert m.expectancy == pytest.approx(2.0, abs=1e-9)

    def test_static_tp_exits_at_stop(self):
        bars = [_bar(100.5, 99.9, 100.0), _bar(99.9, 98.5, 98.6)]
        e = EntryEvent(0, "long", 100.0, 99.0, take_profit=102.0)
        m = evaluate_exit(bars, [e], "STATIC_TP")
        assert m.expectancy == pytest.approx(-1.0, abs=1e-9)

    def test_breakeven_protects_after_trigger(self):
        # +1.5R, затем откат: стоп в точке входа -> 0R вместо -1R.
        bars = [
            _bar(100.5, 99.9, 100.0),   # 0: вход
            _bar(101.5, 100.2, 100.6),  # 1: mfe 1.5R -> стоп 100
            _bar(100.8, 99.6, 99.95),   # 2: стоп 100 пробит
        ]
        e = EntryEvent(0, "long", 100.0, 99.0, take_profit=None)
        m = evaluate_exit(bars, [e], "BREAKEVEN", {"trigger_r": 1.0})
        assert m.expectancy == pytest.approx(0.0, abs=1e-9)
        mfe = m.avg_mfe_r
        assert mfe >= 1.49

    def test_time_stop_closes_after_n_bars(self):
        bars = [
            _bar(100.5, 99.9, 100.0),
            _bar(100.8, 100.1, 100.5),
            _bar(101.0, 100.1, 100.9),
        ]
        e = EntryEvent(0, "long", 100.0, 99.0, take_profit=None)
        m = evaluate_exit(bars, [e], "TIME_STOP", {"bars": 2})
        assert m.expectancy == pytest.approx(0.9, abs=1e-9)

    def test_momentum_exit_on_ema_cross(self):
        closes = [100.0, 100.2, 100.4, 100.6, 100.8, 101.0, 101.2,
                  101.4, 101.6, 99.0]
        bars = [_bar(c + 0.2, c - 0.2, c) for c in closes]
        e = EntryEvent(0, "long", 100.0, 95.0, take_profit=None)
        m = evaluate_exit(bars, [e], "MOMENTUM_EXIT", {"ema": 9})
        assert m.expectancy == pytest.approx((99.0 - 100.0) / 5.0, abs=1e-9)

    def test_regime_exit_on_panic(self):
        bars = [
            _bar(100.5, 99.9, 100.0),
            _bar(100.6, 100.1, 100.3),
            _bar(100.9, 100.2, 100.6),
            _bar(101.2, 100.5, 101.1),
        ]
        regimes = ["TREND", "TREND", "PANIC", "TREND"]
        e = EntryEvent(0, "long", 100.0, 99.0, take_profit=None)
        m = evaluate_exit(bars, [e], "REGIME_EXIT",
                          {"exit_regimes": ["PANIC"]}, regimes=regimes)
        assert m.expectancy == pytest.approx(0.6, abs=1e-9)

    def test_atr_stop_uses_atr_distance(self):
        # ATR постоянен 1.0 (high-low=1, prev close внутри диапазона).
        bars = [_bar(100.5, 99.5, 100.0)] * 20
        e = EntryEvent(0, "long", 100.0, 98.0, take_profit=None)
        # бар 19: low 99.5 <= стоп 100 - 2*1.0 = 98.0? нет: 99.5 > 98.
        bars.append(_bar(98.1, 97.5, 97.6))
        m = evaluate_exit(bars, [e], "ATR_STOP", {"k": 2.0})
        assert m.expectancy == pytest.approx(-1.0, abs=1e-9)  # (98-100)/2

    def test_trailing_tightens_stop(self):
        # risk 2R. АТR на баре 3 (по данным баров 1-2) = (1.5+2.5)/2 = 2.0,
        # экстремум 102.5 -> стоп 100.5; low 100.4 пробивает -> +0.25R
        # (вместо -1R при статическом стопе).
        bars = [
            _bar(100.5, 99.5, 100.0),
            _bar(101.5, 100.5, 101.0),
            _bar(102.5, 101.5, 102.0),
            _bar(102.6, 100.4, 100.5),
        ]
        e = EntryEvent(0, "long", 100.0, 98.0, take_profit=None)
        m = evaluate_exit(bars, [e], "TRAILING", {"k": 1.0})
        assert m.expectancy == pytest.approx(0.25, abs=1e-6)

    def test_costs_reduce_expectancy(self):
        bars = [_bar(100.5, 99.9, 100.0), _bar(99.9, 98.5, 98.6)]
        e = EntryEvent(0, "long", 100.0, 99.0, take_profit=102.0)
        m0 = evaluate_exit(bars, [e], "STATIC_TP", fee_pct=0.0, slippage_pct=0.0)
        m2 = evaluate_exit(bars, [e], "STATIC_TP", fee_pct=0.001, slippage_pct=0.001)
        assert m2.expectancy < m0.expectancy
        assert m0.expectancy - m2.expectancy == pytest.approx(
            2 * 0.002 * 100.0 / 1.0, abs=1e-9
        )  # 0.4R: 2 стороны × 0.2% от цены / R=1


# ---------------------------------------------------------------------------
# Walk-forward без leakage
# ---------------------------------------------------------------------------

def _uptrend_bars(n: int) -> list:
    out = []
    c = 100.0
    for _ in range(n):
        c += 0.5
        out.append(_bar(c + 0.5, c - 0.1, c))
    return out


def _entries_up(n: int, idxs: list[int]) -> list[EntryEvent]:
    out = []
    for i in idxs:
        entry = 100.0 + i * 0.5
        out.append(EntryEvent(i, "long", entry, entry - 1.0,
                              take_profit=entry + 1.0))
    return out


class TestWalkForward:
    def test_block_partition_no_leakage(self):
        bars = _uptrend_bars(400)
        entries = _entries_up(400, [10, 150, 250, 350])
        m = walk_forward_evaluate(bars, entries, "STATIC_TP", folds=3)
        # cut = 133: train [0,133): {10} | validation [133,266): {150,250}
        # | oos [266,400): {350}. Периоды не пересекаются по входам.
        assert m["train"].n == 1
        assert m["validation"].n == 2
        assert m["oos"].n == 1
        assert m["train"].expectancy == pytest.approx(1.0, abs=1e-9)
        assert m["validation"].expectancy == pytest.approx(1.0, abs=1e-9)
        assert m["oos"].expectancy == pytest.approx(1.0, abs=1e-9)
        assert m["walk_forward"].expectancy == pytest.approx(1.0, abs=1e-9)

    def test_folds_minimum(self):
        with pytest.raises(ValueError):
            walk_forward_evaluate(_uptrend_bars(40), _entries_up(40, [1]),
                                  "STATIC_TP", folds=2)


# ---------------------------------------------------------------------------
# Регистрация и gating (TZ §11/§17)
# ---------------------------------------------------------------------------

def _full_positive_metrics() -> dict:
    from astra_bot.ml.exit_research import ExitMetrics

    def mk(n, exp):
        return ExitMetrics(n=n, wins=n, expectancy=exp, win_rate=1.0,
                           profit_factor=2.0, avg_mfe_r=exp, avg_mae_r=-0.2)

    # Выборки, при которых oos-сигнал статистически значим (Этап 6):
    # sample = 20+15+20 = 55, oos ev 0.4 → z≈3 → p≈0.0015 < 0.05 (FDR).
    return {"train": mk(20, 0.5), "validation": mk(15, 0.4),
            "oos": mk(20, 0.4), "walk_forward": mk(4, 0.4)}


class TestPromotionGating:
    def test_full_evidence_promotes(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        hid, promoted, reason = register_exit_hypothesis(
            store, variant="BREAKEVEN", strategy="scalp", regime="ANY",
            params={"trigger_r": 1.0}, metrics=_full_positive_metrics(),
            stress_metrics={"fees_x2": 0.2, "stable": True},
        )
        assert promoted, reason
        assert store.get(hid).status.value == "VALIDATED"

    def test_negative_oos_blocks(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        metrics = _full_positive_metrics()
        metrics["oos"] = type(metrics["oos"])(n=5, wins=0, expectancy=-0.3,
                                              win_rate=0.0, profit_factor=0.5,
                                              avg_mfe_r=0.4, avg_mae_r=-1.0)
        hid, promoted, reason = register_exit_hypothesis(
            store, variant="TRAILING", strategy="scalp", regime="ANY",
            params={"k": 2.0}, metrics=metrics,
            stress_metrics={"fees_x2": 0.1},
        )
        assert not promoted
        # Отрицательный OOS блокирует либо через FDR (p≈1), либо через
        # требование expectancy > 0 в oos — оба пути запрещают промоцию.
        assert "oos" in reason or "FDR" in reason
        assert store.get(hid).status.value == "TESTING"

    def test_small_sample_blocks(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        metrics = _full_positive_metrics()
        for k in ("train", "validation", "oos"):
            metrics[k] = type(metrics[k])(n=1, wins=1, expectancy=0.5,
                                          win_rate=1.0, profit_factor=2.0)
        _, promoted, reason = register_exit_hypothesis(
            store, variant="TIME_STOP", strategy="scalp", regime="ANY",
            params={"bars": 12}, metrics=metrics,
            stress_metrics={"fees_x2": 0.1},
        )
        assert not promoted
        assert "sample_size" in reason

    def test_stress_metrics_stored(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        stress = {"fees_x2_expectancy": 0.18, "slippage_x3_expectancy": 0.12,
                  "stable": True}
        hid, promoted, _ = register_exit_hypothesis(
            store, variant="BREAKEVEN", strategy="scalp", regime="ANY",
            params={"trigger_r": 1.0}, metrics=_full_positive_metrics(),
            stress_metrics=stress,
        )
        assert promoted
        assert store.get(hid).stress_metrics == stress

    def test_id_stable_for_same_params(self, tmp_path):
        store = HypothesisStore(tmp_path / "h.json")
        h1, _, _ = register_exit_hypothesis(
            store, variant="BREAKEVEN", strategy="scalp", regime="ANY",
            params={"trigger_r": 1.0}, metrics=_full_positive_metrics(),
            stress_metrics={"stable": True},
        )
        h2, _, _ = register_exit_hypothesis(
            store, variant="BREAKEVEN", strategy="scalp", regime="ANY",
            params={"trigger_r": 1.0}, metrics=_full_positive_metrics(),
            stress_metrics={"stable": True},
        )
        assert h1 == h2
        assert len(store.hypotheses) == 1
