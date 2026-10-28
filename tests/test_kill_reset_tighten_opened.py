"""Owner 28.10: pf window reset, only-tighten plan stop, opened_at_ms."""
from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from astra_bot.decision.live_exit_engine import _is_only_tighten
from astra_bot.decision.live_positions import load_positions, upsert_plan


def test_only_tighten_short_and_long():
    assert _is_only_tighten("short", Decimal("100"), Decimal("101")) is True
    assert _is_only_tighten("short", Decimal("101"), Decimal("100")) is False
    assert _is_only_tighten("long", Decimal("101"), Decimal("100")) is True
    assert _is_only_tighten("long", Decimal("100"), Decimal("101")) is False
    assert _is_only_tighten("short", Decimal("100"), Decimal("100")) is True


def test_opened_at_ms_preserved_on_upsert(tmp_path):
    path = tmp_path / "pos.json"
    upsert_plan("ETH-USDT", side="short", qty=1, stop=100, tp=90, entry=95, path=path)
    pos = load_positions(path)
    key = "ETH-USDT|short"
    assert key in pos
    opened = pos[key]["opened_at_ms"]
    assert isinstance(opened, int) and opened > 0
    time.sleep(0.01)
    # BE-style upsert must not rewrite opened_at_ms
    upsert_plan(
        "ETH-USDT", side="short", qty=1, stop=95, tp=90, entry=95,
        be_moved=True, path=path,
    )
    pos2 = load_positions(path)
    assert pos2[key]["opened_at_ms"] == opened
    assert pos2[key]["be_moved"] is True
    assert str(pos2[key]["stop"]) == "95"


@pytest.mark.asyncio
async def test_run_exit_pass_updates_plan_stop_after_move(tmp_path, monkeypatch):
    """After successful move_stop, plan stop must match new_stop (only-tighten sync)."""
    from astra_bot.decision import live_exit_engine as eng

    monkeypatch.setenv("ZEUS_LIVE_EXIT_ENGINE", "on")
    monkeypatch.setenv("ZEUS_LIVE_TRAIL_POLICY", "ratchet_e")

    path = tmp_path / "pos.json"
    upsert_plan(
        "ETH-USDT", side="short", qty="10", stop="110", tp="90", entry="100",
        path=path, be_moved=True, initial_stop="110",
    )
    pos = load_positions(path)
    plan = pos["ETH-USDT|short"]
    plan["peak_low"] = "95"  # MFE positive for short
    plan["peak_high"] = "100"
    from astra_bot.decision.live_positions import save_positions
    save_positions(pos, path)

    class P:
        symbol = "ETH-USDT"
        quantity = Decimal("10")
        current_price = Decimal("95")
        mark_price = Decimal("95")

    class O:
        type = "STOP_MARKET"
        stop_price = Decimal("110")
        position_side = "short"

    broker = MagicMock()
    broker.client.get_positions = AsyncMock(return_value=[P()])
    broker.client.get_open_orders = AsyncMock(return_value=[O()])
    broker.client.get_candles = AsyncMock(return_value=[])
    broker.move_stop = AsyncMock(return_value=True)
    broker._halted = False

    # Force evaluate to return a ratchet tighten
    def fake_eval(**kwargs):
        return [{"action": "ratchet_e", "stop": "105", "stop_before": "110"}]

    monkeypatch.setattr(eng, "evaluate_plan_actions", lambda **kw: fake_eval(**kw))
    monkeypatch.setattr(eng, "_closed_bars_for_plan", AsyncMock(return_value=[]))
    monkeypatch.setattr(eng, "migrate_open_stops", lambda plans: [])

    out = await eng.run_exit_pass(broker, positions_path=path)
    assert out.get("mode") == "on"
    broker.move_stop.assert_awaited()
    pos2 = load_positions(path)
    # plan stop must be updated after move
    assert Decimal(str(pos2["ETH-USDT|short"]["stop"])) == Decimal("105")
