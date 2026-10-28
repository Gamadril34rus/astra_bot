"""Owner 28.10: notional position value cap before open_with_protection."""
from __future__ import annotations

from astra_bot.decision.live_arm_wiring import max_position_value_usdt


def test_max_position_value_default(monkeypatch):
    monkeypatch.delenv("ZEUS_MAX_POSITION_VALUE_USDT", raising=False)
    assert max_position_value_usdt() == 50000.0


def test_max_position_value_env(monkeypatch):
    monkeypatch.setenv("ZEUS_MAX_POSITION_VALUE_USDT", "80000")
    assert max_position_value_usdt() == 80000.0


def test_funnel_has_key():
    from astra_bot.decision.live_funnel import SKIP_KEYS
    assert "position_value_too_large" in SKIP_KEYS
