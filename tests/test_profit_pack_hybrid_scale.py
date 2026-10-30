"""Profit pack 30.10: hybrid ratchet after BE + early scale-out."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from astra_bot.decision.live_exit_engine import evaluate_plan_actions
from astra_bot.decision.live_positions import load_positions, save_positions


def test_hybrid_ratchet_emitted_when_be_moved():
    """evaluate still emits ratchet_e when be_moved; apply layer uses hybrid."""
    # long entry 100 stop 90, peak 115 → mfe 1.5R → lock 1.0R → stop 110
    acts = evaluate_plan_actions(
        symbol="BTC-USDT",
        direction="long",
        entry=Decimal("100"),
        stop=Decimal("100"),  # already BE
        mark=Decimal("115"),
        highest=Decimal("115"),
        lowest=Decimal("99"),
        closed_bars=[],
        opened_at_ms=None,
        regime="RANGE",
        pos_regime="RANGE",
        leverage=5,
        initial_stop=Decimal("90"),
        be_moved=True,
    )
    names = [a.get("action") for a in acts]
    assert "ratchet_e" in names
    ratchet = next(a for a in acts if a.get("action") == "ratchet_e")
    assert Decimal(str(ratchet["stop"])) >= Decimal("109")


def test_scale_out_when_mfe_past_early_and_far_tp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    monkeypatch.setenv("ZEUS_TP1_RR", "0.6")
    save_positions(
        {
            "SOL-USDT|short": {
                "symbol": "SOL-USDT",
                "side": "short",
                "entry": "100",
                "stop": "100",
                "initial_stop": "110",
                "tp": "80",  # 2R far
                "qty": "10",
                "tp_frac": 0.3,
                "be_moved": True,
                "tp_parity_v1": True,
            }
        }
    )
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    broker = MagicMock()
    pos = SimpleNamespace(
        symbol="SOL-USDT",
        quantity=Decimal("10"),
        mark_price=Decimal("93"),  # mfe 0.7R
    )
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.client.place_order = AsyncMock()
    broker.client.get_open_orders = AsyncMock(return_value=[])
    broker.client.cancel_order = AsyncMock()

    from scripts.zeus_live_clock import scale_out_early_if_due

    out = asyncio.run(scale_out_early_if_due(broker))
    assert out.get("scaled", 0) == 1
    calls = broker.client.place_order.call_args_list
    types = [c.kwargs.get("order_type") for c in calls]
    assert "MARKET" in types
    assert "TAKE_PROFIT_MARKET" in types
    mkt = next(c for c in calls if c.kwargs.get("order_type") == "MARKET")
    assert Decimal(str(mkt.kwargs["quantity"])) == Decimal("3.00000000")
    plan = load_positions()["SOL-USDT|short"]
    assert plan.get("early_scaled") is True
    assert any(j.get("event") == "early_scale_out" for j in journals)
