"""Блок F: брокер громко отклоняет перевёрнутые стопы/тейки."""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.decision.broker import PaperBroker


@pytest.fixture()
def broker(tmp_path):
    return PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )


def test_valid_long_and_short(broker):
    broker.open_position(
        symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
        stop_loss=Decimal("99"), take_profit=Decimal("102"), quantity=Decimal("1"),
    )
    broker.open_position(
        symbol="ETH-USDT", direction="short", entry_price=Decimal("100"),
        stop_loss=Decimal("101"), take_profit=Decimal("98"), quantity=Decimal("1"),
    )
    assert len(broker.positions) == 2


def test_inverted_stop_rejected(broker):
    with pytest.raises(ValueError):
        broker.open_position(
            symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
            stop_loss=Decimal("101"), take_profit=Decimal("102"), quantity=Decimal("1"),
        )
    with pytest.raises(ValueError):
        broker.open_position(
            symbol="BTC-USDT", direction="short", entry_price=Decimal("100"),
            stop_loss=Decimal("99"), take_profit=Decimal("98"), quantity=Decimal("1"),
        )
    assert broker.positions == []


def test_wrong_side_take_rejected(broker):
    with pytest.raises(ValueError):
        broker.open_position(
            symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
            stop_loss=Decimal("99"), take_profit=Decimal("98"), quantity=Decimal("1"),
        )
    assert broker.positions == []


def test_zero_take_allowed_like_tsmomentum(broker):
    # ts_momentum живёт до смены режима и передаёт take=0 — это «без тейка».
    broker.open_position(
        symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
        stop_loss=Decimal("99"), take_profit=Decimal("0"), quantity=Decimal("1"),
        no_take_profit=True,
    )
    assert len(broker.positions) == 1


def test_unknown_direction_rejected(broker):
    with pytest.raises(ValueError):
        broker.open_position(
            symbol="BTC-USDT", direction="sideways", entry_price=Decimal("100"),
            stop_loss=Decimal("99"), take_profit=Decimal("102"), quantity=Decimal("1"),
        )
