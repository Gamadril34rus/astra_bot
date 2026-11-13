from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from scripts.zeus_live_clock import _F17_5M_CACHE, sync_partial_breakeven


def test_session_be_trigger_moves_at_profile_threshold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    monkeypatch.setattr(
        "astra_bot.decision.live_margin_budget.only_tighter_stop",
        lambda side, current, target: target,
    )
    monkeypatch.setattr(
        "astra_bot.decision.live_stop_telemetry.record_stop_change",
        lambda **kwargs: None,
    )
    _F17_5M_CACHE.clear()

    plans = {
        "INJ-USDT|short": {
            "symbol": "INJ-USDT",
            "side": "short",
            "entry": "100",
            "stop": "110",
            "initial_stop": "110",
            "qty": "10",
            "plan_qty": "10",
            "ts": 1_700_000_000,
        }
    }
    Path("models/zeus_live_positions.json").write_text(
        json.dumps({"positions": plans, "ts": 1}), encoding="utf-8",
    )
    pos = SimpleNamespace(
        symbol="INJ-USDT",
        quantity=Decimal("10"),
        side="short",
        mark_price=Decimal("95.5"),  # 0.45R MFE against 10-point initial risk
    )
    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[pos])
    client.get_candles = AsyncMock(return_value=[])
    broker = MagicMock()
    broker.client = client
    broker.net_breakeven_price = lambda position: Decimal("100.1")
    broker.move_stop = AsyncMock(return_value=True)

    out = asyncio.run(
        sync_partial_breakeven(broker, send_tg=None, trigger_r=0.40)
    )

    assert "INJ-USDT" in out.get("moved", [])
    assert broker.move_stop.await_count == 1
    event = next(row for row in journals if row.get("event") == "price_breakeven")
    assert event["trigger_r"] == 0.40
    assert event["mfe_r"] == 0.45
