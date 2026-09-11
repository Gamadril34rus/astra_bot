"""E2E paper tick + crash recovery (TZ P2.1)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from astra_bot.core.ledger import TradeLedger
from astra_bot.core.state_store import StateStore
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


def _broker(tmp_path, *, state=None, trades=None):
    return PaperBroker(
        initial_capital=Decimal("1000"),
        state_path=state or (tmp_path / "paper_positions.json"),
        trades_path=trades or (tmp_path / "paper_trades.jsonl"),
        cost_model=CostModel(
            taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001")
        ),
    )


def test_open_survives_process_restart(tmp_path, monkeypatch):
    from astra_bot.core import ledger as ledger_mod

    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(tmp_path / "paper_ledger.jsonl"))
    ledger_mod.reset_ledger(tmp_path / "paper_ledger.jsonl", initial_cash=Decimal("1000"))
    state = tmp_path / "paper_positions.json"
    trades = tmp_path / "paper_trades.jsonl"
    broker = _broker(tmp_path, state=state, trades=trades)
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
    restarted = _broker(tmp_path, state=state, trades=trades)
    assert len(restarted.positions) == 1
    assert restarted.positions[0].symbol == "BTC-USDT"
    closed = restarted.check_exits(_bar("80", high="100", low="80"))
    assert closed
    assert closed[0].exit_reason


def test_restore_broker_from_bundle_when_component_missing(tmp_path, monkeypatch):
    from astra_bot.core import ledger as ledger_mod

    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(tmp_path / "paper_ledger.jsonl"))
    ledger_mod.reset_ledger(tmp_path / "paper_ledger.jsonl", initial_cash=Decimal("1000"))
    state = tmp_path / "paper_positions.json"
    trades = tmp_path / "paper_trades.jsonl"
    broker = _broker(tmp_path, state=state, trades=trades)
    pos = broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=Decimal("130"),
        quantity=Decimal("1"),
        strategy="e2e",
    )
    equity_before = broker.equity
    store = StateStore(tmp_path / "state_bundle.json")
    bundle = store.snapshot(broker=broker)
    store.save(bundle)
    state.unlink()
    restarted = _broker(tmp_path, state=state, trades=trades)
    assert restarted.positions == []
    loaded = store.load()
    assert loaded is not None
    assert store.restore_broker(restarted, loaded) is True
    assert len(restarted.positions) == 1
    assert restarted.positions[0].id == pos.id
    assert restarted.positions[0].symbol == "BTC-USDT"
    assert restarted.equity == equity_before


def test_ledger_replay_dedup_same_event(tmp_path, monkeypatch):
    from astra_bot.core import ledger as ledger_mod

    path = tmp_path / "paper_ledger.jsonl"
    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(path))
    ledger = ledger_mod.reset_ledger(path, initial_cash=Decimal("1000"))
    broker = _broker(tmp_path)
    pos = broker.open_position(
        symbol="ETH-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=Decimal("130"),
        quantity=Decimal("1"),
        strategy="e2e",
    )
    before = path.read_text(encoding="utf-8")
    n_before = before.count("\n")
    broker._ledger(
        "order",
        symbol="ETH-USDT",
        side="long",
        qty=Decimal("1"),
        ref_id=pos.id,
        event_id=f"order:{pos.id}:open",
    )
    broker._ledger(
        "fill",
        symbol="ETH-USDT",
        side="long",
        qty=Decimal("1"),
        ref_id=pos.id,
        event_id=f"fill:{pos.id}:open",
    )
    assert path.read_text(encoding="utf-8") == before
    assert path.read_text(encoding="utf-8").count("\n") == n_before
    snap = TradeLedger(path, initial_cash=Decimal("1000")).replay(
        initial_cash=Decimal("1000")
    )
    assert snap.events == n_before
    # singleton still has the seen-index
    assert ledger.replay().events == n_before


def test_ledger_replay_after_round_trip(tmp_path, monkeypatch):
    from astra_bot.core import ledger as ledger_mod

    path = tmp_path / "paper_ledger.jsonl"
    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(path))
    ledger_mod.reset_ledger(path, initial_cash=Decimal("1000"))
    state = tmp_path / "paper_positions.json"
    trades = tmp_path / "paper_trades.jsonl"
    broker = _broker(tmp_path, state=state, trades=trades)
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
    assert snap.cash == Decimal("1000") + snap.realized_pnl
