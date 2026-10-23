"""F9: hedge position parse + missing-with-orders + catch-up path."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


def test_parse_five_partial_all_returned():
    from astra_bot.adapters.bingx.client_live import parse_position_item

    rows = [
        {"symbol": "BTC-USDT", "positionSide": "LONG", "positionAmt": "0.4", "markPrice": "80000", "avgPrice": "82000"},
        {"symbol": "SOL-USDT", "positionSide": "LONG", "positionAmt": "241.7", "markPrice": "103", "avgPrice": "104.7"},
        {"symbol": "DOGE-USDT", "positionSide": "SHORT", "positionAmt": "-100", "markPrice": "0.08", "avgPrice": "0.09"},
        {"symbol": "AVAX-USDT", "positionSide": "SHORT", "availableAmt": "-50", "markPrice": "9", "avgPrice": "9.2"},
        {"symbol": "LTC-USDT", "positionSide": "SHORT", "holdVol": "10", "markPrice": "58", "avgPrice": "59"},
    ]
    pos = [parse_position_item(r) for r in rows]
    pos = [p for p in pos if p is not None]
    assert len(pos) == 5
    sol = next(p for p in pos if p.symbol == "SOL-USDT")
    assert sol.quantity == Decimal("241.7")
    assert sol.side == "long"


def test_parse_bad_row_others_ok():
    from astra_bot.adapters.bingx.client_live import parse_position_item

    good = parse_position_item(
        {"symbol": "BTC-USDT", "positionSide": "LONG", "positionAmt": "1", "markPrice": "1", "avgPrice": "1"}
    )
    bad_amt = parse_position_item(
        {"symbol": "X", "positionSide": "LONG", "positionAmt": "not-a-number", "markPrice": "1"}
    )
    empty_sym = parse_position_item({"positionSide": "LONG", "positionAmt": "1", "markPrice": "1"})
    zero = parse_position_item({"symbol": "Z", "positionAmt": "0"})
    assert good is not None and good.symbol == "BTC-USDT"
    assert bad_amt is None  # no parseable non-zero qty
    assert empty_sym is None
    assert zero is None


def test_position_missing_with_orders_journals(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    from astra_bot.decision.live_positions import upsert_plan
    from scripts.zeus_live_clock import detect_position_missing_with_orders

    upsert_plan(
        "SOL-USDT",
        side="long",
        qty="402",
        stop="102",
        tp="108",
        entry="104.7",
        path=tmp_path / "models" / "zeus_live_positions.json",
    )
    orders = [
        SimpleNamespace(order_type="STOP_MARKET", id="1"),
        SimpleNamespace(order_type="TAKE_PROFIT_MARKET", id="2"),
    ]

    class Client:
        async def get_positions(self):
            return []

        async def get_open_orders(self, symbol=None):
            return orders

    class Broker:
        client = Client()

    r = asyncio.run(detect_position_missing_with_orders(Broker()))
    assert "SOL-USDT" in r.get("missing", [])
    j = (tmp_path / "models" / "zeus_live_journal.jsonl").read_text()
    assert "position_missing_with_open_orders" in j


def test_be_catchup_sol_partial_e2e(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    from astra_bot.decision.live_positions import upsert_plan, load_positions
    from scripts.zeus_live_clock import sync_partial_breakeven

    pos_path = tmp_path / "models" / "zeus_live_positions.json"
    upsert_plan(
        "SOL-USDT",
        side="long",
        qty="402.84",
        plan_qty="402.84",
        stop="102.114",
        tp="108.326",
        entry="104.752",
        path=pos_path,
        be_moved=False,
    )
    moves = []
    pos = SimpleNamespace(
        symbol="SOL-USDT",
        quantity=Decimal("241.7"),
        current_price=Decimal("104.752"),
    )

    class Client:
        async def get_positions(self):
            return [pos]

    class Broker:
        client = Client()

        async def move_stop(self, symbol, side, quantity, stop_price):
            moves.append((symbol, side, str(quantity), str(stop_price)))
            return SimpleNamespace(id="s1")

        def net_breakeven_price(self, p):
            return Decimal("104.752")

    r = asyncio.run(sync_partial_breakeven(Broker(), positions_path=pos_path))
    assert "SOL-USDT" in r.get("moved", [])
    assert moves[0][3] == "104.752"
    plan = load_positions(pos_path)["SOL-USDT|long"]
    assert plan.get("be_moved") is True
    assert Decimal(str(plan["qty"])) == Decimal("241.7")
    assert "partial_fill" in (tmp_path / "models" / "zeus_live_trades.jsonl").read_text()
    assert "be_catchup" in (tmp_path / "models" / "zeus_live_journal.jsonl").read_text()
