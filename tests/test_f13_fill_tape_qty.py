"""F13: fill-tape, qty reconcile, strategy_from_signal_id, restore cancel journal."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astra_bot.decision.live_income_report import (
    _income_row_id,
    _parse_income_fill,
    load_fill_tape_seen,
    process_income_fill_tape,
    save_fill_tape_seen,
    strategy_from_signal_id,
)


def test_strategy_from_signal_id_prefix():
    assert strategy_from_signal_id("zeus_channel_boundary_4h|BTC-USDT|long") == "zeus_channel_boundary_4h"
    assert strategy_from_signal_id("zeus_wedge_retest_4h:ETH:short") == "zeus_wedge_retest_4h"
    assert strategy_from_signal_id(None) is None
    assert strategy_from_signal_id("") is None


def test_income_row_id_prefers_tran_id():
    assert _income_row_id({"tranId": "T1", "time": 1, "income": 1}) == "T1"
    assert _income_row_id({"incomeType": "REALIZED_PNL", "time": 9, "income": -1, "symbol": "X"}) == "X|REALIZED_PNL|9|-1"


def test_parse_income_fill_only_realized():
    assert _parse_income_fill({"_incomeType": "FUNDING_FEE", "income": 1, "time": 1}, "BTC-USDT") is None
    f = _parse_income_fill({"_incomeType": "REALIZED_PNL", "income": -12.5, "time": 1_700_000_000_000, "tranId": "A"}, "BTC-USDT")
    assert f is not None
    assert f["event"] == "partial_fill"
    assert f["source"] == "income_tape"
    assert f["pnl"] == -12.5
    assert f["income_id"] == "A"


@pytest.mark.asyncio
async def test_fill_tape_dedup(tmp_path):
    state = tmp_path / "seen.json"
    trades = tmp_path / "trades.jsonl"
    client = MagicMock()
    client.get_realized_income = AsyncMock(return_value=[
        {"tranId": "1", "_incomeType": "REALIZED_PNL", "income": 1.0, "time": 1_700_000_000_000},
        {"tranId": "1", "_incomeType": "REALIZED_PNL", "income": 1.0, "time": 1_700_000_000_000},
        {"tranId": "2", "_incomeType": "REALIZED_PNL", "income": 2.0, "time": 1_700_000_000_001},
    ])
    r1 = await process_income_fill_tape(client, ["BTC-USDT"], state_path=state, trades_path=trades)
    assert r1["written"] == 2
    r2 = await process_income_fill_tape(client, ["BTC-USDT"], state_path=state, trades_path=trades)
    assert r2["written"] == 0
    lines = trades.read_text().strip().splitlines()
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_reconcile_plan_qty_threshold(tmp_path, monkeypatch):
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
    from astra_bot.decision.live_positions import save_positions, plan_key

    pos_path = tmp_path / "pos.json"
    save_positions({
        plan_key("LTC-USDT", "short"): {
            "symbol": "LTC-USDT", "side": "short", "qty": 474.75, "stop": 59.0,
            "entry": 59.17, "plan_qty": 474.75,
        }
    }, pos_path)
    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[
        SimpleNamespace(symbol="LTC-USDT", side="short", quantity=284.85),
    ])
    broker = LiveBroker(client, LiveBrokerConfig())
    journal = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journal.append(row),
    )
    out = await broker.reconcile_plan_qty(positions_path=pos_path, threshold=0.005)
    assert len(out["reconciled"]) == 1
    assert out["reconciled"][0]["event"] == "qty_reconcile"
    assert abs(out["reconciled"][0]["new_qty"] - 284.85) < 1e-6
    # second call — no change
    out2 = await broker.reconcile_plan_qty(positions_path=pos_path, threshold=0.005)
    assert out2["reconciled"] == []
