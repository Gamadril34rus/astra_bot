
"""Heal pack: ratchet 0.75, min stop gate constants, cluster 3/4."""
from __future__ import annotations
from decimal import Decimal

from astra_bot.decision.live_exit_engine import ratchet_e_stop_candidate
from astra_bot.decision.live_arm import SAME_SIDE_CLUSTER_MAX, SAME_SIDE_SOFT_CAP
from astra_bot.decision.live_arm_feed import FEED_SYMBOLS


def test_cluster_owner_expand():
    assert SAME_SIDE_SOFT_CAP == 3
    assert SAME_SIDE_CLUSTER_MAX == 4


def test_feed_has_expand_coins():
    assert len(FEED_SYMBOLS) == 5
    assert "INJ-USDT" in FEED_SYMBOLS
    assert "HYPE-USDT" in FEED_SYMBOLS
    assert "BTC-USDT" in FEED_SYMBOLS
    assert "AVAX-USDT" not in FEED_SYMBOLS


def test_heal_ratchet_levels_match_engine():
    entry, init = Decimal("97.114"), Decimal("100.85940979512189")
    stop = entry  # BE
    lo = Decimal("93.626")
    cand = ratchet_e_stop_candidate(
        "short", entry, stop, None, lo, initial_stop=init, mark=lo, be_moved=True
    )
    assert cand is not None
    assert cand < stop
    # ~0.25R lock
    risk = abs(entry - init)
    assert abs(cand - (entry - risk * Decimal("0.25"))) < Decimal("0.01")
