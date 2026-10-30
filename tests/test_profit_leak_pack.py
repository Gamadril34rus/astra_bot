"""Profit-leak pack: ratchet 0.75 after BE; FEED expands; PF partial."""
from __future__ import annotations

from decimal import Decimal

from astra_bot.decision.live_exit_engine import ratchet_e_stop_candidate
from astra_bot.decision.live_arm_feed import FEED_SYMBOLS


def test_feed_includes_owner_expand():
    for s in ("INJ-USDT", "AVAX-USDT", "HYPE-USDT", "ZEC-USDT", "TOTAL-USDT", "NEAR-USDT"):
        assert s in FEED_SYMBOLS
    assert "BTC-USDT" in FEED_SYMBOLS


def test_ratchet_after_be_locks_at_075():
    """After BE, MFE 0.8R → stop at entry - 0.25R (short)."""
    entry = Decimal("100")
    init = Decimal("110")  # short risk 10
    # lowest gives mfe = (100-92)/10 = 0.8
    cand = ratchet_e_stop_candidate(
        "short",
        entry,
        entry,  # current stop at BE
        highest_price=None,
        lowest_price=Decimal("92"),
        initial_stop=init,
        mark=Decimal("92"),
        be_moved=True,
    )
    assert cand is not None
    assert cand == Decimal("97.5")


def test_ratchet_no_lock_before_075_after_be():
    entry = Decimal("100")
    init = Decimal("110")
    # mfe 0.6 < 0.75
    cand = ratchet_e_stop_candidate(
        "short",
        entry,
        entry,
        highest_price=None,
        lowest_price=Decimal("94"),
        initial_stop=init,
        mark=Decimal("94"),
        be_moved=True,
    )
    assert cand is None


def test_ratchet_classic_1r_still_works():
    entry = Decimal("100")
    init = Decimal("110")
    # mfe 1.2 → locked floor(1.2/0.5)*0.5-0.5 = 1.0-0.5=0.5 → stop 95
    cand = ratchet_e_stop_candidate(
        "short",
        entry,
        entry,
        highest_price=None,
        lowest_price=Decimal("88"),
        initial_stop=init,
        mark=Decimal("88"),
        be_moved=True,
    )
    assert cand == Decimal("95")
