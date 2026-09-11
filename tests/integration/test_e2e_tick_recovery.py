"""E2E paper tick + crash recovery (TZ P2.1)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from astra_bot.core.ledger import TradeLedger
from astra_bot.decision.broker import PaperBroker
from astra_bot.engines.cost_model import CostModel


def _bar(close: str, high: str | None = None, low: str | None = None):
    c = Decimal(close)
    return SimpleNamespace(
        open=c,
        high=Decimal(high) if high else c,
        low=Decimal(low) if low else c,
        close=c,
        volume=Decimal("1"),
        open_time=1_700_000_000_000,
    )


def test_open_survives_process_restart(tmp_path, monkeypatch):
    from astra_bot.core import ledger as ledger_mod

    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(tmp_path / "paper_ledger.jsonl"))
    ledger_mod.reset_ledger(tmp_path / "paper_ledger.jsonl", initial_cash=Decimal("1000"))
    state = tmp_path / "paper_positions.json"
    trades = tmp_path / "paper_trades.jsonl"
    broker = PaperBroker(
        initial_capital=Decimal("1000"),
        state_path=state,
        trades_path=trades,
        cost_model=CostModel(
            taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001")
        ),
    )
    pos = broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=Decimal("130"),
        quantity=Decimal("1"),
        strategy="e2e",
    )
    assert pos.id
    restarted = PaperBroker(
        initial_capital=Decimal("1000"),
        state_path=state,
        trades_path=trades,
        cost_model=CostModel(
            taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001")
        ),
    )
    assert len(restarted.positions) == 1
    assert restarted.positions[0].symbol == "BTC-USDT"
    closed = restarted.check_exits(_bar("80", high="100", low="80"))
    assert closed
    assert closed[0].exit_reason


def test_ledger_replay_after_round_trip(tmp_path, monkeypatch):
    from astra_bot.core import ledger as ledger_mod

    path = tmp_path / "paper_ledger.jsonl"
    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(path))
    ledger_mod.reset_ledger(path, initial_cash=Decimal("1000"))
    state = tmp_path / "paper_positions.json"
    trades = tmp_path / "paper_trades.jsonl"
    broker = PaperBroker(
        initial_capital=Decimal("1000"),
        state_path=state,
        trades_path=trades,
        cost_model=CostModel(
            taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001")
        ),
    )
    broker.open_position(
        symbol="ETH-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=Decimal("130"),
        quantity=Decimal("1"),
        strategy="e2e",
    )
    broker.check_exits(_bar("80", high="100", low="80"))
    snap = TradeLedger(path, initial_cash=Decimal("1000")).replay(
        initial_cash=Decimal("1000")
    )
    assert snap.events >= 2
    assert snap.positions == {}
    # cash_delta on close equals realized pnl of the closed trade
    assert snap.cash == Decimal("1000") + snap.realized_pnl
