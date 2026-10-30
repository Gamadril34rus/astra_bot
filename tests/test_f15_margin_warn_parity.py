"""F15 pre-merge: margin-warn on restore + tp_parity place-before-cancel."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.decision.live_margin_warn import (
    MARGIN_WARN_PATH,
    maybe_warn_margin_reject,
    set_send_tg,
)
from astra_bot.decision.live_positions import save_positions


def test_margin_warn_restore_once_per_hour(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    sent: list[str] = []

    def _tg(msg: str) -> None:
        sent.append(msg)

    set_send_tg(_tg)
    err = "BingX: order size must be less than available 42000"
    assert maybe_warn_margin_reject("DOT-USDT", err, _tg) is True
    assert len(sent) == 1
    assert "защита отклонена" in sent[0]
    assert "DOT-USDT" in sent[0]
    # second within hour — no repeat
    assert maybe_warn_margin_reject("DOT-USDT", err, _tg) is False
    assert len(sent) == 1
    # state file written
    assert MARGIN_WARN_PATH.exists()
    state = json.loads(MARGIN_WARN_PATH.read_text())
    assert "DOT-USDT" in state


def test_restore_tp_failed_journals_and_warns(tmp_path, monkeypatch):
    """restore_missing_stops TP reject → restore_tp_failed journal + margin warn once."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journals: list[dict] = []
    trades: list[dict] = []
    sent: list[str] = []

    def _tg(msg: str) -> None:
        sent.append(msg)

    set_send_tg(_tg)
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    monkeypatch.setattr(
        "astra_bot.decision.live_broker.append_live_trade",
        lambda row, path=None: trades.append(row),
    )

    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
    from astra_bot.decision.live_positions import upsert_plan

    upsert_plan(
        "DOT-USDT",
        side="long",
        qty="1000",
        stop="1.0",
        tp="1.5",
        tp1="1.5",
        entry="1.2",
        path=Path("models/zeus_live_positions.json"),
    )

    class Client:
        async def get_positions(self):
            return [SimpleNamespace(symbol="DOT-USDT", quantity=Decimal("1000"), side="long")]

        async def get_open_orders(self, symbol=None):
            # stop present, no TP → restore TP path
            return [
                SimpleNamespace(
                    symbol="DOT-USDT",
                    order_type="STOP_MARKET",
                    id="s1",
                )
            ]

        async def place_order(self, **kw):
            raise RuntimeError("order size must be less than available 100")

        async def get_instrument(self, symbol):
            return SimpleNamespace(step_size=Decimal("0.1"), quantity_precision=1)

        async def get_instruments(self, symbol=None):
            return []

    b = LiveBroker(Client(), LiveBrokerConfig())
    asyncio.run(b.restore_missing_stops(send_tg=_tg))
    assert any(r.get("event") == "restore_tp_failed" for r in trades + journals)
    assert len(sent) == 1
    # second restore same hour — still journal, no second TG
    asyncio.run(b.restore_missing_stops(send_tg=_tg))
    assert len(sent) == 1


def test_tp_parity_place_before_cancel(tmp_path, monkeypatch):
    """REPLACE-FIRST: place new TP then cancel old — no naked window."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    calls: list[tuple] = []
    journals: list[dict] = []

    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )

    save_positions(
        {
            "BTC-USDT|long": {
                "symbol": "BTC-USDT",
                "side": "long",
                "entry": "100",
                "stop": "90",
                "tp": "140",  # 4R overshoot → outside [2.0,2.3]
                "tp1": "140",
                "qty": "10",
            }
        }
    )

    class Client:
        async def get_positions(self):
            return [SimpleNamespace(symbol="BTC-USDT", quantity=Decimal("10"), side="long")]

        async def get_open_orders(self, symbol=None):
            return [
                SimpleNamespace(
                    symbol="BTC-USDT",
                    order_type="TAKE_PROFIT_MARKET",
                    type="TAKE_PROFIT_MARKET",
                    id="old_tp_1",
                )
            ]

        async def place_order(self, **kw):
            calls.append(("place", kw.get("order_type"), float(kw.get("stop_price") or 0)))
            return SimpleNamespace(id="new_tp")

        async def cancel_order(self, symbol, oid):
            calls.append(("cancel", oid))
            return True

    from scripts.zeus_live_clock import apply_tp_parity_v1

    broker = MagicMock()
    broker.client = Client()
    out = asyncio.run(apply_tp_parity_v1(broker, send_tg=None))
    assert out.get("replaced", 0) >= 1
    # place before any cancel
    types = [c[0] for c in calls]
    assert "place" in types and "cancel" in types
    assert types.index("place") < types.index("cancel")
    assert any(r.get("event") == "tp_parity_replace" for r in journals)


def test_tp_parity_old_filled_not_hard_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journals: list[dict] = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    save_positions(
        {
            "ETH-USDT|long": {
                "symbol": "ETH-USDT",
                "side": "long",
                "entry": "100",
                "stop": "90",
                "tp": "140",
                "tp1": "140",
                "qty": "5",
            }
        }
    )

    class Client:
        async def get_positions(self):
            return [SimpleNamespace(symbol="ETH-USDT", quantity=Decimal("5"), side="long")]

        async def get_open_orders(self, symbol=None):
            return [
                SimpleNamespace(
                    order_type="TAKE_PROFIT_MARKET",
                    type="TAKE_PROFIT_MARKET",
                    id="old1",
                )
            ]

        async def place_order(self, **kw):
            return SimpleNamespace(id="new1")

        async def cancel_order(self, symbol, oid):
            raise RuntimeError("order already filled")

    from scripts.zeus_live_clock import apply_tp_parity_v1

    broker = MagicMock()
    broker.client = Client()
    out = asyncio.run(apply_tp_parity_v1(broker, send_tg=None))
    assert out.get("replaced", 0) >= 1
    assert out.get("failed", 0) == 0
    assert any(r.get("event") == "tp_parity_old_filled" for r in journals)
