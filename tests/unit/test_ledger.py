"""Double-entry trade ledger (TZ P1.8)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.core.ledger import LedgerEvent, TradeLedger, reset_ledger


def test_append_and_replay_round_trip(tmp_path):
    path = tmp_path / "paper_ledger.jsonl"
    ledger = reset_ledger(path, initial_cash=Decimal("1000"))
    ledger.record(
        "fill",
        symbol="BTC-USDT",
        side="long",
        qty="0.1",
        price="50000",
        fee="2.5",
        cash_delta="-5002.5",
        position_delta="0.1",
        ref_id="pos-1",
    )
    ledger.record(
        "fill",
        symbol="BTC-USDT",
        side="long",
        qty="0.1",
        price="51000",
        fee="2.55",
        pnl="95",
        cash_delta="5097.45",
        position_delta="-0.1",
        ref_id="pos-1",
    )
    snap = ledger.replay(initial_cash=Decimal("1000"))
    assert snap.events == 2
    assert snap.positions == {}
    assert snap.cash == Decimal("1000") - Decimal("5002.5") + Decimal("5097.45")
    assert snap.realized_pnl == Decimal("95")
    assert snap.fees == Decimal("2.5") + Decimal("2.55")


def test_open_position_leaves_inventory(tmp_path):
    ledger = TradeLedger(tmp_path / "l.jsonl", initial_cash=Decimal("1000"))
    ledger.record(
        "fill",
        symbol="ETH-USDT",
        side="long",
        qty="1",
        position_delta="1",
        cash_delta="-2000",
    )
    snap = ledger.replay(initial_cash=Decimal("1000"))
    assert snap.positions["ETH-USDT"] == Decimal("1")
    assert snap.cash == Decimal("-1000")


def test_unknown_kind_rejected():
    with pytest.raises(ValueError, match="unknown ledger kind"):
        LedgerEvent(kind="explode")


def test_schema_version_on_row(tmp_path):
    ledger = TradeLedger(tmp_path / "l.jsonl")
    event = ledger.record("order", symbol="BTC-USDT", side="long")
    assert event.schema_version == 1
    line = (tmp_path / "l.jsonl").read_text().strip()
    assert '"schema_version":1' in line.replace(" ", "")
