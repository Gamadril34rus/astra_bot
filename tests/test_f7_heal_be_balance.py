"""F7: balance pos count, BE mark from current_price, dead-TP heal replace-first."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_fmt_balance_counts_positions_not_zeroed_by_equity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / "zeus_live_positions.json").write_text(
        json.dumps({
            "positions": {
                "BTC-USDT|long": {"symbol": "BTC-USDT", "side": "long", "qty": "1"},
                "DOGE-USDT|short": {"symbol": "DOGE-USDT", "side": "short", "qty": "2"},
                "A|x": {"symbol": "A", "side": "long", "qty": "1"},
                "B|x": {"symbol": "B", "side": "long", "qty": "1"},
                "C|x": {"symbol": "C", "side": "long", "qty": "1"},
            },
            "ts": 1.0,
        }),
        encoding="utf-8",
    )
    (models / "zeus_live_equity.json").write_text(
        json.dumps({"equity": 100000.0, "ts": 2.0}),
        encoding="utf-8",
    )
    from astra_bot.decision.tg_live_commands import _fmt_balance

    text = _fmt_balance()
    assert "открыто позиций: 5" in text
    assert "100000.00$" in text or "100000" in text


def test_sync_partial_uses_current_price(tmp_path, monkeypatch):
    """F7.4: mark from Position.current_price (BingX markPrice mapping)."""
    import asyncio
    from scripts.zeus_live_clock import sync_partial_breakeven
    from astra_bot.decision.live_positions import upsert_plan

    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    pos_path = models / "zeus_live_positions.json"
    upsert_plan(
        "DOGE-USDT",
        side="short",
        qty="100",
        stop="0.09",
        tp="0.07",
        entry="0.08",
        path=pos_path,
        be_moved=False,
    )

    pos = SimpleNamespace(
        symbol="DOGE-USDT",
        quantity=Decimal("100"),
        current_price=Decimal("0.06"),  # short MFE >> 0.8R
        mark_price=None,
        markPrice=None,
        price=None,
    )

    class Broker:
        class client:
            @staticmethod
            async def get_positions():
                return [pos]

        def net_breakeven_price(self, p):
            return Decimal("0.08")

        async def move_stop(self, *a, **k):
            return SimpleNamespace(id="s1")

    async def _run():
        return await sync_partial_breakeven(Broker(), positions_path=pos_path)

    out = asyncio.get_event_loop().run_until_complete(_run())
    # may move or fail move_stop - but must NOT skip for no mark
    assert "DOGE-USDT" not in out.get("skipped", []) or out.get("moved")


def test_heal_dead_tp_replace_first_after_2_ticks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    from astra_bot.decision.live_positions import upsert_plan
    from scripts.zeus_live_clock import heal_dead_tp_legs

    upsert_plan(
        "DOGE-USDT",
        side="short",
        qty="100",
        stop="0.09",
        tp="0.077",
        entry="0.08",
        path=tmp_path / "models" / "zeus_live_positions.json",
    )
    placed = []
    cancelled = []

    old_tp = SimpleNamespace(
        id="old1",
        order_type="TAKE_PROFIT_MARKET",
        status="new",
        status_raw="NEW",
        stop_price=Decimal("0.077"),
        quantity=Decimal("40"),
    )
    pos = SimpleNamespace(
        symbol="DOGE-USDT",
        quantity=Decimal("100"),
        current_price=Decimal("0.076"),  # crossed below TP for short
    )

    class Client:
        async def get_positions(self):
            return [pos]

        async def get_open_orders(self, symbol=None):
            return [old_tp]

        async def place_order(self, **kw):
            placed.append(kw)
            return SimpleNamespace(id="new1")

        async def cancel_order(self, symbol, order_id):
            cancelled.append(order_id)
            return True

    class Broker:
        client = Client()

    import asyncio
    async def _run():
        r1 = await heal_dead_tp_legs(Broker())
        assert r1.get("healed") == []
        assert "DOGE-USDT" in r1.get("watched", [])
        assert placed == []
        r2 = await heal_dead_tp_legs(Broker())
        assert "DOGE-USDT" in r2.get("healed", [])
        assert len(placed) == 1
        assert placed[0].get("reduce_only") is False
        assert placed[0].get("working_type") == "MARK_PRICE"
        assert cancelled == ["old1"]
    asyncio.get_event_loop().run_until_complete(_run())
