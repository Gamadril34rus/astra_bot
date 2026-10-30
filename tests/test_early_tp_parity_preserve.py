"""Early TP 0.6R must not be widened by tp_parity_v1 (owner 30.10)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from astra_bot.decision.live_positions import load_positions, save_positions
from scripts.zeus_live_clock import apply_tp_parity_v1


def test_tp_parity_preserves_early_sub_2r(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    plans = {
        "ETH-USDT|short": {
            "symbol": "ETH-USDT",
            "side": "short",
            "entry": "3800",
            "stop": "3860",
            "initial_stop": "3860",
            "tp": "3764",  # 0.6R
            "qty": "1.0",
            "tp_frac": 0.3,
        }
    }
    save_positions(plans)
    journals: list = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    broker = MagicMock()
    pos = SimpleNamespace(symbol="ETH-USDT", quantity=Decimal("1.0"))
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.client.place_order = AsyncMock()
    broker.client.get_open_orders = AsyncMock(return_value=[])

    out = asyncio.run(apply_tp_parity_v1(broker, send_tg=None))
    assert out.get("replaced", 0) == 0
    broker.client.place_order.assert_not_called()
    saved = load_positions()
    plan = saved["ETH-USDT|short"]
    assert plan.get("tp_parity_v1") is True
    assert Decimal(str(plan["tp"])) == Decimal("3764")
    assert plan.get("early_tp1") is True
    assert any(j.get("event") == "tp_parity_early_preserved" for j in journals)


def test_tp_parity_clamps_overshoot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    plans = {
        "BTC-USDT|long": {
            "symbol": "BTC-USDT",
            "side": "long",
            "entry": "100",
            "stop": "90",
            "initial_stop": "90",
            "tp": "140",  # 4R
            "qty": "1.0",
            "tp_frac": 0.3,
        }
    }
    save_positions(plans)
    monkeypatch.setattr("astra_bot.decision.live_arm_wiring.append_journal", lambda row: None)
    broker = MagicMock()
    pos = SimpleNamespace(symbol="BTC-USDT", quantity=Decimal("1.0"))
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.client.place_order = AsyncMock()
    broker.client.get_open_orders = AsyncMock(return_value=[])
    broker.client.cancel_order = AsyncMock()

    out = asyncio.run(apply_tp_parity_v1(broker, send_tg=None))
    assert out.get("replaced", 0) == 1
    broker.client.place_order.assert_called()
    sp = Decimal(str(broker.client.place_order.call_args.kwargs["stop_price"]))
    assert Decimal("120") <= sp <= Decimal("123")
