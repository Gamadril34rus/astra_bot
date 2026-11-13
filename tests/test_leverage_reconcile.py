from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astra_bot.adapters.bingx.client_live import parse_position_item
from astra_bot.decision.live_broker import LiveBroker
from astra_bot.decision.live_positions import load_positions, save_positions


def test_bingx_position_parser_preserves_reported_leverage():
    pos = parse_position_item({
        "symbol": "INJ-USDT",
        "positionAmt": "-548",
        "positionSide": "SHORT",
        "avgPrice": "7.896",
        "markPrice": "7.455",
        "leverage": "6",
    })
    assert pos is not None
    assert pos.side == "short"
    assert pos.leverage == 6


def test_plan_leverage_reconciles_from_exchange_without_changing_exchange(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    save_positions({
        "INJ-USDT|short": {
            "symbol": "INJ-USDT", "side": "short",
            "qty": "548", "leverage": 5, "entry": "7.896",
        }
    })
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    import astra_bot.decision.live_broker as module
    monkeypatch.setattr(module, "append_live_trade", lambda row: None)

    client = SimpleNamespace(
        get_positions=AsyncMock(return_value=[
            SimpleNamespace(
                symbol="INJ-USDT", side="short",
                quantity=Decimal("548"), leverage=6,
            )
        ])
    )
    broker = LiveBroker(client=client)
    out = asyncio.run(broker.reconcile_plan_qty())
    saved = load_positions()

    assert out["reconciled"] == []
    assert out["leverage_reconciled"][0]["old_leverage"] == 5
    assert out["leverage_reconciled"][0]["exchange_leverage"] == 6
    assert saved["INJ-USDT|short"]["leverage"] == 6
    assert any(row.get("event") == "leverage_reconcile" for row in journals)
