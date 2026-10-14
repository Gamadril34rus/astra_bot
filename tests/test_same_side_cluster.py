"""Same-side cluster: 4th/5th only if side upnl >= 0 (owner 14.10)."""
from __future__ import annotations

from astra_bot.decision.live_arm import same_side_cluster_blocks


def test_three_shorts_always_ok() -> None:
    # n=0,1,2 → entry would be 1st–3rd
    for n in (0, 1, 2):
        assert same_side_cluster_blocks("short", side_count=n, side_upnl=-100) is False


def test_fourth_short_blocked_when_net_negative() -> None:
    # 3 open → 4th blocked if upnl < 0
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=-0.01) is True


def test_fourth_short_allowed_when_net_nonneg() -> None:
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=0.0) is False
    assert same_side_cluster_blocks("short", side_count=3, side_upnl=1.5) is False


def test_fifth_blocked_when_net_negative() -> None:
    # 4 open → 5th blocked if upnl < 0
    assert same_side_cluster_blocks("short", side_count=4, side_upnl=-1.0) is True


def test_fifth_allowed_when_net_zero() -> None:
    assert same_side_cluster_blocks("long", side_count=4, side_upnl=0) is False


def test_unknown_side_not_blocked() -> None:
    assert same_side_cluster_blocks("", side_count=5, side_upnl=-10) is False
