"""PR-A behavior tests."""
from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


def test_only_tighter_stop_never_widens():
    assert only_tighter_stop("long", Decimal("100"), Decimal("101")) == Decimal("101")
    assert only_tighter_stop("long", Decimal("100"), Decimal("99")) is None
    assert only_tighter_stop("short", Decimal("100"), Decimal("99")) == Decimal("99")
    assert only_tighter_stop("short", Decimal("100"), Decimal("101")) is None


def test_post_fill_risk_le_budget_110():
    budget = Decimal("1000")
    # risk within 10%
    risk_ok = actual_risk_usdt(Decimal("100"), Decimal("10"), Decimal("0"))  # 1000
    assert risk_ok <= budget * Decimal("1.10")
    risk_over = actual_risk_usdt(Decimal("120"), Decimal("10"), Decimal("0"))  # 1200
    assert risk_over > budget * Decimal("1.10")


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
    for_lines = [ln for ln in text.splitlines() if "for F in models/zeus_live_commands" in ln]
    assert len(for_lines) == 1
    for_line = for_lines[0].strip()
    assert for_line.endswith("; do")
    tmp = Path("/tmp/wf_for_test.sh")
    tmp.write_text(for_line + "\n  echo ok\ndone\n")
    r = subprocess.run(["bash", "-n", str(tmp)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_margin_gates_invoked_in_run_arm_entries_source():
    """Behavior: wiring source must call check_margin_gates (not dead helper)."""
    src = Path("/home/workdir/artifacts/pr_work/astra_bot/decision/live_arm_wiring.py").read_text()
    assert "check_margin_gates(" in src
    assert "actual_risk_usdt(" in src
    assert "slip_in_r(" in src
    assert "post_fill_risk" in src or "post-fill" in src.lower() or "post_fill" in src


@pytest.mark.asyncio
async def test_price_breakeven_once_at_08r_without_tp_fill(tmp_path, monkeypatch):
    from scripts import zeus_live_clock as zc
    from astra_bot.decision import live_positions as lp

    plan = {
        "BTC-USDT|long": {
            "symbol": "BTC-USDT",
            "side": "long",
            "entry": "100",
            "stop": "90",
            "qty": "1",
            "be_moved": False,
            "tp1": "115",
        }
    }
    monkeypatch.setattr(lp, "load_positions", lambda path=None: plan)

    def _up(**kw):
        plan["BTC-USDT|long"]["be_moved"] = True
        plan["BTC-USDT|long"]["stop"] = str(kw.get("stop"))

    monkeypatch.setattr(lp, "upsert_plan", _up)
    monkeypatch.setattr("astra_bot.decision.live_arm_wiring.append_journal", lambda *a, **k: None)
    monkeypatch.setattr("astra_bot.decision.live_stop_telemetry.record_stop_change", lambda **k: None)

    class P:
        symbol = "BTC-USDT"
        quantity = Decimal("1")
        mark_price = Decimal("108")  # +0.8R

    broker = MagicMock()
    broker.client.get_positions = AsyncMock(return_value=[P()])
    broker.move_stop = AsyncMock()
    # no net_breakeven_price → loud gross fallback
    if hasattr(broker, "net_breakeven_price"):
        del broker.net_breakeven_price

    out = await zc.sync_partial_breakeven(broker, positions_path=tmp_path / "p.json")
    assert "BTC-USDT" in out["moved"]
    assert broker.move_stop.await_count == 1
    out2 = await zc.sync_partial_breakeven(broker, positions_path=tmp_path / "p.json")
    assert out2["moved"] == []
    assert broker.move_stop.await_count == 1
