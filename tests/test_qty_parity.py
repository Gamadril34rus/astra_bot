"""Qty-parity: exchange filled size drives protections (owner №21a)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from astra_bot.decision.live_broker import (
    LiveBroker,
    LiveBrokerConfig,
    LiveEntryPlan,
    _is_tp_price_past_error,
    _order_filled_qty,
)


def test_order_filled_qty_variants():
    assert _order_filled_qty(SimpleNamespace(filledQty=Decimal("208"))) == Decimal("208")
    assert _order_filled_qty({"executedQty": "100.5"}) == Decimal("100.5")
    assert _order_filled_qty(SimpleNamespace(quantity=0)) is None
    assert _order_filled_qty(SimpleNamespace()) is None
    assert _order_filled_qty(None) is None


def test_is_tp_price_past_error():
    assert _is_tp_price_past_error(
        Exception("Take Profit price should be lower than the current price")
    )
    assert not _is_tp_price_past_error(Exception("insufficient margin"))


def test_fact_qty_drives_tp_and_stop():
    placed = []

    class Client:
        async def set_margin_isolated(self, symbol):
            return None

        async def set_leverage(self, symbol, lev, side="LONG"):
            return None

        async def get_balance_usdt(self):
            return Decimal("100000")

        async def get_open_orders(self, symbol=None):
            return []

        async def place_order(self, **kw):
            placed.append(kw)
            if kw.get("order_type") == "MARKET" and kw.get("side") == "SELL":
                return SimpleNamespace(
                    filled_price=Decimal("60"),
                    filledQty=Decimal("208"),
                    quantity=Decimal("1040"),
                    id="e1",
                )
            return SimpleNamespace(id="x")

        async def get_instruments(self, symbol=None):
            return []

    broker = LiveBroker(Client(), LiveBrokerConfig())
    plan = LiveEntryPlan(
        symbol="LTC-USDT",
        side="short",
        quantity=Decimal("1040"),
        stop_price=Decimal("61"),
        take_profit_price=Decimal("58"),
        leverage=3,
        entry_price=Decimal("60"),
    )
    res = asyncio.run(broker.open_with_protection(plan))
    assert Decimal(str(res["fact_qty"])) == Decimal("208")
    assert Decimal(str(res["plan_qty"])) == Decimal("1040")
    assert res["qty_ratio"] == pytest.approx(0.2, rel=1e-3)
    assert any("биржа дала" in a for a in res["alerts"])
    stop_orders = [p for p in placed if p.get("order_type") == "STOP_MARKET"]
    tp_orders = [p for p in placed if p.get("order_type") == "TAKE_PROFIT_MARKET"]
    assert stop_orders and stop_orders[0]["quantity"] == Decimal("208")
    assert tp_orders and tp_orders[0]["quantity"] == Decimal("83.2")


def test_fail_open_no_filled_qty():
    placed = []

    class Client:
        async def set_margin_isolated(self, symbol):
            return None

        async def set_leverage(self, symbol, lev, side="LONG"):
            return None

        async def get_balance_usdt(self):
            return Decimal("1000")

        async def get_open_orders(self, symbol=None):
            return []

        async def place_order(self, **kw):
            placed.append(kw)
            if kw.get("order_type") == "MARKET" and kw.get("side") == "BUY":
                return SimpleNamespace(filled_price=10, id="e")
            return SimpleNamespace(id="x")

        async def get_instruments(self, symbol=None):
            return []

    broker = LiveBroker(Client(), LiveBrokerConfig())
    plan = LiveEntryPlan(
        symbol="X-USDT",
        side="long",
        quantity=Decimal("50"),
        stop_price=Decimal("9"),
        take_profit_price=Decimal("12"),
        leverage=3,
        entry_price=Decimal("10"),
    )
    res = asyncio.run(broker.open_with_protection(plan))
    assert Decimal(str(res["fact_qty"])) == Decimal("50")
    assert not res["alerts"]


def test_tp_past_level_market_leg_be_moved(monkeypatch):
    placed = []

    class Client:
        async def set_margin_isolated(self, symbol):
            return None

        async def set_leverage(self, symbol, lev, side="LONG"):
            return None

        async def get_balance_usdt(self):
            return Decimal("1000")

        async def get_open_orders(self, symbol=None):
            return []

        async def place_order(self, **kw):
            placed.append(kw)
            if kw.get("order_type") == "MARKET" and kw.get("side") == "SELL":
                return SimpleNamespace(
                    filled_price=60, filledQty=Decimal("100"), id="e"
                )
            if kw.get("order_type") == "TAKE_PROFIT_MARKET":
                raise Exception("Take Profit price should be lower than the current price")
            if kw.get("order_type") == "STOP_MARKET":
                return SimpleNamespace(id="s")
            if kw.get("order_type") == "MARKET" and kw.get("side") == "BUY":
                return SimpleNamespace(id="mkt_tp")
            return SimpleNamespace(id="x")

        async def get_instruments(self, symbol=None):
            return []

    import astra_bot.decision.live_broker as lb

    rows = []
    monkeypatch.setattr(lb, "append_live_trade", lambda row, path=None: rows.append(row))

    broker = LiveBroker(Client(), LiveBrokerConfig())
    plan = LiveEntryPlan(
        symbol="LTC-USDT",
        side="short",
        quantity=Decimal("100"),
        stop_price=Decimal("61"),
        take_profit_price=Decimal("58"),
        leverage=3,
        entry_price=Decimal("60"),
    )
    res = asyncio.run(broker.open_with_protection(plan))
    assert res["be_moved"] is True
    assert any(
        p.get("order_type") == "MARKET" and p.get("quantity") == Decimal("40")
        for p in placed
    )
    assert any(r.get("event") == "tp1_market_past_level" for r in rows)
