"""Regression tests for side-aware live close reconciliation."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astra_bot.decision.live_broker import (
    LiveBroker,
    LiveBrokerConfig,
    _aggregate_exit_fills,
    _filter_fills_for_position_side,
)
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



def test_exit_fill_aggregation_isolated_by_bingx_position_side():
    fills = [
        {
            "tradeId": "long-fill",
            "positionSide": "LONG",
            "side": "SELL",
            "type": "STOP_MARKET",
            "price": "100",
            "qty": "1",
            "fee": "-0.10",
            "realizedPnl": "10.00",
        },
        {
            "tradeId": "short-fill",
            "positionSide": "SHORT",
            "side": "SELL",
            "type": "MARKET",
            "price": "101",
            "qty": "5",
            "fee": "-0.90",
            "realizedPnl": "-50.00",
        },
    ]
    long_fills, ambiguous = _filter_fills_for_position_side(fills, "long")
    assert ambiguous is False
    assert [row["tradeId"] for row in long_fills] == ["long-fill"]

    exit_avg, fees, reason, qty = _aggregate_exit_fills(
        long_fills, side="long", entry_px=110.0
    )
    assert exit_avg == 100.0
    assert fees == 0.10
    assert reason == "стоп"
    assert qty == 1.0
    assert sum(float(row["realizedPnl"]) for row in long_fills) == 10.0


def test_fill_without_position_side_invalidates_side_specific_batch():
    fills = [
        {
            "tradeId": "known-long",
            "positionSide": "LONG",
            "side": "SELL",
            "price": "100",
            "qty": "1",
            "fee": "-0.10",
        },
        {
            "tradeId": "unknown",
            "side": "SELL",
            "price": "101",
            "qty": "2",
            "fee": "-0.20",
        },
    ]
    selected, ambiguous = _filter_fills_for_position_side(fills, "long")
    assert selected == []
    assert ambiguous is True



def test_protective_order_requires_explicit_position_side_in_hedge_mode():
    from astra_bot.decision.live_broker import (
        _has_matching_protective_order,
        _has_unscoped_open_order,
        _has_unscoped_protective_order,
        _order_matches_position_side,
    )

    side_only_stop = SimpleNamespace(
        id="side-only-stop",
        symbol="BTC-USDT",
        order_type="STOP_MARKET",
        side="SELL",
        status="NEW",
    )
    assert _order_matches_position_side(side_only_stop, "long") is False
    assert _has_matching_protective_order(
        [side_only_stop], "long", kind="stop"
    ) is False
    assert _has_unscoped_protective_order(
        [side_only_stop], kind="stop"
    ) is True
    assert _has_unscoped_open_order([side_only_stop]) is True


def test_protective_order_with_explicit_hedge_side_is_scoped():
    from astra_bot.decision.live_broker import (
        _has_matching_protective_order,
        _has_unscoped_open_order,
        _has_unscoped_protective_order,
    )

    long_stop = SimpleNamespace(
        id="long-stop",
        symbol="BTC-USDT",
        order_type="STOP_MARKET",
        side="SELL",
        position_side="LONG",
        status="NEW",
    )
    assert _has_matching_protective_order(
        [long_stop], "long", kind="stop"
    ) is True
    assert _has_unscoped_protective_order([long_stop], kind="stop") is False
    assert _has_unscoped_open_order([long_stop]) is False


@pytest.mark.asyncio
async def test_sync_closed_plans_uses_position_history_when_fills_and_income_are_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astra_bot.decision import live_income_report, trade_viz
    from astra_bot.decision.live_positions import plan_key, save_positions

    monkeypatch.chdir(tmp_path)
    positions_path = tmp_path / "models" / "zeus_live_positions.json"
    opened_at_ms = 1_700_000_000_000
    stale_plan_opened_at_ms = opened_at_ms + 3 * 60 * 60 * 1000
    save_positions(
        {
            plan_key("BTC-USDT", "long"): {
                "symbol": "BTC-USDT",
                "side": "long",
                "qty": 1.0,
                "entry": 100.0,
                "stop": 98.0,
                "tp": 105.0,
                "leverage": 3,
                "pending_verify": False,
                "signal_id": "btc-long",
                "ts": stale_plan_opened_at_ms / 1000.0,
                "opened_at_ms": stale_plan_opened_at_ms,
                "initial_stop": 98.0,
            }
        },
        positions_path,
    )

    history_row = {
        "symbol": "BTC-USDT",
        "positionSide": "LONG",
        "openTime": opened_at_ms,
        "updateTime": opened_at_ms + 30_000,
        "avgClosePrice": "99.5",
        "netProfit": "-2.2",
        "positionCommission": "-0.1",
        "totalFunding": "-0.1",
        "positionId": "position-1",
    }
    trades_path = tmp_path / "models" / "zeus_live_trades.jsonl"
    trades_path.write_text(
        json.dumps(
            {
                "event": "entry",
                "symbol": "BTC-USDT",
                "side": "long",
                "signal_id": "btc-long",
                "ts": opened_at_ms / 1000.0,
                "entry": 100.0,
                "qty": 1.0,
                "leverage": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    client = SimpleNamespace(
        get_positions=AsyncMock(return_value=[]),
        get_open_orders=AsyncMock(return_value=[]),
        get_all_fills=AsyncMock(return_value=[]),
        get_realized_income=AsyncMock(return_value=[]),
        get_position_history=AsyncMock(return_value=[history_row]),
    )

    async def ambiguous_income(*args, **kwargs):
        return {
            "ambiguous_position_side": True,
            "has_realized": False,
            "pnl": 0.0,
            "fees": 0.0,
            "funding": 0.0,
            "net": 0.0,
        }

    monkeypatch.setattr(live_income_report, "fetch_income_with_retries", ambiguous_income)
    cards = []
    messages = []
    card_results = [False, True]

    async def fake_card(**kwargs):
        cards.append(kwargs)
        return card_results.pop(0)

    async def fake_tg(message):
        messages.append(message)

    monkeypatch.setattr(trade_viz, "notify_exit_stats_card", fake_card)
    broker = LiveBroker(client=client, config=LiveBrokerConfig())
    result = await broker.sync_closed_plans(
        positions_path=positions_path,
        send_tg=fake_tg,
    )

    assert result["closed"] == ["BTC-USDT"]
    assert result["pending_fill"] == []
    assert get_plan("BTC-USDT", positions_path, side="long") is None
    assert client.get_position_history.await_count == 1
    assert client.get_position_history.await_args.kwargs["start_ms"] == opened_at_ms - 60_000
    assert len(cards) == 1
    assert cards[0]["exit_price"] == 99.5
    assert cards[0]["pnl"] == -2.1
    assert len(messages) == 1

    pending_path = tmp_path / "models" / "zeus_live_tg_pending.json"
    pending_items = json.loads(pending_path.read_text()).get("items", [])
    assert len(pending_items) == 1
    assert pending_items[0]["card_sent"] is False
    assert pending_items[0]["followup_sent"] is True
    assert pending_items[0]["exit_price"] == 99.5

    from astra_bot.decision.live_income_report import resolve_pending_dogon

    delivered = await resolve_pending_dogon(
        client,
        send_tg=fake_tg,
        path=pending_path,
        trades_path=tmp_path / "models" / "zeus_live_trades.jsonl",
        now=1_800_000_000.0,
    )
    assert delivered == 1
    assert len(cards) == 2
    assert cards[1]["exit_price"] == 99.5
    assert len(messages) == 1
    assert json.loads(pending_path.read_text()).get("items", []) == []

    trade_rows = [
        json.loads(line)
        for line in (tmp_path / "models" / "zeus_live_trades.jsonl").read_text().splitlines()
    ]
    exit_row = next(row for row in trade_rows if row.get("event") == "exit")
    assert exit_row["pnl_source"] == "position_history"
    assert exit_row["exchange_fill_type"] == "POSITION_HISTORY"
    assert exit_row["position_id"] == "position-1"
    assert exit_row["pnl"] == -2.1
    assert exit_row["fees"] == -0.1
    assert exit_row["funding_fee"] == -0.1
    assert exit_row["exit"] == 99.5


