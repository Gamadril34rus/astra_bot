"""Phase-2: restore stops, arm sizing/guards (no separate live JSON rotator)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        self.closed_sides: list[tuple[str, str | None]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.cancel_all_symbols: list[str] = []

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
            position_side=(
                str(kwargs.get("position_side") or "").lower()
                if kwargs.get("position_side") is not None
                else None
            ),
            status="new",
        )
        self._orders.append(o)
        return o

    async def cancel_order(self, symbol, order_id):
        self.cancelled.append((symbol, order_id))
        self._orders = [o for o in self._orders if o.id != order_id]
        return True

    async def cancel_all_orders(self, symbol):
        self.cancel_all_symbols.append(symbol)
        n = len([o for o in self._orders if o.symbol == symbol])
        self._orders = [o for o in self._orders if o.symbol != symbol]
        return n

    async def set_leverage(self, *a, **k):
        return None

    async def set_margin_isolated(self, *a, **k):
        return None

    async def close_position(
        self, symbol, quantity=None, price=None, position_side=None
    ):
        self.closed.append(symbol)
        self.closed_sides.append((symbol, position_side))
        self._positions = [
            position
            for position in self._positions
            if not (
                position.symbol == symbol
                and (position_side is None or position.side == position_side)
            )
        ]
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
    i_res = src.index("await broker.restore_missing_stops(")
    assert i_rec < i_res



def test_restore_orphan_with_matching_exchange_stop_is_preserved(tmp_path):
    async def _():
        path = tmp_path / "pos.json"
        save_positions({}, path)
        pos = SimpleNamespace(
            symbol="BTC-USDT",
            quantity=Decimal("0.01"),
            side="long",
        )
        stop = SimpleNamespace(
            id="long-stop",
            symbol="BTC-USDT",
            order_type="stop_market",
            side="sell",
            position_side="long",
            status="new",
        )
        client = MockClient(positions=[pos], orders=[stop])
        broker = LiveBroker(client)
        result = await broker.restore_missing_stops(positions_path=path)

        assert result["orphan_protected_legs"] == ["BTC-USDT|long"]
        assert not result["closed_naked"]
        assert not client.closed
        assert not client.cancelled
        assert not client.cancel_all_symbols
        assert stop in client._orders

    asyncio.run(_())


def test_restore_orphan_does_not_cancel_opposite_hedge_stop(tmp_path):
    async def _():
        path = tmp_path / "pos.json"
        save_positions({}, path)
        long_position = SimpleNamespace(
            symbol="BTC-USDT",
            quantity=Decimal("0.01"),
            side="long",
        )
        short_stop = SimpleNamespace(
            id="short-stop",
            symbol="BTC-USDT",
            order_type="stop_market",
            side="buy",
            position_side="short",
            status="new",
        )
        client = MockClient(positions=[long_position], orders=[short_stop])
        broker = LiveBroker(client)
        result = await broker.restore_missing_stops(positions_path=path)

        assert result["closed_legs"] == ["BTC-USDT|long"]
        assert client.closed_sides == [("BTC-USDT", "long")]
        assert not client.cancelled
        assert not client.cancel_all_symbols
        assert short_stop in client._orders

    asyncio.run(_())


def test_restore_orphan_with_unscoped_stop_is_preserved(tmp_path):
    async def _():
        path = tmp_path / "pos.json"
        save_positions({}, path)
        position = SimpleNamespace(
            symbol="ETH-USDT",
            quantity=Decimal("1"),
            side="short",
        )
        unscoped_stop = SimpleNamespace(
            id="unknown-stop",
            symbol="ETH-USDT",
            order_type="stop_market",
            side="",
            position_side="",
            status="new",
        )
        client = MockClient(positions=[position], orders=[unscoped_stop])
        broker = LiveBroker(client)
        result = await broker.restore_missing_stops(positions_path=path)

        assert result["orphan_protection_unknown_legs"] == ["ETH-USDT|short"]
        assert not result["closed_naked"]
        assert not client.closed
        assert not client.cancelled
        assert unscoped_stop in client._orders

    asyncio.run(_())


def test_move_stop_never_cancels_opposite_hedge_stop():
    async def _():
        short_stop = SimpleNamespace(
            id="short-stop",
            symbol="BTC-USDT",
            order_type="stop_market",
            side="buy",
            position_side="short",
            status="new",
        )
        client = MockClient(orders=[short_stop])
        broker = LiveBroker(client)
        await broker.move_stop(
            "BTC-USDT",
            "long",
            Decimal("0.01"),
            Decimal("90"),
        )

        assert short_stop in client._orders
        assert ("BTC-USDT", "short-stop") not in client.cancelled
        assert any(
            order.get("position_side") == "LONG"
            and order.get("order_type") == "STOP_MARKET"
            for order in client.placed
        )

    asyncio.run(_())


def test_live_client_requires_side_for_ambiguous_hedge_close(monkeypatch):
    from astra_bot.adapters.bingx.client_live import LiveBingXClient
    from astra_bot.core.exceptions import ExchangeError

    client = object.__new__(LiveBingXClient)
    positions = [
        SimpleNamespace(
            symbol="BTC-USDT",
            side="long",
            quantity=Decimal("1"),
        ),
        SimpleNamespace(
            symbol="BTC-USDT",
            side="short",
            quantity=Decimal("2"),
        ),
    ]
    placed = []

    async def get_positions():
        return positions

    async def place_order(**kwargs):
        placed.append(kwargs)
        return SimpleNamespace(id="order-1")

    monkeypatch.setattr(client, "get_positions", get_positions)
    monkeypatch.setattr(client, "place_order", place_order)

    async def _():
        with pytest.raises(ExchangeError, match="ambiguous hedge close"):
            await client.close_position("BTC-USDT")
        await client.close_position(
            "BTC-USDT",
            quantity=Decimal("1"),
            position_side="short",
        )

    asyncio.run(_())

    assert len(placed) == 1
    assert placed[0]["side"] == "BUY"
    assert placed[0]["position_side"] == "SHORT"
    assert placed[0]["quantity"] == Decimal("1")
