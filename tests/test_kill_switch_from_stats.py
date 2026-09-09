"""Блок A: пер-стратегийный килл-свитч из strategy_stats.json."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


class _Strat:
    def __init__(self, name: str):
        self.name = name


def _make_engine(tmp_path, buckets: dict | None):
    sp = tmp_path / "strategy_stats.json"
    if buckets is not None:
        sp.write_text(json.dumps({"buckets": buckets}), encoding="utf-8")
    cfg = TradingEngineConfig(
        stats_path=str(sp),
        state_path=str(tmp_path / "pos.json"),
        trades_path=str(tmp_path / "trades.jsonl"),
    )
    pipe = MagicMock()
    pipe.strategies = [_Strat("bad_strat"), _Strat("good_strat")]
    return TradingEngine(exchange=MagicMock(), pipeline=pipe, config=cfg)


def _names(eng) -> list[str]:
    return [s.name for s in eng.pipeline.strategies]


def test_loser_removed_winner_kept(tmp_path):
    eng = _make_engine(
        tmp_path,
        {
            # PF = 1/5 = 0.2 < 1, n=10 → килл-свитч (два бакета одного имени).
            "bad_strat|LOW_VOL|1h": {"sample_size": 6, "wins_sum_r": 1.0, "losses_sum_r": -3.0},
            "bad_strat|RANGE|5m": {"sample_size": 4, "wins_sum_r": 0.0, "losses_sum_r": -2.0},
            # PF = 6/2 = 3 → живёт.
            "good_strat|LOW_VOL|1h": {"sample_size": 10, "wins_sum_r": 6.0, "losses_sum_r": -2.0},
            # Мёртвое имя (не загружено) — пропускается молча, не падает.
            "ghost_strat|LOW_VOL|1h": {"sample_size": 50, "wins_sum_r": 0.0, "losses_sum_r": -50.0},
        },
    )
    assert _names(eng) == ["good_strat"]


def test_small_sample_kept(tmp_path):
    eng = _make_engine(
        tmp_path,
        {
            # Убыточна, но n=3 < 5 → рано судить, живёт.
            "bad_strat|LOW_VOL|1h": {"sample_size": 3, "wins_sum_r": 0.0, "losses_sum_r": -3.0},
        },
    )
    assert _names(eng) == ["bad_strat", "good_strat"]


def test_missing_file_keeps_all(tmp_path):
    eng = _make_engine(tmp_path, None)
    assert _names(eng) == ["bad_strat", "good_strat"]
