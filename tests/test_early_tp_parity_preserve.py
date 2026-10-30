"""Early TP 0.6R must not be widened by tp_parity_v1 (owner 30.10)."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts import zeus_live_clock as clock


@pytest.mark.asyncio
async def test_tp_parity_preserves_early_sub_2r(tmp_path, monkeypatch):
    plans = {
        "ETH-USDT|short": {
            "symbol": "ETH-USDT",
            "side": "short",
            "entry": "3800",
            "stop": "3860",  # risk 60
            "initial_stop": "3860",
            "tp": "3764",  # 0.6R = 36 below entry
            "qty": "1.0",
            "tp_frac": 0.3,
        }
    }
    pos_path = tmp_path / "positions.json"
    monkeypatch.setattr(clock, "DEFAULT_PATH", pos_path)  # may not exist on clock
    from astra_bot.decision import live_positions as lp

    monkeypatch.setattr(lp, "DEFAULT_PATH", pos_path)
    lp.save_positions(plans, path=pos_path)

    broker = MagicMock()
    pos = MagicMock()
    pos.symbol = "ETH-USDT"
    pos.quantity = Decimal("1.0")
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.client.place_order = AsyncMock()
    broker.client.get_open_orders = AsyncMock(return_value=[])

    # Patch load/save inside function imports
    import astra_bot.decision.live_positions as live_pos

    monkeypatch.setattr(live_pos, "load_positions", lambda path=None: lp.load_positions(pos_path))
    monkeypatch.setattr(
        live_pos,
        "save_positions",
        lambda positions, path=None: lp.save_positions(positions, path=pos_path),
    )
    journals = []

    def _aj(row):
        journals.append(row)

    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal", _aj
    )

    out = await clock.apply_tp_parity_v1(broker, send_tg=None)
    assert out.get("replaced", 0) == 0
    broker.client.place_order.assert_not_called()
    saved = lp.load_positions(pos_path)
    plan = saved["ETH-USDT|short"]
    assert plan.get("tp_parity_v1") is True or plan.get(clock._TP_PARITY_DONE) is True
    assert Decimal(str(plan["tp"])) == Decimal("3764")
    assert any(j.get("event") == "tp_parity_early_preserved" for j in journals)


@pytest.mark.asyncio
async def test_tp_parity_clamps_overshoot(tmp_path, monkeypatch):
    plans = {
        "BTC-USDT|long": {
            "symbol": "BTC-USDT",
            "side": "long",
            "entry": "100",
            "stop": "90",
            "initial_stop": "90",
            "tp": "140",  # 4R — overshoot
            "qty": "1.0",
            "tp_frac": 0.3,
        }
    }
    pos_path = tmp_path / "positions.json"
    from astra_bot.decision import live_positions as lp

    lp.save_positions(plans, path=pos_path)
    broker = MagicMock()
    pos = MagicMock()
    pos.symbol = "BTC-USDT"
    pos.quantity = Decimal("1.0")
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.client.place_order = AsyncMock()
    broker.client.get_open_orders = AsyncMock(return_value=[])
    broker.client.cancel_order = AsyncMock()

    monkeypatch.setattr(lp, "load_positions", lambda path=None: lp.load_positions(pos_path))
    monkeypatch.setattr(
        lp,
        "save_positions",
        lambda positions, path=None: lp.save_positions(positions, path=pos_path),
    )
    monkeypatch.setattr("astra_bot.decision.live_arm_wiring.append_journal", lambda row: None)

    out = await clock.apply_tp_parity_v1(broker, send_tg=None)
    assert out.get("replaced", 0) == 1
    broker.client.place_order.assert_called()
    call_kw = broker.client.place_order.call_args.kwargs
    # clamped to ~2.0–2.3R → stopPrice between 120 and 123
    sp = Decimal(str(call_kw["stop_price"]))
    assert Decimal("120") <= sp <= Decimal("123")
