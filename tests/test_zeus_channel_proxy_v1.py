"""Proxy v1 channel: lookback 48, width near, clone, retest shadow."""
from __future__ import annotations

import os
from astra_bot.strategies.zeus_channel_boundary import (
    ZeusChannelBoundaryConfig,
    ZeusChannelBoundaryStrategy,
    _retest_mode,
)
from astra_bot.decision.live_arm_feed import paper_channel_config


def test_paper_config_proxy_v1():
    c = paper_channel_config()
    assert c.lookback == 48
    assert c.near_boundary_width_frac == 0.20
    assert c.use_parallel_clone is True
    assert c.min_stop_pct == 0.01
    assert c.retest_atr_mult == 0.15
    assert c.enable_breakout_hold is True
    assert c.enable_breakout_retest is True


def test_retest_mode_default_shadow(monkeypatch):
    monkeypatch.delenv("ZEUS_RETEST_V1", raising=False)
    assert _retest_mode() == "shadow"
    monkeypatch.setenv("ZEUS_RETEST_V1", "live")
    assert _retest_mode() == "live"
    monkeypatch.setenv("ZEUS_RETEST_V1", "off")
    assert _retest_mode() == "off"


def test_default_config_lookback():
    c = ZeusChannelBoundaryConfig()
    assert c.lookback == 48
    assert c.near_boundary_width_frac == 0.20
