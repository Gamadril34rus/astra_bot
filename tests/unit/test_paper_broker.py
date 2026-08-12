"""Тесты виртуального брокера с частичными тейками и трейлингом."""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.decision.broker import PaperBroker


def _bar(symbol: str, o, h, low, c, ts: int = 0):
    return models.Candle(
        exchange="test",
        symbol=symbol,
        timeframe="5m",
        open_time=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal("10"),
        quote_volume=Decimal("1"),
    )


@pytest.fixture()
def broker(tmp_path):
    return PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
    )


def test_long_stop_loss(broker):
    broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("102"),
        quantity=Decimal("1"),
    )
    closed = broker.on_bar(_bar("BTC-USDT", 100, 100, 98.5, 99))
    assert len(closed) == 1
    assert closed[0].exit_reason == "stop_loss"
    assert closed[0].pnl == -1.0
    assert not broker.positions


def test_long_partial_tps_and_trailing(broker):
    broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("103"),
        quantity=Decimal("1"),
    )
    pos = broker.positions[0]
    # TP1 at 101.
    closed1 = broker.on_bar(_bar("BTC-USDT", 100.5, 101.2, 100, 101))
    assert any(c.exit_reason == "tp1" for c in closed1)
    assert pos.tp_filled[0] is True
    # стоп должен переехать в БУ.
    assert pos.stop_loss == Decimal("100")
    # TP2 на 101.8.
    closed2 = broker.on_bar(_bar("BTC-USDT", 101.2, 102.0, 101, 101.9))
    assert any(c.exit_reason == "tp2" for c in closed2)
    # TP3 = entry + 2.5*risk = 102.5 закроет остаток.
    closed3 = broker.on_bar(_bar("BTC-USDT", 102, 102.7, 102, 102.6))
    assert any(c.exit_reason == "tp3" for c in closed3)
    assert not broker.positions


def test_short_tp_levels(broker):
    broker.open_position(
        symbol="BTC-USDT",
        direction="short",
        entry_price=Decimal("100"),
        stop_loss=Decimal("101"),
        take_profit=Decimal("97"),
        quantity=Decimal("1"),
    )
    closed = broker.on_bar(_bar("BTC-USDT", 100, 100, 98.9, 99))
    assert any(c.exit_reason == "tp1" for c in closed)


def test_persistence_roundtrip(tmp_path, broker):
    broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("103"),
        quantity=Decimal("2"),
    )
    broker.save()
    restored = PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
    )
    assert len(restored.positions) == 1
    assert restored.positions[0].entry_price == Decimal("100")
