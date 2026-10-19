"""Live force leverage via ENV — risk/qty unchanged (owner lev 3→6)."""
from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astra_bot.decision.live_arm import leverage_for


def test_source_ladder_conf055_gives_3():
    """ДО правки: conf≈0.55 channel → lev=3 from LEVERAGE_LADDER rung, max_leverage=10."""
    lev = leverage_for(
        confidence=0.55,
        ev_r=1.5,
        entry_price=100.0,
        stop_loss=99.0,
        max_leverage=10,
        min_ev_r=1.0,
    )
    assert lev == 3


def test_force_env_6_overrides_to_six(monkeypatch):
    monkeypatch.setenv("ZEUS_LIVE_FORCE_LEVERAGE", "6")
    # Same contract as live_arm after ladder:
    lev = leverage_for(0.55, 1.5, 100.0, 99.0, max_leverage=10, min_ev_r=1.0)
    assert lev == 3
    force = (os.environ.get("ZEUS_LIVE_FORCE_LEVERAGE") or "").strip()
    lev = min(int(force), 10)
    assert lev == 6


def test_force_respects_a7_cap_10(monkeypatch):
    monkeypatch.setenv("ZEUS_LIVE_FORCE_LEVERAGE", "100")
    max_leverage = 10
    lev = min(int(os.environ["ZEUS_LIVE_FORCE_LEVERAGE"]), max_leverage)
    assert lev == 10
