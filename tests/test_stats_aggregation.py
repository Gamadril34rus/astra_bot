"""Блок H: один sample на позицию (частичные tp1 не раздувают статистику)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


def _make_engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = TradingEngineConfig(
        stats_path=str(tmp_path / "strategy_stats.json"),
        state_path=str(tmp_path / "pos.json"),
        trades_path=str(tmp_path / "trades.jsonl"),
        hypotheses_path=str(tmp_path / "hyp.json"),
    )
    pipe = MagicMock()
    pipe.strategies = []
    return TradingEngine(exchange=MagicMock(), pipeline=pipe, config=cfg)


def _row(pid: str, **kw):
    d = {
        "id": pid, "symbol": "BTC-USDT", "direction": "long",
        "entry_price": 100.0, "exit_price": 101.0, "quantity": 0.5,
        "pnl": 10.0, "pnl_pct": 1.0, "fees": 1.0, "r_multiple": 1.0,
        "mfe_r": 1.0, "mae_r": -0.1, "regime": "RANGE",
        "regime_axes": "", "timeframe": "1h", "exit_reason": "tp1",
        "strategy": "momentum", "opened_at": 1, "closed_at": 2,
    }
    d.update(kw)
    return SimpleNamespace(**d)


def test_record_closed_writes_single_weighted_sample(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    # Позиция закрыта полностью (в broker.positions её нет): tp1 + финал.
    closed = [
        _row("P1", quantity=0.5, r_multiple=1.0, mfe_r=1.0, mae_r=-0.1, exit_reason="tp1"),
        _row("P1", quantity=0.5, r_multiple=0.0, mfe_r=1.2, mae_r=-0.3, exit_reason="stop_loss"),
    ]
    eng._record_closed(closed)
    rec = eng.stats_store.record
    assert rec.call_count == 1
    kw = rec.call_args.kwargs
    assert kw["r_multiple"] == 0.5  # (1.0×0.5 + 0.0×0.5) / 1.0
    assert kw["mfe_r"] == 1.2
    assert kw["mae_r"] == -0.3
    assert kw["strategy"] == "momentum"


def test_partial_close_defers_sample(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    # Остаток позиции ещё открыт → sample не пишем (допишем при полном).
    eng.broker.positions.append(SimpleNamespace(id="P1"))
    eng._record_closed([_row("P1", exit_reason="tp1")])
    assert eng.stats_store.record.call_count == 0


def test_aggregate_reads_earlier_partials_from_file(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    # Ранняя tp1-строка лежит в файле (прошлая сессия), сейчас — финал.
    old = dict(_row("P9").__dict__)
    old.update({"quantity": 0.5, "r_multiple": 1.0, "exit_reason": "tp1"})
    final = dict(_row("P9").__dict__)
    final.update({"quantity": 0.5, "r_multiple": -1.0, "exit_reason": "stop_loss"})
    # В live брокер пишет _log_trade в момент закрытия: в файле ОБЕ строки.
    (tmp_path / "trades.jsonl").write_text(
        json.dumps(old) + "\n" + json.dumps(final) + "\n", encoding="utf-8"
    )
    sample = eng._aggregate_position_sample("P9", [final])
    assert sample["r_multiple"] == 0.0  # (1.0 − 1.0) / 2 по объёму
