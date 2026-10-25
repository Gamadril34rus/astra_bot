"""F19: exchange-validity defer, ratchet step-lock on be_moved."""
from __future__ import annotations

from decimal import Decimal

from astra_bot.decision.live_exit_engine import (
    evaluate_plan_actions,
    ratchet_e_stop_candidate,
    stop_exchange_valid,
)


def test_stop_exchange_valid_long_short():
    assert stop_exchange_valid("long", Decimal("100"), Decimal("101")) is True
    assert stop_exchange_valid("long", Decimal("101"), Decimal("100")) is False
    assert stop_exchange_valid("short", Decimal("101"), Decimal("100")) is True
    assert stop_exchange_valid("short", Decimal("100"), Decimal("101")) is False
    assert stop_exchange_valid("short", Decimal("100"), None) is True


def test_sol_case_be_invalid_vs_mark():
    """SOL short: BE cand 100.705, mark 102.667 → exchange-invalid."""
    assert stop_exchange_valid("short", Decimal("100.705"), Decimal("102.667")) is False


def test_ratchet_be_moved_step_lock_short():
    entry = Decimal("100")
    initial = Decimal("110")
    low = Decimal("86")  # mfe 1.4R
    cand = ratchet_e_stop_candidate(
        "short", entry, Decimal("100"), None, low,
        initial_stop=initial, mark=Decimal("86"), be_moved=True,
    )
    assert cand == Decimal("90")


def test_ratchet_be_moved_skips_be_only():
    cand = ratchet_e_stop_candidate(
        "short", Decimal("100"), Decimal("100"), None, Decimal("95"),
        initial_stop=Decimal("110"), mark=Decimal("95"), be_moved=True,
    )
    assert cand is None


def test_ratchet_not_be_moved_emits_be():
    cand = ratchet_e_stop_candidate(
        "short", Decimal("100"), Decimal("105"), None, Decimal("95"),
        initial_stop=Decimal("110"), mark=Decimal("95"), be_moved=False,
    )
    assert cand == Decimal("100")


def test_evaluate_be_moved_emits_step_not_be():
    acts = evaluate_plan_actions(
        symbol="X",
        direction="short",
        entry=Decimal("100"),
        stop=Decimal("100"),
        mark=Decimal("85"),
        highest=None,
        lowest=Decimal("85"),
        closed_bars=[],
        opened_at_ms=None,
        regime="",
        pos_regime=None,
        initial_stop=Decimal("110"),
        be_moved=True,
    )
    rats = [a for a in acts if a.get("action") == "ratchet_e"]
    assert len(rats) == 1
    assert Decimal(rats[0]["stop"]) == Decimal("90")
