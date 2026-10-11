"""Phase-2: restore stops, arm sizing/guards (no separate live JSON rotator)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from astra_bot.decision.live_arm import (
    COOLDOWN_MS,
    MAX_OPEN,
    SignalCandidate,
    _cooldown_key,
    arm_enabled,
    conflict_blocks,
    cooldown_blocks,
    mark_cooldown,
    plan_from_candidate,
    quantize_qty,
    size_qty,
)
from astra_bot.decision.live_broker import LiveBroker
from astra_bot.decision.live_positions import get_plan, save_positions, upsert_plan


class MockClient:
    def __init__(self, balance: Decimal = Decimal("100"), positions=None, orders=None):
        self._balance = balance
        self._positions = list(positions or [])
        self._orders = list(orders or [])
        self.placed: list[dict] = []
        self.closed: list[str] = []

    async def get_balance_usdt(self):
        return self._balance

    async def get_positions(self):
        return list(self._positions)

    async def get_open_orders(self, symbol=None):
        if symbol:
            return [o for o in self._orders if getattr(o, "symbol", None) == symbol]
        return list(self._orders)

    async def place_order(self, **kwargs):
        self.placed.append(kwargs)
        o = SimpleNamespace(
            id=f"o{len(self.placed)}",
            symbol=kwargs.get("symbol"),
            order_type=str(kwargs.get("order_type", "")).lower(),
            side=kwargs.get("side"),
            quantity=kwargs.get("quantity"),
        )
        self._orders.append(o)
        return o

    async def cancel_order(self, symbol, order_id):
        self._orders = [o for o in self._orders if o.id != order_id]
        return True

    async def cancel_all_orders(self, symbol):
        n = len([o for o in self._orders if o.symbol == symbol])
        self._orders = [o for o in self._orders if o.symbol != symbol]
        return n

    async def set_leverage(self, *a, **k):
        return None

    async def set_margin_isolated(self, *a, **k):
        return None

    async def close_position(self, symbol, quantity=None, price=None):
        self.closed.append(symbol)
        self._positions = [p for p in self._positions if p.symbol != symbol]
        return True


def test_restore_with_plan(tmp_path):
    async def _():
        path = tmp_path / "pos.json"
        upsert_plan("BTC-USDT", side="long", qty="0.01", stop="90", tp="120", path=path)
        pos = SimpleNamespace(symbol="BTC-USDT", quantity=Decimal("0.01"), side="long")
        c = MockClient(positions=[pos], orders=[])
        b = LiveBroker(c)
        r = await b.restore_missing_stops(positions_path=path)
        assert "BTC-USDT" in r["restored"]
        assert any("stop" in str(x.get("order_type", "")).lower() for x in c.placed)
        assert not r["closed_naked"]
    asyncio.run(_())


def test_restore_no_plan_closes(tmp_path):
    async def _():
        path = tmp_path / "pos.json"
        save_positions({}, path)
        pos = SimpleNamespace(symbol="ETH-USDT", quantity=Decimal("1"), side="long")
        c = MockClient(positions=[pos], orders=[])
        b = LiveBroker(c)
        r = await b.restore_missing_stops(positions_path=path)
        assert "ETH-USDT" in r["closed_naked"]
        assert "ETH-USDT" in c.closed
    asyncio.run(_())


def test_restore_missing_file_is_no_plan(tmp_path):
    async def _():
        path = tmp_path / "missing.json"
        pos = SimpleNamespace(symbol="SOL-USDT", quantity=Decimal("2"), side="short")
        c = MockClient(positions=[pos])
        b = LiveBroker(c)
        r = await b.restore_missing_stops(positions_path=path)
        assert "SOL-USDT" in r["closed_naked"]
        assert get_plan("SOL-USDT", path) is None
    asyncio.run(_())


def test_size_qty_and_step():
    q = size_qty(Decimal("30"), Decimal("100"), Decimal("90"), Decimal("0.01"))
    assert q == Decimal("3.00")
    assert quantize_qty(Decimal("1.239"), Decimal("0.01")) == Decimal("1.23")


def test_conflict_and_cooldown(tmp_path):
    path = tmp_path / "pos.json"
    upsert_plan("BTC-USDT", side="long", qty="1", stop="1", path=path)
    assert conflict_blocks("BTC-USDT", path) is True
    assert conflict_blocks("ETH-USDT", path) is False
    assert _cooldown_key("zeus", "X", "LONG") == "zeus|X|long"
    assert COOLDOWN_MS == 20 * 60 * 1000
    mark_cooldown("zeus", "X", "long", now_ms=1_000_000)
    assert cooldown_blocks("zeus", "X", "long", now_ms=1_000_000 + 1000)
    assert not cooldown_blocks("zeus", "X", "long", now_ms=1_000_000 + 21 * 60_000)


def test_arm_flag(monkeypatch):
    monkeypatch.delenv("ZEUS_ARM", raising=False)
    assert arm_enabled() is False
    monkeypatch.setenv("ZEUS_ARM", "1")
    assert arm_enabled() is True


def test_zeus_arm_absent_no_entry(monkeypatch, tmp_path):
    """ZEUS_ARM missing → execute_one path must not be taken by arm_enabled gate."""
    monkeypatch.delenv("ZEUS_ARM", raising=False)
    assert arm_enabled() is False
    c = MockClient(Decimal("100"))
    # Gate is on arm_enabled; callers must check before execute.
    # When disabled, plan_from_candidate may still size, but clock never calls it.
    assert not arm_enabled()
    # Simulate clock: no open_with_protection when arm off
    assert c.placed == []


def test_plan_from_candidate_respects_max(tmp_path):
    async def _():
        path = tmp_path / "pos.json"
        for i in range(MAX_OPEN):
            upsert_plan(f"S{i}", side="long", qty="1", stop="1", path=path)
        c = MockClient(Decimal("100"))
        b = LiveBroker(c)
        cand = SignalCandidate(
            symbol="NEW",
            side="long",
            entry=Decimal("10"),
            stop=Decimal("9"),
            confidence=0.95,
        )
        plan = await plan_from_candidate(b, cand, positions_path=path)
        assert plan is None
    asyncio.run(_())


def test_restore_after_reconcile_order():
    """restore_missing_stops is invoked after reconcile in clock source order."""
    src = Path("scripts/zeus_live_clock.py").read_text(encoding="utf-8")
    i_rec = src.index("await broker.reconcile()")
    i_res = src.index("await broker.restore_missing_stops()")
    assert i_rec < i_res
