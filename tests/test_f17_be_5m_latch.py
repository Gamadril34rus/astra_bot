"""F17: L3 be_earned latch from 5m extremes + F10 mark defer."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from scripts.zeus_live_clock import (
    _F17_5M_CACHE,
    _f17_be_earned_from_bars,
    sync_partial_breakeven,
)


def test_f17_bars_long_short_extremes():
    bars = [
        SimpleNamespace(high=Decimal("101"), low=Decimal("99")),
        SimpleNamespace(high=Decimal("105"), low=Decimal("100")),
    ]
    assert _f17_be_earned_from_bars(bars, side="long", net_be=Decimal("104")) is True
    assert _f17_be_earned_from_bars(bars, side="long", net_be=Decimal("106")) is False
    assert _f17_be_earned_from_bars(bars, side="short", net_be=Decimal("99.5")) is True
    assert _f17_be_earned_from_bars(bars, side="short", net_be=Decimal("98")) is False
    assert _f17_be_earned_from_bars(None, side="long", net_be=Decimal("100")) is False
    assert _f17_be_earned_from_bars([], side="long", net_be=Decimal("100")) is False


def test_spike_latches_without_place(tmp_path, monkeypatch):
    """5m spike crosses net_be but mark still below → be_earned, zero place_order."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journals: list[dict] = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    _F17_5M_CACHE.clear()

    plans = {
        "BTC-USDT|long": {
            "symbol": "BTC-USDT",
            "side": "long",
            "entry": "100",
            "stop": "90",
            "qty": "10",
            "plan_qty": "10",
            "ts": 1_700_000_000,
        }
    }
    Path("models/zeus_live_positions.json").write_text(
        json.dumps({"positions": plans, "ts": 1}), encoding="utf-8"
    )

    # partial qty = 60%
    pos = SimpleNamespace(
        symbol="BTC-USDT",
        quantity=Decimal("6"),
        side="long",
        mark_price=Decimal("99"),  # below entry/net_be
    )
    bars = [SimpleNamespace(high=Decimal("101"), low=Decimal("98"), open_time=1_700_000_000_000)]

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[pos])
    client.get_candles = AsyncMock(return_value=bars)
    client.place_order = AsyncMock()
    broker = MagicMock()
    broker.client = client
    broker.move_stop = AsyncMock()
    # no net_breakeven → gross entry
    if hasattr(broker, "net_breakeven_price"):
        del broker.net_breakeven_price

    out = asyncio.run(sync_partial_breakeven(broker, send_tg=None))
    assert out.get("moved") == []
    assert client.place_order.await_count == 0
    assert broker.move_stop.await_count == 0
    assert any(j.get("event") == "be_earned_5m" for j in journals)
    saved = json.loads(Path("models/zeus_live_positions.json").read_text())
    row = saved["positions"]["BTC-USDT|long"]
    assert row.get("be_earned") is True


def test_mark_ok_places_once_then_dedup(tmp_path, monkeypatch):
    """After be_earned + mark >= net_be → one move_stop; second tick zero."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journals: list[dict] = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    monkeypatch.setattr(
        "astra_bot.decision.live_margin_budget.only_tighter_stop",
        lambda side, cur, tgt: tgt,
    )
    monkeypatch.setattr(
        "astra_bot.decision.live_stop_telemetry.record_stop_change",
        lambda **kw: None,
    )
    _F17_5M_CACHE.clear()

    plans = {
        "ETH-USDT|long": {
            "symbol": "ETH-USDT",
            "side": "long",
            "entry": "100",
            "stop": "90",
            "qty": "5",
            "plan_qty": "10",  # partial 50%
            "be_earned": True,
            "ts": 1_700_000_000,
        }
    }
    Path("models/zeus_live_positions.json").write_text(
        json.dumps({"positions": plans, "ts": 1}), encoding="utf-8"
    )
    pos = SimpleNamespace(
        symbol="ETH-USDT",
        quantity=Decimal("5"),
        side="long",
        mark_price=Decimal("100.5"),
    )
    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[pos])
    client.get_candles = AsyncMock(return_value=[])
    broker = MagicMock()
    broker.client = client
    broker.move_stop = AsyncMock(return_value=True)

    out1 = asyncio.run(sync_partial_breakeven(broker, send_tg=None))
    assert "ETH-USDT" in out1.get("moved", [])
    assert broker.move_stop.await_count == 1
    assert any(j.get("event") == "be_catchup" for j in journals)
    catch = next(j for j in journals if j.get("event") == "be_catchup")
    assert catch.get("be_eligible_source") in ("mark", "5m")

    # second tick: be_moved set
    out2 = asyncio.run(sync_partial_breakeven(broker, send_tg=None))
    assert broker.move_stop.await_count == 1
    assert out2.get("moved") == []


def test_short_spike_latches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journals: list = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    _F17_5M_CACHE.clear()
    plans = {
        "SOL-USDT|short": {
            "symbol": "SOL-USDT",
            "side": "short",
            "entry": "100",
            "stop": "110",
            "qty": "10",
            "plan_qty": "20",
            "ts": 1_700_000_000,
        }
    }
    Path("models/zeus_live_positions.json").write_text(
        json.dumps({"positions": plans, "ts": 1}), encoding="utf-8"
    )
    pos = SimpleNamespace(
        symbol="SOL-USDT",
        quantity=Decimal("10"),
        side="short",
        mark_price=Decimal("101"),  # above entry — cannot place yet
    )
    bars = [SimpleNamespace(high=Decimal("102"), low=Decimal("99"), open_time=1_700_000_100_000)]
    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[pos])
    client.get_candles = AsyncMock(return_value=bars)
    broker = MagicMock()
    broker.client = client
    broker.move_stop = AsyncMock()
    out = asyncio.run(sync_partial_breakeven(broker, send_tg=None))
    assert out.get("moved") == []
    assert broker.move_stop.await_count == 0
    assert any(j.get("event") == "be_earned_5m" for j in journals)
