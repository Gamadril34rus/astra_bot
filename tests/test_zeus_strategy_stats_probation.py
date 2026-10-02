"""Zeus strategy stats: record → get_any, sample_size ≥ 5 → probation=False."""

from __future__ import annotations

from pathlib import Path

from astra_bot.decision.strategy_stats import StrategyStatsStore


def test_record_get_any_lifts_probation(tmp_path: Path) -> None:
    path = tmp_path / "zeus_strategy_stats.json"
    store = StrategyStatsStore(path=path, shrinkage_k=10.0, min_samples=5)

    strategy = "zeus_channel_boundary_4h"
    timeframe = "4h"
    for i in range(5):
        store.record(
            strategy=strategy,
            regime="LOW_VOLATILITY",
            timeframe=timeframe,
            r_multiple=0.5 if i % 2 == 0 else -0.3,
            mfe_r=1.0,
            mae_r=-0.2,
            fees=0.01,
            regime_axes="T:RANGE/V:LOW/L:DEEP",
        )

    any_bucket = store.get_any(strategy, timeframe)
    assert any_bucket is not None
    assert any_bucket.sample_size >= 5
    # Same rule as TradingEngine sizing / binding_constraint
    probation = any_bucket is None or any_bucket.sample_size < 5
    assert probation is False

    # Persistence round-trip
    store2 = StrategyStatsStore(path=path)
    any2 = store2.get_any(strategy, timeframe)
    assert any2 is not None
    assert any2.sample_size == any_bucket.sample_size


def test_empty_store_stays_on_probation(tmp_path: Path) -> None:
    store = StrategyStatsStore(path=tmp_path / "empty.json")
    bucket = store.get_any("zeus_wedge_retest_4h", "4h")
    probation = bucket is None or bucket.sample_size < 5
    assert probation is True
