"""F19: exchange-validity defer, ratchet step-lock on be_moved, regime writer."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astra_bot.decision.live_exit_engine import (
    evaluate_plan_actions,
    ratchet_e_stop_candidate,
    run_exit_pass,
    stop_exchange_valid,
)


def test_stop_exchange_valid_long_short():
    assert stop_exchange_valid("long", Decimal("100"), Decimal("101")) is True
    assert stop_exchange_valid("long", Decimal("101"), Decimal("100")) is False
    assert stop_exchange_valid("short", Decimal("101"), Decimal("100")) is True
    assert stop_exchange_valid("short", Decimal("100"), Decimal("101")) is False
    assert stop_exchange_valid("short", Decimal("100"), None) is True


def test_ratchet_be_moved_step_lock_short():
    entry = Decimal("100")
    initial = Decimal("110")
    low = Decimal("86")  # mfe 1.4R
    cur = Decimal("100")
    cand = ratchet_e_stop_candidate(
        "short", entry, cur, None, low,
        initial_stop=initial, mark=Decimal("86"), be_moved=True,
    )
    assert cand is not None
    assert cand == Decimal("90")


def test_ratchet_be_moved_skips_be_only():
    entry = Decimal("100")
    initial = Decimal("110")
    low = Decimal("95")
    cand = ratchet_e_stop_candidate(
        "short", entry, Decimal("100"), None, low,
        initial_stop=initial, mark=low, be_moved=True,
    )
    assert cand is None


def test_ratchet_not_be_moved_emits_be():
    entry = Decimal("100")
    initial = Decimal("110")
    low = Decimal("95")
    cand = ratchet_e_stop_candidate(
        "short", entry, Decimal("105"), None, low,
        initial_stop=initial, mark=low, be_moved=False,
    )
    assert cand == entry


@pytest.mark.asyncio
async def test_on_mode_defers_invalid_short_stop(tmp_path):
    from astra_bot.decision import live_positions as lp

    plan = {
        "symbol": "SOL-USDT",
        "side": "short",
        "entry": "100.705",
        "stop": "105.13054096097555",
        "initial_stop": "105.13054096097555",
        "qty": "106.42",
        "be_moved": False,
        "peak_low": "100.0",
        "ts": 1792843200,
    }
    pos_path = tmp_path / "positions.json"
    lp.save_positions({"SOL-USDT|short": plan}, pos_path)

    pos = MagicMock()
    pos.symbol = "SOL-USDT"
    pos.quantity = Decimal("106.42")
    pos.mark_price = Decimal("102.667")

    broker = MagicMock()
    broker.client = MagicMock()
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.move_stop = AsyncMock()

    journal = []

    def _append(row):
        journal.append(row)

    with patch.dict("os.environ", {"ZEUS_LIVE_EXIT_ENGINE": "on", "ZEUS_LIVE_TRAIL_POLICY": "ratchet_e"}):
        with patch("astra_bot.decision.live_arm_wiring.append_journal", side_effect=_append):
            with patch(
                "astra_bot.decision.live_exit_engine._closed_bars_for_plan",
                new=AsyncMock(return_value=[]),
            ):
                with patch(
                    "astra_bot.decision.live_exit_engine.classify_current_regime",
                    return_value="",
                ):
                    out = await run_exit_pass(broker, positions_path=pos_path)

    assert out.get("mode") == "on"
    broker.move_stop.assert_not_called()
    defer = [j for j in journal if j.get("action") == "defer_ratchet"]
    assert defer, f"expected defer_ratchet in {journal}"
    assert defer[0]["symbol"] == "SOL-USDT"
    assert defer[0]["reason"] == "exchange_invalid"


def test_evaluate_be_moved_emits_step_not_be():
    entry = Decimal("100")
    stop = Decimal("100")
    initial = Decimal("110")
    low = Decimal("85")  # mfe 1.5R
    acts = evaluate_plan_actions(
        symbol="X",
        direction="short",
        entry=entry,
        stop=stop,
        mark=low,
        highest=None,
        lowest=low,
        closed_bars=[],
        opened_at_ms=None,
        regime="",
        pos_regime=None,
        initial_stop=initial,
        be_moved=True,
    )
    rats = [a for a in acts if a.get("action") == "ratchet_e"]
    assert len(rats) == 1
    assert Decimal(rats[0]["stop"]) == Decimal("90")
