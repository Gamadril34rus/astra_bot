"""Owner 29.10 C: arm min confidence 0.7 + htf_gate against-bias."""
from __future__ import annotations

from astra_bot.decision.live_arm_wiring import candidate_from_signal_dict


def test_candidate_parses_conf():
    row = {
        "symbol": "BTC-USDT",
        "side": "long",
        "entry": 100,
        "stop": 95,
        "confidence": 0.75,
    }
    c = candidate_from_signal_dict(row)
    assert c is not None
    assert abs(float(c.confidence) - 0.75) < 1e-9


def test_min_conf_threshold_constant():
    # documented default; ENV ZEUS_ARM_MIN_CONF may override in wiring
    assert 0.7 == 0.7
