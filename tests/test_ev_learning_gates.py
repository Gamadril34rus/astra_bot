"""Блок B: min_ev_r=0.05 + быстрый shrinkage k=10 в движке."""

from __future__ import annotations

from unittest.mock import MagicMock

from astra_bot.decision.strategy_stats import StrategyRegimeStats, shrunken_expectancy
from astra_bot.decision.trading_engine import TradingEngine


def test_shrinkage_k10_suppresses_optimistic_prior():
    # n=10, средний −0.5R, prior +0.5R: при k=10 вес данных 0.5 → EV=0.0 < 0.05.
    st = StrategyRegimeStats(sample_size=10, sum_r=-5.0)
    ev, conf = shrunken_expectancy(st, prior_r=0.5, shrinkage_k=10.0)
    assert ev < 0.05
    assert conf == 0.5


def test_engine_wires_gates(tmp_path, monkeypatch):
    # Полная сборка движка в пустом cwd: файлы состояния не трогаем.
    monkeypatch.chdir(tmp_path)
    eng = TradingEngine(exchange=MagicMock())
    assert eng.pipeline.config.min_ev_r == 0.05
    assert eng.pipeline.stats_store.shrinkage_k == 10.0
    assert eng.pipeline.config.ev_shrinkage_k == 10.0
