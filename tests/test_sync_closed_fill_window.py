"""Closed-plan fill history must start at the immutable entry timestamp."""
from __future__ import annotations

import asyncio
import time

from astra_bot.decision.live_broker import LiveBroker
from astra_bot.decision.live_positions import save_positions


class EmptyHistoryClient:
    income_endpoint_cooldown_active = True

    def __init__(self):
        self.fill_starts = []
        self.income_starts = []

    async def get_positions(self):
        return []

    async def get_open_orders(self, symbol=None):
        return []

    async def get_all_fills(self, symbol, *, start_ms=None, end_ms=None):
        self.fill_starts.append(start_ms)
        return []

    async def get_realized_income(self, symbol, *, start_ms=None, end_ms=None):
        self.income_starts.append(start_ms)
        return []


def test_sync_closed_uses_original_opened_at_for_fill_history(tmp_path):
    opened_at_ms = 1_700_000_000_000
    plan_path = tmp_path / "positions.json"
    save_positions(
        {
            "ETH-USDT|short": {
                "symbol": "ETH-USDT",
                "side": "short",
                "entry": "1900",
                "stop": "1950",
                "tp": "1800",
                "qty": "1",
                # Simulate a later BE/ratchet update: this timestamp is mutable.
                "ts": time.time(),
                "opened_at_ms": opened_at_ms,
                "pending_verify": False,
            }
        },
        plan_path,
    )
    client = EmptyHistoryClient()
    broker = LiveBroker(client)

    result = asyncio.run(broker.sync_closed_plans(positions_path=plan_path))

    expected_start_ms = opened_at_ms - 60_000
    assert client.fill_starts == [expected_start_ms]
    assert client.income_starts == [expected_start_ms]
    assert result["pending_fill"] == ["ETH-USDT"]
