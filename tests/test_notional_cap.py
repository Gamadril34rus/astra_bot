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


def test_clip_qty_math():
    """Clip: notional 110k @ entry 131 → qty = 50000/131."""
    from decimal import Decimal
    from astra_bot.decision.live_arm import quantize_qty
    entry = 131.0
    cap = 50000.0
    qty = quantize_qty(Decimal(str(cap / entry)), Decimal("0.01"))
    assert float(qty) * entry <= cap + 1.0
    assert float(qty) > 0
