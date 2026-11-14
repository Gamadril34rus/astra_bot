from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from astra_bot.decision.live_positions import save_positions
from scripts.zeus_live_clock import heal_dead_tp_legs


def test_crossed_tp_is_terminal_and_does_not_retry_invalid_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    save_positions({
        "INJ-USDT|short": {
            "symbol": "INJ-USDT",
            "side": "short",
            "entry": "7.896",
            "qty": "548",
            "tp1": "7.6228",
            "tp": "7.134",
        }
    })
    state_path = tmp_path / "models" / "zeus_tp_heal_state.json"
    state_path.write_text(json.dumps({
        "INJ-USDT|short": {"ticks": 242, "tp": "7.6228", "mark": "7.455"}
    }), encoding="utf-8")
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    broker = MagicMock()
    broker.client.get_positions = AsyncMock(return_value=[
        SimpleNamespace(symbol="INJ-USDT", quantity=Decimal("548"), mark_price=Decimal("7.455"), side="short")
    ])
    broker.client.get_open_orders = AsyncMock(return_value=[
        SimpleNamespace(
            id="tp1-old", symbol="INJ-USDT", order_type="TAKE_PROFIT_MARKET",
            side="BUY", position_side="SHORT",
            status_raw="NEW", stop_price=Decimal("7.6228"),
            quantity=Decimal("219.2"),
        )
    ])
    broker.client.place_order = AsyncMock()
    broker.client.cancel_order = AsyncMock()

    first = asyncio.run(heal_dead_tp_legs(broker))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert first["watched"] == ["INJ-USDT"]
    assert state["INJ-USDT|short"]["status"] == "crossed_unfilled"
    assert state["INJ-USDT|short"]["ticks"] == 243
    assert any(row.get("event") == "tp_crossed_unfilled" for row in journals)
    broker.client.place_order.assert_not_awaited()
    broker.client.cancel_order.assert_not_awaited()

    journal_count = len(journals)
    second = asyncio.run(heal_dead_tp_legs(broker))
    assert second["watched"] == ["INJ-USDT"]
    assert len(journals) == journal_count
    broker.client.place_order.assert_not_awaited()
