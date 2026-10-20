"""Tests for live 40/60 partial TP1 + breakeven move (paper channel parity)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from astra_bot.decision.live_arm import SignalCandidate, plan_from_candidate
from astra_bot.decision.live_broker import LiveBroker, LiveEntryPlan
from astra_bot.decision.live_positions import load_positions, upsert_plan


class MockClient:
    def __init__(self, balance: Decimal = Decimal("500"), positions=None):
        self._balance = balance
        self._positions = list(positions or [])
        self._orders: list[Any] = []
        self.placed: list[dict] = []
        self.cancelled: list[tuple] = []

    async def get_balance_usdt(self) -> Decimal:
        return self._balance

    async def get_positions(self):
        return list(self._positions)

    async def get_open_orders(self, symbol: str | None = None):
        if symbol:
            return [o for o in self._orders if getattr(o, "symbol", None) == symbol]
        return list(self._orders)

    async def place_order(self, **kwargs):
        oid = f"ord-{len(self.placed)+1}"
        o = SimpleNamespace(
            id=oid,
            symbol=kwargs.get("symbol"),
            side=kwargs.get("side"),
            order_type=str(kwargs.get("order_type", "")).lower(),
            quantity=kwargs.get("quantity"),
            status="filled" if kwargs.get("order_type") == "MARKET" else "new",
            filled_quantity=kwargs.get("quantity") if kwargs.get("order_type") == "MARKET" else Decimal("0"),
            stop_price=kwargs.get("stop_price"),
        )
        self.placed.append(kwargs)
        self._orders.append(o)
        if kwargs.get("order_type") == "MARKET" and not kwargs.get("reduce_only"):
            side = "long" if kwargs.get("side") == "BUY" else "short"
            self._positions.append(
                SimpleNamespace(
                    symbol=kwargs["symbol"],
                    side=side,
                    quantity=kwargs["quantity"],
                    entry_price=Decimal("100"),
                )
            )
        return o

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        self.cancelled.append((symbol, order_id))
        self._orders = [o for o in self._orders if o.id != order_id]
        return True

    async def cancel_all_orders(self, symbol: str) -> int:
        n = len([o for o in self._orders if o.symbol == symbol])
        self._orders = [o for o in self._orders if o.symbol != symbol]
        return n

    async def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> None:
        return None

    async def set_margin_isolated(self, symbol: str) -> None:
        return None


def test_plan_tp1_at_1_5r_long(tmp_path, monkeypatch):
    async def _():
        monkeypatch.chdir(tmp_path)
        b = LiveBroker(MockClient(Decimal("500")))
        cand = SignalCandidate(
            symbol="BTC-USDT",
            side="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            take_profit=Decimal("130"),
            confidence=0.9,
            step_size=Decimal("0.001"),
        )
        plan = await plan_from_candidate(b, cand, exchange_open_count=0, exchange_symbols=set())
        assert plan is not None
        # R=10 → TP1 = 100 + 15 = 115
        assert plan.take_profit_price == Decimal("115")
    asyncio.run(_())


def test_plan_tp1_at_1_5r_short(tmp_path, monkeypatch):
    async def _():
        monkeypatch.chdir(tmp_path)
        b = LiveBroker(MockClient(Decimal("500")))
        cand = SignalCandidate(
            symbol="BTC-USDT",
            side="short",
            entry=Decimal("100"),
            stop=Decimal("110"),
            take_profit=Decimal("70"),
            confidence=0.9,
            step_size=Decimal("0.001"),
        )
        plan = await plan_from_candidate(b, cand, exchange_open_count=0, exchange_symbols=set())
        assert plan is not None
        assert plan.take_profit_price == Decimal("85")
    asyncio.run(_())


def test_open_tp1_is_40pct_reduce_only(tmp_path, monkeypatch):
    async def _():
        monkeypatch.chdir(tmp_path)
        c = MockClient(Decimal("500"))
        b = LiveBroker(c)
        plan = LiveEntryPlan(
            symbol="BTC-USDT",
            side="long",
            quantity=Decimal("1"),
            stop_price=Decimal("90"),
            take_profit_price=Decimal("115"),
            leverage=5,
            entry_price=Decimal("100"),
        )
        await b.open_with_protection(plan)
        tp_orders = [o for o in c.placed if "TAKE_PROFIT" in str(o.get("order_type", "")).upper()]
        assert len(tp_orders) == 1
        tp = tp_orders[0]
        assert tp["quantity"] == Decimal("0.4")
        assert tp.get("reduce_only") is False  # BingX hedge 109400
        assert tp.get("close_position") is False
        stop_orders = [o for o in c.placed if "STOP" in str(o.get("order_type", "")).upper() and "TAKE_PROFIT" not in str(o.get("order_type", "")).upper()]
        assert len(stop_orders) >= 1
        assert stop_orders[0].get("close_position") is True
    asyncio.run(_())


def test_upsert_stores_tp1(tmp_path):
    path = tmp_path / "pos.json"
    upsert_plan(
        "BTC-USDT",
        side="long",
        qty="1",
        stop="90",
        tp="115",
        entry="100",
        path=path,
    )
    plans = load_positions(path)
    row = plans["BTC-USDT|long"]
    assert row["tp1"] == "115"
    assert row.get("be_moved") is False


def test_sync_partial_breakeven_moves_stop(tmp_path, monkeypatch):
    async def _():
        from scripts.zeus_live_clock import sync_partial_breakeven

        path = tmp_path / "pos.json"
        upsert_plan(
            "BTC-USDT",
            side="long",
            qty="1.0",
            stop="90",
            tp="115",
            entry="100",
            path=path,
        )
        # ~0.6 remaining after TP1
        # price-BE: mark at +0.8R (no TP1 fill required)
        pos = [
            SimpleNamespace(
                symbol="BTC-USDT",
                side="long",
                quantity=Decimal("1.0"),
                entry_price=Decimal("100"),
                mark_price=Decimal("108"),
            )
        ]
        c = MockClient(positions=pos)
        b = LiveBroker(c)
        out = await sync_partial_breakeven(b, positions_path=path)
        assert "BTC-USDT" in out.get("moved", [])
        plans = load_positions(path)
        assert plans["BTC-USDT|long"]["be_moved"] is True
        # net-BE (taker×2) > entry; not gross "100"
        stop_s = plans["BTC-USDT|long"]["stop"]
        assert Decimal(stop_s) >= Decimal("100")
        assert Decimal(stop_s) <= Decimal("100.2")
        stops = [o for o in c.placed if "STOP" in str(o.get("order_type", "")).upper()]
        assert stops
        assert Decimal(str(stops[-1]["stop_price"])) >= Decimal("100")
    asyncio.run(_())


def test_sync_partial_skips_when_full_qty(tmp_path):
    async def _():
        from scripts.zeus_live_clock import sync_partial_breakeven

        path = tmp_path / "pos.json"
        upsert_plan(
            "BTC-USDT",
            side="long",
            qty="1.0",
            stop="90",
            tp="115",
            entry="100",
            path=path,
        )
        pos = [
            SimpleNamespace(
                symbol="BTC-USDT",
                side="long",
                quantity=Decimal("1.0"),
                entry_price=Decimal("100"),
                mark_price=Decimal("100"),  # mfe_r=0 → no BE
            )
        ]
        c = MockClient(positions=pos)
        b = LiveBroker(c)
        out = await sync_partial_breakeven(b, positions_path=path)
        assert out.get("moved") == []
    asyncio.run(_())


def test_sync_partial_legacy_no_tp1_still_be_on_price(tmp_path):
    async def _():
        from astra_bot.decision.live_positions import plan_key, save_positions
        from scripts.zeus_live_clock import sync_partial_breakeven

        path = tmp_path / "pos.json"
        # legacy plan without tp1
        save_positions(
            {
                plan_key("ETH-USDT", "long"): {
                    "symbol": "ETH-USDT",
                    "side": "long",
                    "qty": "2",
                    "stop": "80",
                    "tp": "120",
                    "entry": "100",
                    "be_moved": False,
                }
            },
            path,
        )
        pos = [
            SimpleNamespace(
                symbol="ETH-USDT",
                side="long",
                quantity=Decimal("2"),
                entry_price=Decimal("100"),
                mark_price=Decimal("116"),  # +0.8R on stop=80 → R=20
            )
        ]
        c = MockClient(positions=pos)
        b = LiveBroker(c)
        out = await sync_partial_breakeven(b, positions_path=path)
        # price-BE does not require tp1
        assert "ETH-USDT" in out.get("moved", [])
    asyncio.run(_())


def test_open_tp1_close_position_false(tmp_path, monkeypatch):
    """TP1 leg must not use closePosition (runner stays open)."""
    async def _():
        monkeypatch.chdir(tmp_path)
        c = MockClient(Decimal("500"))
        b = LiveBroker(c)
        plan = LiveEntryPlan(
            symbol="ETH-USDT",
            side="short",
            quantity=Decimal("2"),
            stop_price=Decimal("110"),
            take_profit_price=Decimal("85"),
            leverage=5,
            entry_price=Decimal("100"),
        )
        await b.open_with_protection(plan)
        tp_orders = [o for o in c.placed if "TAKE_PROFIT" in str(o.get("order_type", "")).upper()]
        assert len(tp_orders) == 1
        assert tp_orders[0]["quantity"] == Decimal("0.8")  # 40% of 2
        assert tp_orders[0].get("reduce_only") is False
        assert tp_orders[0].get("close_position") is False
    asyncio.run(_())


def test_sync_partial_already_be_moved(tmp_path):
    async def _():
        from scripts.zeus_live_clock import sync_partial_breakeven

        path = tmp_path / "pos.json"
        upsert_plan(
            "BTC-USDT",
            side="long",
            qty="1.0",
            stop="100",
            tp="115",
            entry="100",
            tp1="115",
            be_moved=True,
            path=path,
        )
        pos = [
            SimpleNamespace(
                symbol="BTC-USDT",
                side="long",
                quantity=Decimal("0.6"),
                entry_price=Decimal("100"),
                mark_price=Decimal("108"),
            )
        ]
        c = MockClient(positions=pos)
        b = LiveBroker(c)
        out = await sync_partial_breakeven(b, positions_path=path)
        assert out.get("moved") == []
    asyncio.run(_())


def test_risk_budget_uncapped_in_partial_context(tmp_path, monkeypatch):
    """risk_budget uses full wallet, not HARD_CAP (parity B)."""
    async def _():
        monkeypatch.chdir(tmp_path)
        c = MockClient(Decimal("499.7"))
        b = LiveBroker(c)
        # 1% of 499.7 = 5.00 (not clamped by hard_cap 100)
        assert await b.risk_budget(Decimal("0.01")) == Decimal("5.00")
        eq = await b.equity()
        assert eq == Decimal("100")  # equity still capped
    asyncio.run(_())


def test_quantize_tp_qty_aave_precision_1():
    """AAVE quantityPrecision=1 → step 0.1; 156.59634760 → 156.5."""
    b = LiveBroker(MockClient())
    step = Decimal("0.1")
    raw = Decimal("156.59634760")
    out = b._quantize_to_step(raw, step)
    assert out == Decimal("156.5")


def test_open_with_protection_quantizes_tp_to_precision(tmp_path, monkeypatch):
    """Mock instrument qp=1 → TP quantity has at most 1 decimal place."""
    async def _():
        monkeypatch.chdir(tmp_path)

        class _Inst:
            quantity_precision = 1
            step_size = Decimal("0.1")

        class _Client(MockClient):
            async def get_instrument(self, symbol: str):
                return _Inst()

        c = _Client(Decimal("500"))
        b = LiveBroker(c)
        plan = LiveEntryPlan(
            symbol="AAVE-USDT",
            side="long",
            quantity=Decimal("391.49086901"),
            stop_price=Decimal("90"),
            take_profit_price=Decimal("115"),
            leverage=5,
            entry_price=Decimal("100"),
        )
        await b.open_with_protection(plan)
        tp_orders = [o for o in c.placed if "TAKE_PROFIT" in str(o.get("order_type", "")).upper()]
        assert len(tp_orders) == 1
        q = tp_orders[0]["quantity"]
        # 40% of 391.49086901 = 156.596347604 → floor 0.1 → 156.5
        assert q == Decimal("156.5")
        # no long fractional tail
        assert q == q.quantize(Decimal("0.1"))
    asyncio.run(_())
