"""F24c: halt does not block protective move_stop/reduce; arm skips unreconciled plans."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig, LiveEntryPlan


@pytest.mark.asyncio
async def test_move_stop_allowed_under_halt():
    client = MagicMock()
    client.get_open_orders = AsyncMock(return_value=[])
    client.place_order = AsyncMock(return_value={"orderId": "1"})
    client.cancel_order = AsyncMock(return_value=True)
    broker = LiveBroker(client, LiveBrokerConfig())
    broker.set_halt("PF=0.7 n=7 < 1 (window=20)")
    await broker.move_stop("SOL-USDT", "short", Decimal("10"), Decimal("100.5"))
    assert client.place_order.await_count >= 1


@pytest.mark.asyncio
async def test_reduce_allowed_under_halt():
    client = MagicMock()
    pos = MagicMock()
    pos.symbol = "SOL-USDT"
    pos.quantity = Decimal("10")
    client.get_positions = AsyncMock(return_value=[pos])
    client.place_order = AsyncMock(return_value={"orderId": "2"})
    broker = LiveBroker(client, LiveBrokerConfig())
    broker.set_halt("kill")
    ok = await broker.reduce_position_qty("SOL-USDT", "short", Decimal("10"))
    assert ok is True
    assert client.place_order.await_count >= 1


@pytest.mark.asyncio
async def test_open_with_protection_blocked_under_halt():
    client = MagicMock()
    broker = LiveBroker(client, LiveBrokerConfig())
    broker.set_halt("kill")
    plan = LiveEntryPlan(
        symbol="SOL-USDT",
        side="short",
        entry_price=Decimal("100"),
        stop_price=Decimal("101"),
        quantity=Decimal("1"),
        take_profit_price=Decimal("98"),
    )
    with pytest.raises(RuntimeError, match="HALT"):
        await broker.open_with_protection(plan)


def test_plan_allows_new_entry_blocks_foreign(monkeypatch):
    from astra_bot.decision import live_arm_wiring as w

    plans = {
        "DOGE-USDT|short": {
            "symbol": "DOGE-USDT",
            "side": "short",
            "signal_id": "old-sid",
            "qty": 1,
        }
    }
    monkeypatch.setattr(w, "load_positions", lambda path=None: plans)
    monkeypatch.setattr(w, "append_journal", lambda row: None)
    assert w._plan_allows_new_entry("DOGE-USDT", "new-sid") is False
    assert w._plan_allows_new_entry("DOGE-USDT", "old-sid") is True
    assert w._plan_allows_new_entry("SOL-USDT", "x") is True


def test_write_live_equity_not_hard_cap(tmp_path):
    import scripts.zeus_live_clock as clock

    path = tmp_path / "eq.json"
    clock.write_live_equity(96711.5, path=path)
    data = json.loads(path.read_text())
    assert data["equity"] == 96711.5
    assert data["equity"] != 100.0
