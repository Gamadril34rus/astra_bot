"""F12: move_stop cancel-retry on Position SL already exists."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_already_exists_cancel_then_place(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journal: list = []

    class Client:
        def __init__(self):
            self.places = 0
            self.cancelled = []
            self.orders = [
                SimpleNamespace(id="old-sl", order_type="STOP_MARKET", position_side="LONG"),
            ]

        async def place_order(self, **kwargs):
            self.places += 1
            if self.places == 1:
                raise RuntimeError("BingX API error: Position SL order already exists (code=101406)")
            oid = f"new-{self.places}"
            self.orders = [SimpleNamespace(id=oid, order_type="STOP_MARKET", position_side="LONG")]
            return SimpleNamespace(id=oid)

        async def get_open_orders(self, symbol=None):
            return list(self.orders)

        async def cancel_order(self, symbol, order_id):
            self.cancelled.append(order_id)
            self.orders = [o for o in self.orders if str(o.id) != str(order_id)]
            return True

    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
    from astra_bot.decision import live_arm_wiring as law

    monkeypatch.setattr(law, "append_journal", lambda row: journal.append(row))
    broker = LiveBroker(LiveBrokerConfig())
    broker.client = Client()
    # bypass risk gates
    broker._assert_can_trade = lambda: None  # type: ignore

    placed = asyncio.run(broker.move_stop("SOL-USDT", "long", Decimal("241.7"), Decimal("104.752")))
    assert broker.client.places == 2
    assert "old-sl" in broker.client.cancelled
    assert placed.id.startswith("new-")
    events = [j.get("event") for j in journal]
    assert "move_stop_cancel_retry" in events
    assert "move_stop_done" in events


def test_second_call_no_extra_when_place_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()

    class Client:
        def __init__(self):
            self.places = 0

        async def place_order(self, **kwargs):
            self.places += 1
            return SimpleNamespace(id=f"s{self.places}")

        async def get_open_orders(self, symbol=None):
            return [SimpleNamespace(id=f"s{self.places}", order_type="STOP_MARKET", position_side="LONG")]

        async def cancel_order(self, symbol, order_id):
            return True

    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig

    broker = LiveBroker(LiveBrokerConfig())
    broker.client = Client()
    broker._assert_can_trade = lambda: None  # type: ignore
    asyncio.run(broker.move_stop("SOL-USDT", "long", Decimal("100"), Decimal("104.752")))
    assert broker.client.places == 1
    asyncio.run(broker.move_stop("SOL-USDT", "long", Decimal("100"), Decimal("104.752")))
    assert broker.client.places == 2  # each successful call still places (caller dedups be_moved)


def test_no_amend_api_documented():
    """client_live has cancel/place only — no amend/modify."""
    from pathlib import Path
    src = Path("astra_bot/adapters/bingx/client_live.py").read_text(encoding="utf-8")
    assert "def amend" not in src
    assert "modify_order" not in src
