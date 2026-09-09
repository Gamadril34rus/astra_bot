"""Блок G: bars_held растёт на новых барах, а не на каждом тике."""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.decision.broker import PaperBroker


@pytest.fixture()
def broker(tmp_path):
    b = PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )
    b.open_position(
        symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
        stop_loss=Decimal("99"), take_profit=Decimal("102"), quantity=Decimal("1"),
    )
    return b


def _bar(ts: int, high: str = "101", low: str = "99.5"):
    return models.Candle(
        exchange="test", symbol="BTC-USDT", timeframe="5m", open_time=ts,
        open=Decimal("100"), high=Decimal(high), low=Decimal(low),
        close=Decimal("100"), volume=Decimal("10"), quote_volume=Decimal("1"),
    )


def test_same_bar_counts_once(broker):
    bar = _bar(1000)
    broker.update_extremes(bar)
    broker.update_extremes(bar)
    broker.update_extremes(bar)
    assert broker.positions[0].bars_held == 1


def test_new_bar_increments(broker):
    broker.update_extremes(_bar(1000))
    broker.update_extremes(_bar(2000))
    assert broker.positions[0].bars_held == 2


def test_extremes_still_live_on_same_bar(broker):
    broker.update_extremes(_bar(1000, high="101"))
    broker.update_extremes(_bar(1000, high="105"))  # тот же бар, новый хай
    assert broker.positions[0].bars_held == 1
    assert broker.positions[0].highest_price == Decimal("105")
