"""Early TP repair: level already through mark → market leg, no spam (SOL 30.10)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from astra_bot.decision.live_positions import load_positions, save_positions
from scripts.zeus_live_clock import repair_early_tp_after_parity


def test_repair_past_level_short_markets_and_flags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    # entry 100, initial_stop 110, risk 10 → early 0.6R = 94
    # mark 93 already through → short TP must be < mark, 94 invalid
    save_positions(
        {
            "SOL-USDT|short": {
                "symbol": "SOL-USDT",
                "side": "short",
                "entry": "100",
                "stop": "110",
                "initial_stop": "110",
                "tp": "80",  # 2R parity-clamped
                "qty": "10",
                "tp_frac": 0.3,
                "tp_parity_v1": True,
            }
        }
    )
    journals: list = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    broker = MagicMock()
    pos = SimpleNamespace(
        symbol="SOL-USDT", quantity=Decimal("10"), mark_price=Decimal("93")
    )
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.client.get_open_orders = AsyncMock(return_value=[])
    broker.client.place_order = AsyncMock()
    broker.client.cancel_order = AsyncMock()
    broker.move_stop = AsyncMock()

    out = asyncio.run(repair_early_tp_after_parity(broker, send_tg=None))
    assert out.get("past_level", 0) == 1
    # market order, not TAKE_PROFIT
    assert broker.client.place_order.called
    kw = broker.client.place_order.call_args.kwargs
    assert kw["order_type"] == "MARKET"
    saved = load_positions()
    plan = saved["SOL-USDT|short"]
    assert plan.get("early_tp_repair_v1") is True
    assert plan.get("early_tp1") is True
    assert any(j.get("event") == "early_tp_repair_past_level" for j in journals)

    # second call must skip (no spam)
    out2 = asyncio.run(repair_early_tp_after_parity(broker, send_tg=None))
    assert out2.get("skipped", 0) >= 1
    assert out2.get("past_level", 0) == 0
    assert out2.get("failed", 0) == 0


def test_repair_valid_level_places_tp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    # short early 94, mark 96 → TP still valid
    save_positions(
        {
            "ETH-USDT|short": {
                "symbol": "ETH-USDT",
                "side": "short",
                "entry": "100",
                "stop": "110",
                "initial_stop": "110",
                "tp": "80",
                "qty": "5",
                "tp_frac": 0.3,
                "tp_parity_v1": True,
            }
        }
    )
    monkeypatch.setattr("astra_bot.decision.live_arm_wiring.append_journal", lambda row: None)
    broker = MagicMock()
    pos = SimpleNamespace(
        symbol="ETH-USDT", quantity=Decimal("5"), mark_price=Decimal("96")
    )
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.client.get_open_orders = AsyncMock(return_value=[])
    broker.client.place_order = AsyncMock()
    broker.client.cancel_order = AsyncMock()

    out = asyncio.run(repair_early_tp_after_parity(broker, send_tg=None))
    assert out.get("repaired", 0) == 1
    kw = broker.client.place_order.call_args.kwargs
    assert kw["order_type"] == "TAKE_PROFIT_MARKET"
    assert Decimal(str(kw["stop_price"])) == Decimal("94")
