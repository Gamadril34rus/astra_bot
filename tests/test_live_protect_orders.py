"""Protective orders B1–B3 (owner/arena 20.10)."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astra_bot.adapters.bingx.client_live import _parse_order


def test_live_broker_imports():
    """B1 regression: dataclass not applied to _journal_protect."""
    import astra_bot.decision.live_broker as lb
    assert hasattr(lb, "LiveBrokerConfig")
    assert hasattr(lb, "LiveBroker")
    # config is a real dataclass instance
    cfg = lb.LiveBrokerConfig()
    assert cfg.max_leverage >= 1


def test_parse_order_flags_true_false_strings_absent():
    o = _parse_order(
        {
            "orderId": "1",
            "symbol": "BTC-USDT",
            "side": "SELL",
            "type": "STOP_MARKET",
            "quantity": "0.1",
            "status": "NEW",
            "reduceOnly": "true",
            "closePosition": "true",
        },
        "BTC-USDT",
    )
    assert o.reduce_only is True
    assert o.close_position is True

    o2 = _parse_order(
        {
            "orderId": "2",
            "symbol": "ETH-USDT",
            "side": "BUY",
            "type": "TAKE_PROFIT_MARKET",
            "quantity": "1",
            "status": "NEW",
            "reduceOnly": False,
            "closePosition": "false",
        },
        "ETH-USDT",
    )
    assert o2.reduce_only is False
    assert o2.close_position is False

    o3 = _parse_order(
        {
            "orderId": "3",
            "symbol": "ADA-USDT",
            "side": "SELL",
            "type": "LIMIT",
            "quantity": "10",
            "status": "NEW",
        },
        "ADA-USDT",
    )
    assert o3.reduce_only is None
    assert o3.close_position is None


@pytest.mark.asyncio
async def test_ensure_get_open_orders_fail_aborts_no_close(tmp_path):
    from astra_bot.decision import live_positions as lp
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig

    path = tmp_path / "pos.json"
    lp.upsert_plan(
        symbol="ADA-USDT", side="short",
        entry=Decimal("0.22"), stop=Decimal("0.23"),
        qty=Decimal("100"), path=path,
    )
    closed = []

    async def close_position(symbol, quantity=None):
        closed.append(symbol)
        return True

    client = SimpleNamespace(
        get_positions=AsyncMock(
            return_value=[SimpleNamespace(symbol="ADA-USDT", quantity=Decimal("100"), side="short")]
        ),
        get_open_orders=AsyncMock(side_effect=RuntimeError("network")),
        close_position=close_position,
        cancel_all_orders=AsyncMock(),
    )
    broker = LiveBroker(client=client, config=LiveBrokerConfig())
    out = await broker.ensure_protection(positions_path=path, bare_state_path=tmp_path / "bare.json")
    assert out.get("aborted") == "get_open_orders"
    assert closed == []
    assert out.get("closed") == []


@pytest.mark.asyncio
async def test_ensure_verify_fail_after_place_does_not_close(tmp_path):
    from astra_bot.decision import live_positions as lp
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig

    path = tmp_path / "pos.json"
    lp.upsert_plan(
        symbol="ETH-USDT", side="long",
        entry=Decimal("100"), stop=Decimal("90"),
        qty=Decimal("1"), path=path,
    )
    closed = []
    cancels = []

    async def close_position(symbol, quantity=None):
        closed.append(symbol)
        return True

    async def cancel_all_orders(symbol):
        cancels.append(symbol)
        return 0

    n_goo = {"n": 0}

    async def get_open_orders(symbol=None):
        n_goo["n"] += 1
        if n_goo["n"] == 1:
            return []  # initial: bare
        raise RuntimeError("verify timeout")

    client = SimpleNamespace(
        get_positions=AsyncMock(
            return_value=[
                SimpleNamespace(symbol="ETH-USDT", quantity=Decimal("1"), side="long", mark_price=Decimal("101"))
            ]
        ),
        get_open_orders=get_open_orders,
        close_position=close_position,
        cancel_all_orders=cancel_all_orders,
    )
    broker = LiveBroker(client=client, config=LiveBrokerConfig())

    async def move_stop(*a, **k):
        return SimpleNamespace(id="s1")

    broker.move_stop = move_stop  # type: ignore
    out = await broker.ensure_protection(positions_path=path, bare_state_path=tmp_path / "bare.json")
    assert closed == []
    assert cancels == []
    assert out.get("closed") == []


@pytest.mark.asyncio
async def test_ensure_two_confirmed_rejects_closes_only_that_symbol(tmp_path):
    from astra_bot.decision import live_positions as lp
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig

    path = tmp_path / "pos.json"
    bare = tmp_path / "bare.json"
    for sym in ("ADA-USDT", "BTC-USDT"):
        lp.upsert_plan(
            symbol=sym, side="short",
            entry=Decimal("1"), stop=Decimal("1.1"),
            qty=Decimal("10"), path=path,
        )
    closed = []
    cancels = []
    close_order = []

    async def close_position(symbol, quantity=None):
        close_order.append("close")
        closed.append(symbol)
        return True

    async def cancel_all_orders(symbol):
        cancels.append(symbol)
        close_order.append("cancel")
        return 1

    async def move_stop(*a, **k):
        raise RuntimeError("place_order rejected code=101 msg=Insufficient margin")

    # after close, ADA gone
    pos_live = [
        SimpleNamespace(symbol="ADA-USDT", quantity=Decimal("10"), side="short", mark_price=Decimal("1.0")),
        SimpleNamespace(symbol="BTC-USDT", quantity=Decimal("10"), side="short", mark_price=Decimal("1.0")),
    ]

    async def get_positions():
        return [p for p in pos_live if p.symbol not in closed]

    client = SimpleNamespace(
        get_positions=get_positions,
        get_open_orders=AsyncMock(return_value=[]),
        close_position=close_position,
        cancel_all_orders=cancel_all_orders,
    )
    broker = LiveBroker(client=client, config=LiveBrokerConfig())
    broker.move_stop = move_stop  # type: ignore

    out1 = await broker.ensure_protection(positions_path=path, bare_state_path=bare)
    assert out1.get("closed") == []  # first bare tick
    out2 = await broker.ensure_protection(positions_path=path, bare_state_path=bare)
    # both ADA and BTC are bare with confirmed reject → both may close on 2nd tick
    assert "ADA-USDT" in out2.get("closed", [])
    # cancel only after close
    assert close_order.index("close") < close_order.index("cancel")


@pytest.mark.asyncio
async def test_ensure_cancel_not_before_close(tmp_path):
    """Explicit: cancel_all_orders never called if close fails."""
    from astra_bot.decision import live_positions as lp
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig

    path = tmp_path / "pos.json"
    bare = tmp_path / "bare.json"
    lp.upsert_plan(
        symbol="LINK-USDT", side="short",
        entry=Decimal("10"), stop=Decimal("11"),
        qty=Decimal("5"), path=path,
    )
    cancels = []

    async def close_position(symbol, quantity=None):
        raise RuntimeError("close failed")

    async def cancel_all_orders(symbol):
        cancels.append(symbol)
        return 0

    async def move_stop(*a, **k):
        raise RuntimeError("place_order rejected code=99 msg=Insufficient margin")

    client = SimpleNamespace(
        get_positions=AsyncMock(
            return_value=[
                SimpleNamespace(symbol="LINK-USDT", quantity=Decimal("5"), side="short", mark_price=Decimal("10"))
            ]
        ),
        get_open_orders=AsyncMock(return_value=[]),
        close_position=close_position,
        cancel_all_orders=cancel_all_orders,
    )
    broker = LiveBroker(client=client, config=LiveBrokerConfig())
    broker.move_stop = move_stop  # type: ignore
    await broker.ensure_protection(positions_path=path, bare_state_path=bare)
    await broker.ensure_protection(positions_path=path, bare_state_path=bare)
    assert cancels == []
