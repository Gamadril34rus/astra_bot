"""Regression tests for side-aware live close reconciliation."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
from astra_bot.decision.live_positions import get_plan, upsert_plan


@pytest.mark.asyncio
async def test_sync_closed_plans_only_cancels_and_removes_flat_hedge_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    positions_path = tmp_path / "models" / "zeus_live_positions.json"
    upsert_plan(
        "BTC-USDT",
        side="long",
        qty="0.01",
        entry="70000",
        stop="71000",
        pending_verify=True,
        signal_id="btc-long",
        path=positions_path,
    )
    upsert_plan(
        "BTC-USDT",
        side="short",
        qty="0.02",
        entry="70000",
        stop="71000",
        pending_verify=True,
        signal_id="btc-short",
        path=positions_path,
    )

    long_stop = SimpleNamespace(
        id="long-stop",
        symbol="BTC-USDT",
        order_type="STOP_MARKET",
        side="SELL",
        position_side="LONG",
        status="NEW",
    )
    long_tp = SimpleNamespace(
        id="long-tp",
        symbol="BTC-USDT",
        order_type="TAKE_PROFIT_MARKET",
        side="SELL",
        position_side="LONG",
        status="NEW",
    )
    short_stop = SimpleNamespace(
        id="short-stop",
        symbol="BTC-USDT",
        order_type="STOP_MARKET",
        side="BUY",
        position_side="SHORT",
        status="NEW",
    )
    cancelled: list[str] = []

    async def cancel_order(symbol: str, order_id: str) -> bool:
        assert symbol == "BTC-USDT"
        cancelled.append(order_id)
        return True

    client = SimpleNamespace(
        get_positions=AsyncMock(
            return_value=[
                SimpleNamespace(
                    symbol="BTC-USDT",
                    side="short",
                    quantity=Decimal("0.02"),
                )
            ]
        ),
        get_open_orders=AsyncMock(return_value=[long_stop, long_tp, short_stop]),
        cancel_order=cancel_order,
    )
    broker = LiveBroker(client=client, config=LiveBrokerConfig())

    result = await broker.sync_closed_plans(positions_path=positions_path)

    assert result["closed"] == ["BTC-USDT"]
    assert set(cancelled) == {"long-stop", "long-tp"}
    assert "short-stop" not in cancelled
    assert get_plan("BTC-USDT", positions_path, side="long") is None
    assert get_plan("BTC-USDT", positions_path, side="short") is not None


@pytest.mark.parametrize("reported_side", ["long", None])
@pytest.mark.asyncio
async def test_restore_does_not_drop_plan_after_unconfirmed_market_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reported_side: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    positions_path = tmp_path / "models" / "zeus_live_positions.json"
    position_before_close = SimpleNamespace(
        symbol="ETH-USDT",
        side="long",
        quantity=Decimal("1"),
    )
    position_after_close = SimpleNamespace(
        symbol="ETH-USDT",
        side=reported_side,
        quantity=Decimal("1"),
    )
    close_calls: list[tuple[str, str]] = []

    async def close_position(
        symbol: str,
        quantity: Decimal | None = None,
        position_side: str | None = None,
    ) -> bool:
        close_calls.append((symbol, str(position_side)))
        # Simulate an API acknowledgement while the exchange still reports the leg open.
        return True

    client = SimpleNamespace(
        get_positions=AsyncMock(
            side_effect=[[position_before_close], [position_after_close]]
        ),
        get_open_orders=AsyncMock(return_value=[]),
        close_position=close_position,
        cancel_order=AsyncMock(return_value=True),
    )
    broker = LiveBroker(client=client, config=LiveBrokerConfig())

    result = await broker.restore_missing_stops(positions_path=positions_path)

    assert close_calls == [("ETH-USDT", "long")]
    assert result["closed_legs"] == []
    assert result["orphan_protection_unknown_legs"] == ["ETH-USDT|long"]
    assert result["closed_naked"] == []
    # The warning cooldown is persisted across runner invocations.
    warning_state = json.loads(
        (tmp_path / "models" / "zeus_orphan_protected_warn.json").read_text()
    )
    assert "ETH-USDT|long" in warning_state


@pytest.mark.asyncio
async def test_sync_closed_plans_retries_when_side_order_cancel_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    positions_path = tmp_path / "models" / "zeus_live_positions.json"
    upsert_plan(
        "BTC-USDT",
        side="long",
        qty="0.01",
        entry="70000",
        stop="71000",
        pending_verify=True,
        signal_id="btc-long",
        path=positions_path,
    )
    upsert_plan(
        "BTC-USDT",
        side="short",
        qty="0.02",
        entry="70000",
        stop="71000",
        pending_verify=True,
        signal_id="btc-short",
        path=positions_path,
    )
    long_stop = SimpleNamespace(
        id="long-stop",
        symbol="BTC-USDT",
        order_type="STOP_MARKET",
        side="SELL",
        position_side="LONG",
        status="NEW",
    )
    short_stop = SimpleNamespace(
        id="short-stop",
        symbol="BTC-USDT",
        order_type="STOP_MARKET",
        side="BUY",
        position_side="SHORT",
        status="NEW",
    )
    cancel_calls: list[str] = []

    async def cancel_order(symbol: str, order_id: str) -> bool:
        cancel_calls.append(order_id)
        return False

    client = SimpleNamespace(
        get_positions=AsyncMock(
            return_value=[
                SimpleNamespace(
                    symbol="BTC-USDT",
                    side="short",
                    quantity=Decimal("0.02"),
                )
            ]
        ),
        get_open_orders=AsyncMock(return_value=[long_stop, short_stop]),
        cancel_order=cancel_order,
    )
    broker = LiveBroker(client=client, config=LiveBrokerConfig())

    result = await broker.sync_closed_plans(positions_path=positions_path)

    assert result["closed"] == []
    assert result["pending_cleanup"] == ["BTC-USDT|long"]
    assert cancel_calls == ["long-stop"]
    assert get_plan("BTC-USDT", positions_path, side="long") is not None
    assert get_plan("BTC-USDT", positions_path, side="short") is not None
