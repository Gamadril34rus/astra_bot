"""B-015: step from instrument → qty multiple of step."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astra_bot.decision.live_arm import quantize_qty, size_qty
from astra_bot.decision.live_arm_wiring import resolve_step_size


@pytest.mark.asyncio
async def test_resolve_step_size_from_instrument() -> None:
    inst = SimpleNamespace(symbol="BTC-USDT", step_size=Decimal("0.01"))
    client = SimpleNamespace(get_instruments=AsyncMock(return_value=[inst]))
    broker = SimpleNamespace(client=client)
    step = await resolve_step_size(broker, "BTC-USDT")
    assert step == Decimal("0.01")
    qty = size_qty(Decimal("10"), Decimal("100"), Decimal("95"), step)
    # risk 10 / dist 5 = 2 → floor to 0.01
    assert qty == Decimal("2.00") or qty == Decimal("2.0")
    assert qty % Decimal("0.01") == 0


def test_quantize_qty_step_001() -> None:
    q = quantize_qty(Decimal("1.234"), Decimal("0.01"))
    assert q == Decimal("1.23")
