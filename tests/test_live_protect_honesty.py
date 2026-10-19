"""PR-A: margin gates, price BE trigger, rank prior_r, stop telemetry."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from astra_bot.decision.live_margin_budget import (
    check_margin_gates,
    initial_margin_usdt,
    actual_risk_usdt,
    slip_in_r,
)
from astra_bot.decision.live_arm import SignalCandidate, rank_candidates
from astra_bot.decision.live_stop_telemetry import record_stop_change


def test_m2_rejects_fat_position():
    eq = Decimal("100000")
    new_m = Decimal("30000")  # 30% > 25%
    assert check_margin_gates(equity=eq, free_margin=Decimal("50000"), used_margin=Decimal("50000"), new_margin=new_m) == "margin_M2"


def test_m3_rejects_book_over_55():
    eq = Decimal("100000")
    # used 50k + new 10k = 60% > 55%
    assert check_margin_gates(
        equity=eq, free_margin=Decimal("50000"), used_margin=Decimal("50000"), new_margin=Decimal("10000")
    ) == "margin_M3"


def test_m4_requires_free_after():
    eq = Decimal("100000")
    # free 30k - new 10k = 20k < 25k
    assert check_margin_gates(
        equity=eq, free_margin=Decimal("30000"), used_margin=Decimal("70000"), new_margin=Decimal("10000")
    ) == "margin_M4"


def test_ok_within_budget():
    eq = Decimal("100000")
    assert check_margin_gates(
        equity=eq, free_margin=Decimal("50000"), used_margin=Decimal("40000"), new_margin=Decimal("10000")
    ) is None


def test_initial_margin_formula():
    # notional 6000 / lev 6 = 1000
    assert initial_margin_usdt(Decimal("100"), Decimal("60"), 6) == Decimal("1000")


def test_actual_risk_and_slip():
    risk = actual_risk_usdt(Decimal("10"), Decimal("100"), Decimal("99"))
    assert risk == Decimal("10")
    s = slip_in_r(Decimal("100"), Decimal("100.2"), Decimal("99"))
    assert abs(s - 0.2) < 1e-9


def test_rank_prior_r():
    a = SignalCandidate("APT-USDT", "long", Decimal("1"), Decimal("0.9"), prior_r=4.0, confidence=0.5)
    b = SignalCandidate("BTC-USDT", "long", Decimal("1"), Decimal("0.9"), prior_r=1.2, confidence=0.9)
    c = SignalCandidate("ETH-USDT", "long", Decimal("1"), Decimal("0.9"), prior_r=None, confidence=0.99)
    ranked = rank_candidates([b, c, a])
    assert ranked[0].symbol == "APT-USDT"
    assert ranked[1].symbol == "BTC-USDT"
    assert ranked[2].symbol == "ETH-USDT"


def test_stop_telemetry_writes(tmp_path: Path):
    p = tmp_path / "stop.jsonl"
    record_stop_change(symbol="BTC-USDT", stop_price="100", source="test", reason="unit", path=p)
    assert p.exists()
    assert "BTC-USDT" in p.read_text()
