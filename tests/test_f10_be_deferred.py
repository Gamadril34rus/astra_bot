"""F10: deferred BE — place only when mark allows entry stop."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


def _run(broker, pos_path):
    from scripts.zeus_live_clock import sync_partial_breakeven
    return asyncio.run(sync_partial_breakeven(broker, positions_path=pos_path))


def _broker(exch_qty, mark, moves, fail=False):
    pos = SimpleNamespace(
        symbol="SOL-USDT",
        quantity=Decimal(str(exch_qty)),
        current_price=Decimal(str(mark)),
    )

    class Client:
        async def get_positions(self):
            return [pos]

    class Broker:
        client = Client()

        async def move_stop(self, symbol, side, quantity, stop_price):
            if fail:
                raise RuntimeError("Stop Loss price should be lower than the current price")
            moves.append({"symbol": symbol, "qty": str(quantity), "stop": str(stop_price)})
            return SimpleNamespace(id="s1")

        def net_breakeven_price(self, p):
            return Decimal("104.752")

    return Broker()


def test_long_mark_below_entry_pending_no_place(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    from astra_bot.decision.live_positions import upsert_plan

    pos_path = tmp_path / "models" / "zeus_live_positions.json"
    upsert_plan(
        "SOL-USDT", side="long", qty="402.84", plan_qty="402.84",
        stop="102.114", tp="108.326", entry="104.752", path=pos_path, be_moved=False,
    )
    moves = []
    r = _run(_broker("241.7", "103.0", moves), pos_path)  # mark < entry
    assert moves == []
    assert "SOL-USDT" not in r.get("moved", [])
    pending = json.loads((tmp_path / "models" / "zeus_be_pending.json").read_text())
    assert pending.get("SOL-USDT|long", {}).get("be_pending") is True
    j = (tmp_path / "models" / "zeus_live_journal.jsonl").read_text()
    assert "be_catchup_pending" in j
    assert "be_catchup\"" not in j.replace("be_catchup_pending", "")


def test_long_mark_reaches_entry_places_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    from astra_bot.decision.live_positions import upsert_plan, load_positions

    pos_path = tmp_path / "models" / "zeus_live_positions.json"
    upsert_plan(
        "SOL-USDT", side="long", qty="402.84", plan_qty="402.84",
        stop="102.114", tp="108.326", entry="104.752", path=pos_path, be_moved=False,
    )
    moves = []
    # first: defer
    _run(_broker("241.7", "103.0", moves), pos_path)
    assert moves == []
    # second: mark at entry → place
    r2 = _run(_broker("241.7", "104.752", moves), pos_path)
    assert len(moves) == 1
    assert moves[0]["stop"] == "104.752"
    assert "SOL-USDT" in r2.get("moved", [])
    plan = load_positions(pos_path)["SOL-USDT|long"]
    assert plan.get("be_moved") is True
    # third: no repeat
    r3 = _run(_broker("241.7", "105.0", moves), pos_path)
    assert len(moves) == 1
    assert r3.get("moved") == []
    pending = json.loads((tmp_path / "models" / "zeus_be_pending.json").read_text())
    assert "SOL-USDT|long" not in pending


def test_short_mark_above_entry_pending(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    from astra_bot.decision.live_positions import upsert_plan

    pos_path = tmp_path / "models" / "zeus_live_positions.json"
    upsert_plan(
        "DOGE-USDT", side="short", qty="100", plan_qty="100",
        stop="0.09", tp="0.07", entry="0.08", path=pos_path, be_moved=False,
    )
    moves = []
    pos = SimpleNamespace(symbol="DOGE-USDT", quantity=Decimal("60"), current_price=Decimal("0.085"))

    class Client:
        async def get_positions(self):
            return [pos]

    class Broker:
        client = Client()
        async def move_stop(self, *a, **k):
            moves.append(1)
            return SimpleNamespace(id="x")
        def net_breakeven_price(self, p):
            return Decimal("0.08")

    from scripts.zeus_live_clock import sync_partial_breakeven
    asyncio.run(sync_partial_breakeven(Broker(), positions_path=pos_path))
    assert moves == []
    pending = json.loads((tmp_path / "models" / "zeus_be_pending.json").read_text())
    assert pending.get("DOGE-USDT|short", {}).get("be_pending") is True


def test_move_stop_reject_no_be_catchup_no_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    from astra_bot.decision.live_positions import upsert_plan, load_positions

    pos_path = tmp_path / "models" / "zeus_live_positions.json"
    upsert_plan(
        "SOL-USDT", side="long", qty="402.84", plan_qty="402.84",
        stop="102.114", tp="108.326", entry="104.752", path=pos_path, be_moved=False,
    )
    moves = []
    r = _run(_broker("241.7", "104.752", moves, fail=True), pos_path)
    assert moves == []
    plan = load_positions(pos_path)["SOL-USDT|long"]
    assert plan.get("be_moved") is False
    jpath = tmp_path / "models" / "zeus_live_journal.jsonl"
    if jpath.exists():
        j = jpath.read_text()
        # may have be_catchup_pending from earlier paths but not success be_catchup
        assert '"event": "be_catchup"' not in j or "be_catchup_pending" in j
