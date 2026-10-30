"""Same-side cluster: hard max 3 + soft upnl rule legacy."""
from __future__ import annotations

from astra_bot.decision.live_arm import SAME_SIDE_CLUSTER_MAX, same_side_cluster_blocks


def test_under_cap_ok() -> None:
    # n=0,1,2 → entry would be 1st–3rd — under hard max 3
    for n in (0, 1, 2):
        assert same_side_cluster_blocks("short", side_count=n, side_upnl=-100) is False


def test_hard_cap_blocks_fourth_regardless_of_upnl() -> None:
    # n>=3 → block (no 4th same-side)
    assert SAME_SIDE_CLUSTER_MAX == 3
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=-0.01) is True
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=0.0) is True
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=100.0) is True


def test_hard_cap_blocks_above() -> None:
    assert same_side_cluster_blocks("short", side_count=4, side_upnl=50.0) is True
    assert same_side_cluster_blocks("long", side_count=5, side_upnl=0) is True


def test_hard_max_disabled_falls_back_to_soft() -> None:
    # hard_max=0 disables hard cap → soft: 4th only when upnl < 0
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=1.5, hard_max=0) is False
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=-0.01, hard_max=0) is True


def test_unknown_side_not_blocked() -> None:
    assert same_side_cluster_blocks("", side_count=5, side_upnl=-10) is False
