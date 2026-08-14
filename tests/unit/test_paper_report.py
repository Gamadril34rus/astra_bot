"""Тесты агрегата paper-сделок для утреннего отчёта."""

import json
from pathlib import Path

import astra_bot.core.paper_report as pr


def _write_trades(tmp_path: Path, trades: list[dict], positions=None):
    (tmp_path / "models").mkdir(exist_ok=True)
    (tmp_path / "models" / "paper_trades.jsonl").write_text(
        "\n".join(json.dumps(t) for t in trades) + "\n", encoding="utf-8"
    )
    (tmp_path / "models" / "paper_positions.json").write_text(
        json.dumps({"positions": positions or [], "realized_pnl": "0", "initial_capital": "40000"}),
        encoding="utf-8",
    )


def test_aggregates_closed_trades(tmp_path, monkeypatch):
    trades = [
        {"symbol": "BTC-USDT", "direction": "long", "pnl": 25.0,
         "closed_at": 1_700_000_000_000, "exit_reason": "tp1"},
        {"symbol": "ETH-USDT", "direction": "short", "pnl": -10.0,
         "closed_at": 1_700_000_000_001, "exit_reason": "stop_loss"},
    ]
    _write_trades(tmp_path, trades, positions=[])
    monkeypatch.chdir(tmp_path)
    s = pr.paper_stats()
    assert s["total_trades"] == 2
    assert s["total_wins"] == 1
    assert s["total_losses"] == 1
    assert s["total_pnl"] == 15.0
    assert "BTC-USDT" in dict(s["top_symbols"])


def test_section_handles_empty(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir(exist_ok=True)
    (tmp_path / "models" / "paper_trades.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "models" / "paper_positions.json").write_text(
        json.dumps({"positions": [], "realized_pnl": "0"}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    text = pr.format_paper_section()
    assert "Пока нет закрытых сделок" in text


def test_reports_positions(tmp_path, monkeypatch):
    trades = [{"symbol": "BTC-USDT", "direction": "long", "pnl": 5.0,
               "closed_at": 1_700_000_000_000, "exit_reason": "tp1"}]
    positions = [{"symbol": "ETH-USDT", "direction": "short", "entry_price": "100", "quantity": "1"}]
    _write_trades(tmp_path, trades, positions)
    monkeypatch.chdir(tmp_path)
    s = pr.paper_stats()
    assert s["open_positions"] == 1
    text = pr.format_paper_section()
    assert "Открыто позиций: 1" in text
