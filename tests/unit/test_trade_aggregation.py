"""Tests for P1-1: trade aggregation and P1-2: trade instrumentation.

Проверяем:
1. 3 частичных тейка + стоп = 1 агрегированная сделка.
2. Weighted exit price корректен.
3. Win rate считается по агрегированным сделкам.
4. R-метрики сохраняются при агрегации.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.aggregate_trades import aggregate_trades, AggregatedTrade


def _write_trades(path: Path, trades: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


class TestAggregation:
    def test_three_tps_plus_stop_single_trade(self, tmp_path):
        """3 частичных тейка + финальный стоп = 1 сделка."""
        trades = [
            {"id": "pos-1", "symbol": "BTC-USDT", "direction": "long",
             "entry_price": 100, "exit_price": 101, "quantity": 0.5,
             "pnl": 0.4, "fees": 0.1, "exit_reason": "tp1",
             "strategy": "test", "opened_at": 1000, "closed_at": 2000},
            {"id": "pos-1", "symbol": "BTC-USDT", "direction": "long",
             "entry_price": 100, "exit_price": 102, "quantity": 0.3,
             "pnl": 0.5, "fees": 0.06, "exit_reason": "tp2",
             "strategy": "test", "opened_at": 1000, "closed_at": 3000},
            {"id": "pos-1", "symbol": "BTC-USDT", "direction": "long",
             "entry_price": 100, "exit_price": 103, "quantity": 0.2,
             "pnl": 0.5, "fees": 0.04, "exit_reason": "tp3",
             "strategy": "test", "opened_at": 1000, "closed_at": 4000},
        ]
        inp = tmp_path / "trades.jsonl"
        out = tmp_path / "agg.jsonl"
        _write_trades(inp, trades)

        result = aggregate_trades(inp, out)
        assert len(result) == 1
        agg = result[0]
        assert agg.id == "pos-1"
        assert agg.num_exits == 3
        assert agg.total_quantity == pytest.approx(1.0)  # 0.5 + 0.3 + 0.2
        assert agg.total_pnl == pytest.approx(1.4)  # 0.4 + 0.5 + 0.5
        assert agg.final_exit_reason == "tp3"
        assert len(agg.exit_reasons) == 3

    def test_weighted_exit_price(self, tmp_path):
        """Weighted exit price = сумма(exit_price * qty) / сумма(qty)."""
        trades = [
            {"id": "pos-2", "symbol": "ETH-USDT", "direction": "long",
             "entry_price": 2000, "exit_price": 2010, "quantity": 5,
             "pnl": 40, "fees": 2, "exit_reason": "tp1",
             "strategy": "test", "opened_at": 1000, "closed_at": 2000},
            {"id": "pos-2", "symbol": "ETH-USDT", "direction": "long",
             "entry_price": 2000, "exit_price": 2020, "quantity": 5,
             "pnl": 80, "fees": 2, "exit_reason": "stop_loss",
             "strategy": "test", "opened_at": 1000, "closed_at": 3000},
        ]
        inp = tmp_path / "trades.jsonl"
        _write_trades(inp, trades)

        result = aggregate_trades(inp, tmp_path / "agg.jsonl")
        assert len(result) == 1
        # weighted = (2010*5 + 2020*5) / 10 = 2015
        assert result[0].weighted_exit_price == pytest.approx(2015.0)

    def test_separate_positions_stay_separate(self, tmp_path):
        """Разные позиции не агрегируются."""
        trades = [
            {"id": "pos-A", "symbol": "BTC-USDT", "direction": "long",
             "entry_price": 100, "exit_price": 101, "quantity": 1,
             "pnl": 0.8, "fees": 0.2, "exit_reason": "stop_loss",
             "strategy": "test", "opened_at": 1000, "closed_at": 2000},
            {"id": "pos-B", "symbol": "ETH-USDT", "direction": "short",
             "entry_price": 2000, "exit_price": 1990, "quantity": 1,
             "pnl": 8, "fees": 2, "exit_reason": "tp1",
             "strategy": "test", "opened_at": 1500, "closed_at": 2500},
        ]
        inp = tmp_path / "trades.jsonl"
        _write_trades(inp, trades)

        result = aggregate_trades(inp, tmp_path / "agg.jsonl")
        assert len(result) == 2
        ids = {r.id for r in result}
        assert ids == {"pos-A", "pos-B"}

    def test_win_rate_from_aggregated(self, tmp_path):
        """Win rate считается по агрегированным сделкам."""
        trades = [
            # pos-1: 2 выхода, итого PnL > 0 → win
            {"id": "pos-1", "symbol": "BTC-USDT", "direction": "long",
             "entry_price": 100, "exit_price": 101, "quantity": 0.5,
             "pnl": 0.4, "fees": 0.1, "exit_reason": "tp1",
             "strategy": "t", "opened_at": 1000, "closed_at": 2000},
            {"id": "pos-1", "symbol": "BTC-USDT", "direction": "long",
             "entry_price": 100, "exit_price": 99, "quantity": 0.5,
             "pnl": -0.6, "fees": 0.1, "exit_reason": "stop_loss",
             "strategy": "t", "opened_at": 1000, "closed_at": 3000},
            # pos-2: 1 выход, PnL < 0 → loss
            {"id": "pos-2", "symbol": "ETH-USDT", "direction": "long",
             "entry_price": 2000, "exit_price": 1990, "quantity": 1,
             "pnl": -12, "fees": 2, "exit_reason": "stop_loss",
             "strategy": "t", "opened_at": 1500, "closed_at": 2500},
        ]
        inp = tmp_path / "trades.jsonl"
        _write_trades(inp, trades)

        result = aggregate_trades(inp, tmp_path / "agg.jsonl")
        assert len(result) == 2
        # pos-1: 0.4 + (-0.6) = -0.2 → loss
        # pos-2: -12 → loss
        wins = sum(1 for r in result if r.is_win)
        assert wins == 0  # Both are losses after aggregation


class TestInstrumentation:
    def test_r_metrics_preserved(self, tmp_path):
        """R-метрики сохраняются при агрегации."""
        trades = [
            {"id": "pos-R", "symbol": "BTC-USDT", "direction": "long",
             "entry_price": 100, "exit_price": 102, "quantity": 0.5,
             "pnl": 0.8, "fees": 0.1, "exit_reason": "tp1",
             "r_multiple": 0.8, "mfe_r": 1.5, "mae_r": -0.3,
             "regime": "trend", "timeframe": "1h",
             "strategy": "t", "opened_at": 1000, "closed_at": 2000},
            {"id": "pos-R", "symbol": "BTC-USDT", "direction": "long",
             "entry_price": 100, "exit_price": 99, "quantity": 0.5,
             "pnl": -0.6, "fees": 0.1, "exit_reason": "stop_loss",
             "r_multiple": -0.6, "mfe_r": 1.5, "mae_r": -1.0,
             "regime": "trend", "timeframe": "1h",
             "strategy": "t", "opened_at": 1000, "closed_at": 3000},
        ]
        inp = tmp_path / "trades.jsonl"
        _write_trades(inp, trades)

        result = aggregate_trades(inp, tmp_path / "agg.jsonl")
        assert len(result) == 1
        agg = result[0]
        assert agg.regime == "trend"
        assert agg.timeframe == "1h"
        # mfe_r = max of all (best favorable excursion)
        assert agg.mfe_r == 1.5
        # mae_r = min of all (worst adverse excursion)
        assert agg.mae_r == -1.0
