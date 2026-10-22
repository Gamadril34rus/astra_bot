"""F8.1 BE catch-up on partial TP1 (exch_qty <= 0.65 plan_qty)."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astra_bot.decision.live_positions import upsert_plan


def _broker(exch_qty: Decimal, moves: list):
    pos = SimpleNamespace(
        symbol="SOL-USDT",
        quantity=exch_qty,
        current_price=Decimal("103.0"),
    )

    class Client:
        async def get_positions(self):
            return [pos]

    class Broker:
        client = Client()

        async def move_stop(self, symbol, side, quantity, stop_price):
            moves.append({
                "symbol": symbol,
                "side": side,
                "qty": str(quantity),
                "stop": str(stop_price),
            })
            return SimpleNamespace(id="stop1")

        def net_breakeven_price(self, p):
            return Decimal("104.752")

    return Broker()


def test_be_catchup_60pct_moves_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
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
    moves: list = []
    broker = _broker(Decimal("241.7"), moves)

    from scripts.zeus_live_clock import sync_partial_breakeven

    async def run_twice():
        r1 = await sync_partial_breakeven(broker, positions_path=pos_path)
        r2 = await sync_partial_breakeven(broker, positions_path=pos_path)
        return r1, r2

    r1, r2 = asyncio.run(run_twice())
    assert "SOL-USDT" in r1.get("moved", [])
    assert len(moves) == 1
    assert Decimal(moves[0]["stop"]) == Decimal("104.752")
    assert Decimal(moves[0]["qty"]) == Decimal("241.7")
    # second tick: be_moved true → no second move
    assert r2.get("moved") == []
    assert len(moves) == 1
    plans = json.loads(pos_path.read_text())["positions"]
    row = plans["SOL-USDT|long"]
    assert row.get("be_moved") is True
    assert Decimal(str(row["qty"])) == Decimal("241.7")
    trades = (tmp_path / "models" / "zeus_live_trades.jsonl").read_text()
    assert "partial_fill" in trades
    assert "be_catchup_qty_gap" in trades


def test_be_catchup_full_qty_no_action(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
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
    moves: list = []
    broker = _broker(Decimal("402.84"), moves)
    from scripts.zeus_live_clock import sync_partial_breakeven

    r = asyncio.run(sync_partial_breakeven(broker, positions_path=pos_path))
    assert moves == []
    assert "SOL-USDT" not in r.get("moved", [])
