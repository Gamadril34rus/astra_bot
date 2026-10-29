"""Same-side cluster: hard max 2 (owner 29.10 B/C) + soft upnl rule legacy."""
from __future__ import annotations

from astra_bot.decision.live_arm import SAME_SIDE_CLUSTER_MAX, same_side_cluster_blocks


def test_two_same_side_ok() -> None:
    # n=0,1 → entry would be 1st–2nd — under hard max 2
    for n in (0, 1):
        assert same_side_cluster_blocks("short", side_count=n, side_upnl=-100) is False


def test_hard_cap_blocks_third_regardless_of_upnl() -> None:
    # Owner 29.10: n>=2 → block (no 3rd same-side)
    assert SAME_SIDE_CLUSTER_MAX == 2
    assert same_side_cluster_blocks("short", side_count=2, side_upnl=-0.01) is True
    assert same_side_cluster_blocks("short", side_count=2, side_upnl=0.0) is True
    assert same_side_cluster_blocks("short", side_count=2, side_upnl=100.0) is True


def test_hard_cap_blocks_fourth() -> None:
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=50.0) is True
    assert same_side_cluster_blocks("long", side_count=4, side_upnl=0) is True


def test_hard_max_disabled_falls_back_to_soft() -> None:
    # hard_max=0 disables hard cap → soft: 4th only when upnl < 0
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=1.5, hard_max=0) is False
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=-0.01, hard_max=0) is True


def test_unknown_side_not_blocked() -> None:
    assert same_side_cluster_blocks("", side_count=5, side_upnl=-10) is False
