"""Hedge-mode regression tests for Zeus clock reconciliation and TP repair."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astra_bot.decision.live_positions import save_positions, upsert_plan
from scripts.zeus_live_clock import (
    _open_orders_for_hedge_leg,
    _position_for_hedge_leg,
    _positions_by_hedge_leg,
    apply_tp_parity_v1,
    detect_position_missing_with_orders,
    heal_dead_tp_legs,
    sync_partial_breakeven,
)


def _position(side: str, quantity: str, mark: str):
    return SimpleNamespace(
        symbol="BTC-USDT",
        side=side,
        quantity=Decimal(quantity),
        entry_price=Decimal("100"),
        mark_price=Decimal(mark),
    )


def _tp(order_id: str, side: str, quantity: str, price: str):
    return SimpleNamespace(
        id=order_id,
        symbol="BTC-USDT",
        side="SELL" if side == "long" else "BUY",
        position_side=side.upper(),
        order_type="TAKE_PROFIT_MARKET",
        status="NEW",
        quantity=Decimal(quantity),
        stop_price=Decimal(price),
    )


def _stop(order_id: str, side: str):
    return SimpleNamespace(
        id=order_id,
        symbol="BTC-USDT",
        side="SELL" if side == "long" else "BUY",
        position_side=side.upper(),
        order_type="STOP_MARKET",
        status="NEW",
    )


def test_position_index_and_order_filter_are_hedge_side_specific():
    long_position = _position("long", "1", "110")
    short_position = _position("short", "2", "90")
    index = _positions_by_hedge_leg([long_position, short_position])

    assert _position_for_hedge_leg(index, "BTC-USDT", "long") is long_position
    assert _position_for_hedge_leg(index, "BTC-USDT", "short") is short_position

    long_tp = _tp("long-tp", "long", "0.3", "110")
    short_tp = _tp("short-tp", "short", "0.6", "90")
    orders = [long_tp, short_tp]

    assert _open_orders_for_hedge_leg(
        orders, "BTC-USDT", "long", kind="take_profit"
    ) == [long_tp]
    assert _open_orders_for_hedge_leg(
        orders, "BTC-USDT", "short", kind="take_profit"
    ) == [short_tp]


def test_duplicate_exchange_rows_make_one_hedge_leg_ambiguous():
    first = _position("long", "1", "110")
    duplicate = _position("long", "1.1", "111")
    index = _positions_by_hedge_leg([first, duplicate])

    assert _position_for_hedge_leg(index, "BTC-USDT", "long") is None


def test_sync_partial_breakeven_uses_exact_long_and_short_quantities(
    tmp_path, monkeypatch
):
    async def run():
        monkeypatch.chdir(tmp_path)
        (tmp_path / "models").mkdir()
        plans_path = tmp_path / "models" / "positions.json"
        upsert_plan(
            "BTC-USDT",
            side="long",
            qty="1",
            plan_qty="1",
            entry="100",
            stop="90",
            tp="120",
            path=plans_path,
        )
        upsert_plan(
            "BTC-USDT",
            side="short",
            qty="2",
            plan_qty="2",
            entry="100",
            stop="110",
            tp="80",
            path=plans_path,
        )

        positions = [
            _position("long", "0.6", "110"),
            _position("short", "1.2", "90"),
        ]

        class Broker:
            def __init__(self):
                self.client = SimpleNamespace(
                    get_positions=AsyncMock(return_value=positions)
                )
                self.stop_moves = []

            def net_breakeven_price(self, position):
                return position.entry_price

            async def move_stop(self, symbol, side, quantity, price):
                self.stop_moves.append((symbol, side, quantity, price))

        broker = Broker()
        result = await sync_partial_breakeven(broker, positions_path=plans_path)

        assert len(broker.stop_moves) == 2
        observed = {
            (symbol, side): quantity
            for symbol, side, quantity, _price in broker.stop_moves
        }
        assert observed == {
            ("BTC-USDT", "long"): Decimal("0.6"),
            ("BTC-USDT", "short"): Decimal("1.2"),
        }
        assert len(result["moved"]) == 2

    asyncio.run(run())


def test_tp_parity_sizes_and_cancels_only_matching_hedge_leg(tmp_path, monkeypatch):
    async def run():
        monkeypatch.chdir(tmp_path)
        (tmp_path / "models").mkdir()
        save_positions(
            {
                "BTC-USDT|long": {
                    "symbol": "BTC-USDT",
                    "side": "long",
                    "entry": "100",
                    "stop": "90",
                    "initial_stop": "90",
                    "tp": "140",
                    "qty": "1",
                    "tp_frac": 0.3,
                },
                "BTC-USDT|short": {
                    "symbol": "BTC-USDT",
                    "side": "short",
                    "entry": "100",
                    "stop": "110",
                    "initial_stop": "110",
                    "tp": "60",
                    "qty": "2",
                    "tp_frac": 0.3,
                },
            }
        )
        long_tp = _tp("old-long-tp", "long", "0.3", "140")
        short_tp = _tp("old-short-tp", "short", "0.6", "60")

        class Client:
            def __init__(self):
                self.cancelled = []
                self.placed = []

            async def get_positions(self):
                return [
                    _position("long", "1", "105"),
                    _position("short", "2", "95"),
                ]

            async def get_open_orders(self, symbol=None):
                return [long_tp, short_tp]

            async def place_order(self, **kwargs):
                self.placed.append(kwargs)
                return SimpleNamespace(id=f"new-{len(self.placed)}")

            async def cancel_order(self, symbol, order_id):
                self.cancelled.append((symbol, order_id))
                return True

        monkeypatch.setattr(
            "astra_bot.decision.live_arm_wiring.append_journal", lambda _row: None
        )
        client = Client()
        broker = SimpleNamespace(client=client)

        result = await apply_tp_parity_v1(broker)

        assert result["replaced"] == 2
        assert {row["quantity"] for row in client.placed} == {
            Decimal("0.30000000"),
            Decimal("0.60000000"),
        }
        assert set(client.cancelled) == {
            ("BTC-USDT", "old-long-tp"),
            ("BTC-USDT", "old-short-tp"),
        }

    asyncio.run(run())


def test_dead_tp_heal_replaces_only_the_matching_hedge_tp(tmp_path, monkeypatch):
    async def run():
        monkeypatch.chdir(tmp_path)
        models = tmp_path / "models"
        models.mkdir()
        plans_path = models / "positions.json"
        save_positions(
            {
                "BTC-USDT|long": {
                    "symbol": "BTC-USDT",
                    "side": "long",
                    "entry": "100",
                    "stop": "90",
                    "tp1": "110",
                    "qty": "1",
                },
                "BTC-USDT|short": {
                    "symbol": "BTC-USDT",
                    "side": "short",
                    "entry": "100",
                    "stop": "110",
                    "tp1": "90",
                    "qty": "2",
                },
            },
            plans_path,
        )
        (models / "zeus_tp_heal_state.json").write_text(
            json.dumps(
                {
                    "BTC-USDT|long": {"ticks": 1},
                    "BTC-USDT|short": {"ticks": 1},
                }
            ),
            encoding="utf-8",
        )
        long_tp = _tp("old-long-tp", "long", "0.3", "110")
        short_tp = _tp("old-short-tp", "short", "0.6", "90")

        class Client:
            def __init__(self):
                self.placed = []
                self.cancelled = []

            async def get_positions(self):
                return [
                    _position("long", "0.7", "111"),
                    _position("short", "1.4", "89"),
                ]

            async def get_open_orders(self, symbol=None):
                return [long_tp, short_tp]

            async def place_order(self, **kwargs):
                self.placed.append(kwargs)
                return SimpleNamespace(id=f"replacement-{len(self.placed)}")

            async def cancel_order(self, symbol, order_id):
                self.cancelled.append((symbol, order_id))
                return True

        monkeypatch.setattr(
            "astra_bot.decision.live_arm_wiring.append_journal", lambda _row: None
        )
        client = Client()
        broker = SimpleNamespace(client=client)

        result = await heal_dead_tp_legs(broker, positions_path=plans_path)

        assert len(client.placed) == 2
        assert {row["quantity"] for row in client.placed} == {
            Decimal("0.3"),
            Decimal("0.6"),
        }
        assert {row["position_side"] for row in client.placed} == {"LONG", "SHORT"}
        assert set(client.cancelled) == {
            ("BTC-USDT", "old-long-tp"),
            ("BTC-USDT", "old-short-tp"),
        }
        assert len(result["healed"]) == 2

    asyncio.run(run())


def test_missing_position_detection_does_not_confuse_opposite_hedge_leg(
    tmp_path, monkeypatch
):
    async def run():
        monkeypatch.chdir(tmp_path)
        models = tmp_path / "models"
        models.mkdir()
        plans_path = models / "positions.json"
        save_positions(
            {
                "BTC-USDT|long": {"symbol": "BTC-USDT", "side": "long", "qty": "1"},
                "BTC-USDT|short": {"symbol": "BTC-USDT", "side": "short", "qty": "2"},
            },
            plans_path,
        )
        orders = [
            _stop("long-stop", "long"),
            _tp("long-tp", "long", "0.3", "110"),
            _stop("short-stop", "short"),
            _tp("short-tp", "short", "0.6", "90"),
        ]

        class Client:
            async def get_positions(self):
                return [_position("short", "2", "95")]

            async def get_open_orders(self, symbol=None):
                return orders

        monkeypatch.setattr(
            "astra_bot.decision.live_arm_wiring.append_journal", lambda _row: None
        )
        broker = SimpleNamespace(client=Client())

        result = await detect_position_missing_with_orders(
            broker, positions_path=plans_path
        )

        assert result["missing"] == ["BTC-USDT|long"]
        state = json.loads((models / "zeus_pos_missing_warn.json").read_text())
        assert "BTC-USDT|long" in state

    asyncio.run(run())


def test_missing_detector_does_not_call_duplicate_side_row_missing(
    tmp_path, monkeypatch
):
    async def run():
        monkeypatch.chdir(tmp_path)
        models = tmp_path / "models"
        models.mkdir()
        plans_path = models / "positions.json"
        save_positions(
            {
                "BTC-USDT|long": {
                    "symbol": "BTC-USDT",
                    "side": "long",
                    "qty": "1",
                }
            },
            plans_path,
        )
        duplicate_positions = [
            _position("long", "1", "110"),
            _position("long", "1.1", "111"),
        ]
        orders = [_stop("long-stop", "long"), _tp("long-tp", "long", "0.3", "110")]

        class Client:
            async def get_positions(self):
                return duplicate_positions

            async def get_open_orders(self, symbol=None):
                return orders

        monkeypatch.setattr(
            "astra_bot.decision.live_arm_wiring.append_journal", lambda _row: None
        )
        result = await detect_position_missing_with_orders(
            SimpleNamespace(client=Client()), positions_path=plans_path
        )
        assert result["missing"] == []

    asyncio.run(run())
