"""F18: ratchet_e trail policy in live_exit_engine."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astra_bot.decision.live_exit_engine import (
    engine_mode,
    evaluate_plan_actions,
    ratchet_e_stop_candidate,
    trail_policy,
)


def test_trail_policy_default_atr(monkeypatch):
    monkeypatch.delenv("ZEUS_LIVE_TRAIL_POLICY", raising=False)
    assert trail_policy() == "atr"
    monkeypatch.setenv("ZEUS_LIVE_TRAIL_POLICY", "ratchet_e")
    assert trail_policy() == "ratchet_e"
    monkeypatch.setenv("ZEUS_LIVE_TRAIL_POLICY", "nope")
    assert trail_policy() == "atr"


def test_ratchet_step_not_down_long():
    entry, init_stop = Decimal("100"), Decimal("90")  # risk 10
    # current stop already at +1.0R = 110
    cur = Decimal("110")
    # MFE 1.5R peak 115 → target +1.0R = 110 → not tighter
    cand = ratchet_e_stop_candidate(
        "long", entry, cur, Decimal("115"), None, initial_stop=init_stop
    )
    assert cand is None  # would not move down / sideways past tighter


def test_ratchet_mfe_1_5_to_stop_1_0():
    entry, init_stop = Decimal("100"), Decimal("90")  # R=10
    cur = Decimal("90")  # still at initial
    # MFE 1.5R → peak 115 → locked 1.0R → stop 110
    cand = ratchet_e_stop_candidate(
        "long", entry, cur, Decimal("115"), None, initial_stop=init_stop
    )
    assert cand is not None
    assert abs(float(cand) - 110.0) < 1e-9


def test_ratchet_be_after_touch_entry():
    entry, init_stop = Decimal("100"), Decimal("90")
    cur = Decimal("90")
    # peak at entry only → BE
    cand = ratchet_e_stop_candidate(
        "long", entry, cur, Decimal("100"), None, initial_stop=init_stop
    )
    assert cand is not None
    assert abs(float(cand) - 100.0) < 1e-9


def test_ratchet_short_mfe_1_5():
    entry, init_stop = Decimal("100"), Decimal("110")  # R=10
    cur = Decimal("110")
    # short peak low 85 = 1.5R → locked 1.0R → stop 90
    cand = ratchet_e_stop_candidate(
        "short", entry, cur, None, Decimal("85"), initial_stop=init_stop
    )
    assert cand is not None
    assert abs(float(cand) - 90.0) < 1e-9


def test_shadow_emits_ratchet_and_trailing_when_possible(monkeypatch):
    """Both actions in one evaluate when candidates exist."""
    monkeypatch.setenv("ZEUS_LIVE_TRAIL_POLICY", "atr")
    # enough bars for ATR trail? may be None if <15 bars — still want ratchet
    entry, stop = Decimal("100"), Decimal("90")
    acts = evaluate_plan_actions(
        symbol="X-USDT",
        direction="long",
        entry=entry,
        stop=stop,
        mark=Decimal("115"),
        highest=Decimal("115"),
        lowest=Decimal("95"),
        closed_bars=[],  # no ATR trail
        opened_at_ms=None,
        regime="",
        pos_regime=None,
        initial_stop=stop,
    )
    names = [a["action"] for a in acts]
    assert "ratchet_e" in names
    rat = next(a for a in acts if a["action"] == "ratchet_e")
    assert abs(float(rat["stop"]) - 110.0) < 1e-9


@pytest.mark.asyncio
async def test_shadow_never_calls_place_or_move(monkeypatch):
    monkeypatch.setenv("ZEUS_LIVE_EXIT_ENGINE", "shadow")
    monkeypatch.setenv("ZEUS_LIVE_TRAIL_POLICY", "ratchet_e")
    assert engine_mode() == "shadow"

    from astra_bot.decision import live_exit_engine as lee

    broker = MagicMock()
    broker.client = MagicMock()
    broker.client.get_candles = AsyncMock(return_value=[])
    broker.move_stop = AsyncMock()
    broker.reduce_position_qty = AsyncMock()
    if hasattr(broker, "place_order"):
        broker.place_order = AsyncMock()

    # minimal plan dict path via load_positions mock
    plans = {
        "X-USDT|long": {
            "symbol": "X-USDT",
            "side": "long",
            "entry": "100",
            "stop": "90",
            "initial_stop": "90",
            "qty": "1",
            "peak_high": "115",
            "ts": 1_700_000_000,
        }
    }
    pos = SimpleNamespace(symbol="X-USDT", side="long", quantity=1, mark_price=115)

    async def fake_positions():
        return [pos]

    broker.client.get_positions = fake_positions

    import astra_bot.decision.live_positions as lp
    import astra_bot.decision.live_arm_wiring as law
    monkeypatch.setattr(lp, "load_positions", lambda path=None: plans)
    monkeypatch.setattr(lee, "migrate_open_stops", lambda p: [])
    monkeypatch.setattr(law, "append_journal", lambda *a, **k: None)

    out = await lee.run_exit_pass(broker)
    assert out["mode"] == "shadow"
    broker.move_stop.assert_not_called()
    if hasattr(broker, "place_order"):
        broker.place_order.assert_not_called()
    broker.reduce_position_qty.assert_not_called()
