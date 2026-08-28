"""Статистика стратегий по режимам + shrinkage EV (TZ §3.1/§6)."""

from __future__ import annotations

import pytest
from astra_bot.decision.strategy_stats import (
    StrategyRegimeStats,
    StrategyStatsStore,
    shrunken_expectancy,
)


class TestStrategyRegimeStats:
    def test_win_rate_expectancy_pf(self):
        s = StrategyRegimeStats()
        for r in (1.0, 1.5, 0.5, -1.0, -0.5):
            s.record(r)
        assert s.sample_size == 5
        assert s.wins == 3
        assert s.losses == 2
        assert s.win_rate == pytest.approx(0.6)
        assert s.expectancy_r == pytest.approx(0.3)
        assert s.avg_win_r == pytest.approx(1.0)
        assert s.avg_loss_r == pytest.approx(-0.75)
        assert s.profit_factor == pytest.approx(3.0 / 1.5)

    def test_mfe_mae_fees_accumulate(self):
        s = StrategyRegimeStats()
        s.record(r_multiple=1.0, mfe_r=2.0, mae_r=0.5, fees=0.3)
        s.record(r_multiple=-1.0, mfe_r=0.2, mae_r=1.1, fees=0.2)
        assert s.sum_mfe_r == pytest.approx(2.2)
        assert s.sum_mae_r == pytest.approx(1.6)
        assert s.sum_fees == pytest.approx(0.5)


class TestShrinkage:
    """Bayesian shrinkage к prior (TZ §6): маленькая выборка не даёт
    «огромного EV» из трёх сделок."""

    def test_no_data_returns_prior(self):
        ev, conf = shrunken_expectancy(None, prior_r=0.5)
        assert ev == pytest.approx(0.5)
        assert conf == 0.0

    def test_three_wins_barely_move_prior(self):
        s = StrategyRegimeStats()
        for _ in range(3):
            s.record(1.0)
        ev, conf = shrunken_expectancy(s, prior_r=0.0, shrinkage_k=30)
        # w = 3/33 = 0.0909
        assert conf == pytest.approx(3 / 33)
        assert ev == pytest.approx(3 / 33 * 1.0)
        assert ev < 0.15  # «3 сделки, 3 выигрыша» не даёт уверенного EV

    def test_large_sample_dominates_prior(self):
        s = StrategyRegimeStats()
        for r in (1.0, 1.2, 0.8, -1.0):
            for _ in range(75):
                s.record(r)
        ev, conf = shrunken_expectancy(s, prior_r=0.0, shrinkage_k=30)
        # w = 300/330 = 0.909
        assert conf == pytest.approx(300 / 330)
        sample_expectancy = sum([1.0, 1.2, 0.8, -1.0]) / 4
        assert ev == pytest.approx(300 / 330 * sample_expectancy + 30 / 330 * 0.0)


class TestStore:
    def test_record_updates_regime_and_any_buckets(self, tmp_path):
        store = StrategyStatsStore(tmp_path / "stats.json")
        store.record(
            strategy="scalp", regime="WEAK_BULL_TREND", timeframe="1h", r_multiple=1.0
        )
        store.record(
            strategy="scalp", regime="RANGE", timeframe="1h", r_multiple=-1.0
        )
        regime_bucket = store.get("scalp", "WEAK_BULL_TREND", "1h")
        assert regime_bucket.sample_size == 1
        any_bucket = store.get_any("scalp", "1h")
        assert any_bucket.sample_size == 2
        assert any_bucket.expectancy_r == pytest.approx(0.0)

    def test_get_falls_back_to_any_regime(self, tmp_path):
        store = StrategyStatsStore(tmp_path / "stats.json")
        store.record(
            strategy="pullback", regime="STRONG_BEAR_TREND",
            timeframe="1h", r_multiple=0.5,
        )
        # Другого режима нет — отдаём агрегированный ANY.
        bucket = store.get("pullback", "RANGE", "1h")
        assert bucket is not None
        assert bucket.sample_size == 1
        # Ничего нет вообще → None.
        assert store.get("unknown_strategy", "RANGE", "1h") is None

    def test_persistence_roundtrip(self, tmp_path):
        path = tmp_path / "stats.json"
        store = StrategyStatsStore(path)
        store.record(
            strategy="scalp", regime="RANGE", timeframe="1h",
            r_multiple=1.5, mfe_r=2.0, mae_r=0.4, fees=0.1,
        )
        reloaded = StrategyStatsStore(path)
        bucket = reloaded.get("scalp", "RANGE", "1h")
        assert bucket.sample_size == 1
        assert bucket.expectancy_r == pytest.approx(1.5)
        assert bucket.sum_mfe_r == pytest.approx(2.0)
        assert bucket.sum_fees == pytest.approx(0.1)

    def test_expectancy_with_fallback(self, tmp_path):
        store = StrategyStatsStore(tmp_path / "stats.json")
        store.record(
            strategy="scalp", regime="ANY", timeframe="1h", r_multiple=-1.0
        )
        # Конкретного режима нет: shrinkage идёт от ANY-выборки.
        ev, conf, stats = store.expectancy("scalp", "RANGE", "1h", prior_r=0.5)
        assert stats is not None
        assert conf == pytest.approx(1 / 31)
        assert ev == pytest.approx(1 / 31 * (-1.0) + 30 / 31 * 0.5)


@pytest.mark.parametrize(
    "n,prior,k", [(0, 0.3, 30), (3, 0.0, 30), (30, 0.0, 30), (300, -0.2, 30)]
)
def test_shrinkage_monotonic_in_sample(n, prior, k):
    """Доля доверия растёт с выборкой; оценка движется к эмпирической."""
    s = StrategyRegimeStats()
    for _ in range(n):
        s.record(1.0)
    ev, conf = shrunken_expectancy(s, prior_r=prior, shrinkage_k=k)
    expected_conf = n / (n + k) if n else 0.0
    assert conf == pytest.approx(expected_conf)
    if n:
        assert ev == pytest.approx(expected_conf * 1.0 + (1 - expected_conf) * prior)
