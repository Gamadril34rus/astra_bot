"""BE MFE from initial_stop; trigger default 0.5R (owner 30.10)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.decision.live_broker import LiveBroker
from astra_bot.decision.live_positions import load_positions, upsert_plan


class MockClient:
    def __init__(self, positions=None):
        self.positions = positions or []
        self.placed = []

    async def get_positions(self):
        return self.positions

    async def place_order(self, **kw):
        self.placed.append(kw)
        return SimpleNamespace(id="1", status="NEW")

    async def get_open_orders(self, symbol=None):
        return []

    async def cancel_order(self, symbol, oid):
        return True


def test_be_fires_at_0_6r_with_initial_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_BE_TRIGGER_R", "0.5")
    path = tmp_path / "pos.json"
    upsert_plan(
        "BTC-USDT",
        side="long",
        qty="1.0",
        stop="90",
        tp="120",
        entry="100",
        initial_stop="90",
        path=path,
    )
    pos = [
        SimpleNamespace(
            symbol="BTC-USDT",
            side="long",
            quantity=Decimal("1.0"),
            entry_price=Decimal("100"),
            mark_price=Decimal("106"),  # 0.6R on risk 10
        )
    ]
    b = LiveBroker(MockClient(positions=pos))
    from scripts.zeus_live_clock import sync_partial_breakeven

    out = asyncio.run(sync_partial_breakeven(b, positions_path=path))
    assert "BTC-USDT" in out.get("moved", [])
    plans = load_positions(path)
    assert plans["BTC-USDT|long"]["be_moved"] is True


def test_mfe_uses_initial_not_tight_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_BE_TRIGGER_R", "0.5")
    path = tmp_path / "pos.json"
    upsert_plan(
        "ETH-USDT",
        side="long",
        qty="1.0",
        stop="99.5",  # tightened
        tp="120",
        entry="100",
        initial_stop="90",  # risk 10 → mark 103 = 0.3R
        path=path,
    )
    pos = [
        SimpleNamespace(
            symbol="ETH-USDT",
            side="long",
            quantity=Decimal("1.0"),
            entry_price=Decimal("100"),
            mark_price=Decimal("103"),
        )
    ]
    b = LiveBroker(MockClient(positions=pos))
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    from scripts.zeus_live_clock import sync_partial_breakeven

    out = asyncio.run(sync_partial_breakeven(b, positions_path=path))
    assert "ETH-USDT" not in out.get("moved", [])
    ticks = [j for j in journals if j.get("event") == "be_mark_tick"]
    assert ticks, "expected be_mark_tick journal"
    assert abs(float(ticks[0]["mfe_r"]) - 0.3) < 0.05
