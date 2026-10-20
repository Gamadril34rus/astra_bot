"""PR-A behavior tests."""
from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path

from astra_bot.decision.live_margin_budget import (
    check_margin_gates,
    only_tighter_stop,
    actual_risk_usdt,
    slip_in_r,
)
from astra_bot.decision.live_arm import SignalCandidate, rank_candidates
from astra_bot.decision.live_stop_telemetry import record_stop_change
from astra_bot.decision.live_funnel import identity_ok, empty_skipped, bump


def test_m2_m3_m4_and_equity_unknown():
    eq = Decimal("100000")
    assert check_margin_gates(equity=eq, free_margin=Decimal("50000"), used_margin=Decimal("50000"), new_margin=Decimal("30000")) == "margin_M2"
    assert check_margin_gates(equity=eq, free_margin=Decimal("50000"), used_margin=Decimal("50000"), new_margin=Decimal("10000")) == "margin_M3"
    assert check_margin_gates(equity=eq, free_margin=Decimal("30000"), used_margin=Decimal("70000"), new_margin=Decimal("10000")) == "margin_M4"
    assert check_margin_gates(equity=Decimal("0"), free_margin=None, used_margin=None, new_margin=Decimal("1")) == "equity_unknown"
    assert check_margin_gates(equity=eq, free_margin=Decimal("50000"), used_margin=Decimal("40000"), new_margin=Decimal("10000")) is None


def test_only_tighter_stop():
    assert only_tighter_stop("long", Decimal("100"), Decimal("101")) == Decimal("101")
    assert only_tighter_stop("long", Decimal("100"), Decimal("99")) is None
    assert only_tighter_stop("short", Decimal("100"), Decimal("99")) == Decimal("99")
    assert only_tighter_stop("short", Decimal("100"), Decimal("101")) is None


def test_slip_and_risk():
    assert abs(slip_in_r(Decimal("100"), Decimal("100.2"), Decimal("99")) - 0.2) < 1e-9
    assert actual_risk_usdt(Decimal("10"), Decimal("100"), Decimal("99")) == Decimal("10")


def test_rank_prior_r():
    a = SignalCandidate("APT-USDT", "long", Decimal("1"), Decimal("0.9"), prior_r=4.0, confidence=0.5)
    b = SignalCandidate("BTC-USDT", "long", Decimal("1"), Decimal("0.9"), prior_r=1.2, confidence=0.9)
    assert rank_candidates([b, a])[0].symbol == "APT-USDT"


def test_identity_funnel():
    sk = empty_skipped()
    bump(sk, "margin_M2")
    bump(sk, "other")
    assert identity_ok(3, [{"a": 1}], sk) is True


def test_stop_telemetry(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    record_stop_change(symbol="ETH-USDT", stop_price="2415.67", source="migrate", reason="owner", path=p)
    assert "2415.67" in p.read_text()


def test_workflow_save_bash_n():
    yml = Path("/home/workdir/artifacts/pr_work/.github/workflows/zeus-live-clock.yml")
    text = yml.read_text()
    assert "models/zeus_live_stop_log.jsonl" in text
    for_lines = [ln for ln in text.splitlines() if "for F in models/zeus_live_commands" in ln]
    assert len(for_lines) == 1
    for_line = for_lines[0].strip()
    assert for_line.endswith("; do")
    tmp = Path("/tmp/wf_for_test.sh")
    tmp.write_text(for_line + "\n  echo ok\ndone\n")
    r = subprocess.run(["bash", "-n", str(tmp)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
