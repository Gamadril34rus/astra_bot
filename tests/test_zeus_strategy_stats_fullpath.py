"""Zeus stats path: _record_closed must write to zeus_strategy_stats.json."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.decision.strategy_stats import StrategyStatsStore
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


def _closed_trade(pid: str = "Z1", **kw):
    d = {
        "id": pid,
        "symbol": "BTC-USDT",
        "direction": "long",
        "entry_price": 100.0,
        "exit_price": 102.0,
        "quantity": 1.0,
        "pnl": 1.5,
        "pnl_pct": 1.5,
        "fees": 0.05,
        "r_multiple": 1.2,
        "mfe_r": 1.5,
        "mae_r": -0.2,
        "regime": "LOW_VOLATILITY",
        "regime_axes": "T:RANGE/V:LOW/L:DEEP",
        "timeframe": "4h",
        "exit_reason": "tp2",
        "strategy": "zeus_channel_boundary_4h",
        "opened_at": 1,
        "closed_at": 2,
    }
    d.update(kw)
    return SimpleNamespace(**d)


def test_record_closed_writes_zeus_stats_file(tmp_path, monkeypatch):
    """Full path: TradingEngine._record_closed -> StrategyStatsStore -> zeus file."""
    monkeypatch.chdir(tmp_path)
    zeus_stats = tmp_path / "zeus_strategy_stats.json"
    cfg = TradingEngineConfig(
        stats_path=str(zeus_stats),
        state_path=str(tmp_path / "pos.json"),
        trades_path=str(tmp_path / "trades.jsonl"),
        hypotheses_path=str(tmp_path / "hyp.json"),
        halt_alerts_path=str(tmp_path / "halt.json"),
        no_trade_observations_path=str(tmp_path / "nto.jsonl"),
        no_trade_outcomes_path=str(tmp_path / "nto_out.json"),
        pattern_exit_shadow_path=str(tmp_path / "pes.jsonl"),
        symbol_loss_guard_path=str(tmp_path / "slg.json"),
    )
    # Simulate Zeus runner: pipeline owns the store at ZEUS path
    store = StrategyStatsStore(path=zeus_stats, shrinkage_k=10.0, min_samples=5)
    pipe = MagicMock()
    pipe.strategies = []
    pipe.stats_store = store
    eng = TradingEngine(exchange=MagicMock(), pipeline=pipe, config=cfg)
    # Engine must use pipeline's store (same path)
    assert eng.stats_store is store
    assert eng.stats_store.path == zeus_stats
    # Position fully closed → not in broker.positions
    eng.broker.positions = []
    eng._record_closed([_closed_trade()])
    assert zeus_stats.exists(), "zeus_strategy_stats.json must appear after close"
    data = json.loads(zeus_stats.read_text(encoding="utf-8"))
    buckets = data.get("buckets") or {}
    any_key = "zeus_channel_boundary_4h|ANY|4h"
    assert any_key in buckets, f"missing {any_key}, got {list(buckets)}"
    assert buckets[any_key]["sample_size"] >= 1
    # Probation lift threshold
    for _ in range(4):
        eng._record_closed([_closed_trade(pid=f"Z{_}")])
    data = json.loads(zeus_stats.read_text(encoding="utf-8"))
    n = data["buckets"][any_key]["sample_size"]
    assert n >= 5
    bucket = eng.stats_store.get_any("zeus_channel_boundary_4h", "4h")
    assert bucket is not None and bucket.sample_size >= 5
    probation = bucket is None or bucket.sample_size < 5
    assert probation is False


def test_record_closed_skips_partial_still_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    zeus_stats = tmp_path / "zeus_strategy_stats.json"
    store = StrategyStatsStore(path=zeus_stats)
    pipe = MagicMock()
    pipe.strategies = []
    pipe.stats_store = store
    cfg = TradingEngineConfig(
        stats_path=str(zeus_stats),
        state_path=str(tmp_path / "pos.json"),
        trades_path=str(tmp_path / "trades.jsonl"),
        hypotheses_path=str(tmp_path / "hyp.json"),
    )
    eng = TradingEngine(exchange=MagicMock(), pipeline=pipe, config=cfg)
    eng.broker.positions = [SimpleNamespace(id="P_OPEN")]
    eng._record_closed([_closed_trade(pid="P_OPEN", exit_reason="tp1")])
    # partial still open → no sample
    if zeus_stats.exists():
        data = json.loads(zeus_stats.read_text(encoding="utf-8"))
        assert not data.get("buckets"), data
    else:
        assert True
