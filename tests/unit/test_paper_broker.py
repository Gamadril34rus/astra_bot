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
    # Нулевые издержки: старые тесты проверяют механику тейков/стопов
    # на чистых ценах. Издержки покрыты отдельными тестами ниже.
    return PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )


@pytest.fixture()
def cost_broker(tmp_path):
    """Брокер с реальными издержками: 0.1% комиссия, 0.1% slippage."""
    return PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=Decimal("0.001"),
        slippage_pct=Decimal("0.001"),
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


def test_open_position_without_take_profits(broker):
    pos = broker.open_position(
        symbol="BTC/USDT",
        direction="long",
        entry_price=Decimal("60000"),
        stop_loss=Decimal("57000"),
        take_profit=Decimal("0"),
        quantity=Decimal("0.01"),
        strategy="ts_momentum",
        no_take_profit=True,
    )
    assert pos.take_profits == []
    assert pos.tp_filled == []


def test_fees_and_slippage_reduce_pnl(cost_broker):
    """Вход 100 со slippage 0.1% → fill 100.1; стоп 99, exit fill 98.901.

    gross = 98.901 - 100.1 = -1.199
    fees  = 100.1*0.001 (вход) + 98.901*0.001 (выход) = 0.199001
    pnl   = -1.199 - 0.199001 = -1.398001
    """
    cost_broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("102"),
        quantity=Decimal("1"),
    )
    closed = cost_broker.on_bar(_bar("BTC-USDT", 100, 100, 98.5, 99))
    assert len(closed) == 1
    assert closed[0].exit_reason == "stop_loss"
    assert closed[0].pnl == pytest.approx(-1.398001, abs=1e-9)
    assert closed[0].fees == pytest.approx(0.199001, abs=1e-9)


def test_fee_only_no_slippage(tmp_path):
    """Только комиссия, без slippage: pnl = gross - 2*fee."""
    broker = PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=Decimal("0.001"),
        slippage_pct=Decimal("0"),
    )
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
    # gross = 99 - 100 = -1; fees = 100*0.001 (вход) + 99*0.001 (выход) = 0.199
    assert closed[0].pnl == pytest.approx(-1.199, abs=1e-9)
    assert closed[0].fees == pytest.approx(0.199, abs=1e-9)


def test_slippage_on_short(cost_broker):
    """Short: slippage ухудшает и вход, и выход."""
    cost_broker.open_position(
        symbol="BTC-USDT",
        direction="short",
        entry_price=Decimal("100"),
        stop_loss=Decimal("101"),
        take_profit=Decimal("97"),
        quantity=Decimal("1"),
    )
    closed = cost_broker.on_bar(_bar("BTC-USDT", 100.5, 101.2, 100.5, 101))
    assert len(closed) == 1
    assert closed[0].exit_reason == "stop_loss"
    # fill = 100*0.999 = 99.9; exit_fill = 101*1.001 = 101.101
    # gross = 99.9 - 101.101 = -1.201
    # fees = 99.9*0.001 + 101.101*0.001 = 0.201001
    assert closed[0].pnl == pytest.approx(-1.402001, abs=1e-9)


def test_partial_tps_share_entry_fee_proportionally(cost_broker):
    """Частичный тейк закрывает часть объёма: комиссия входа делится
    пропорционально закрытой части."""
    cost_broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("101"),  # tp1 = 101
        quantity=Decimal("1"),
    )
    closed = cost_broker.on_bar(_bar("BTC-USDT", 100.5, 101.2, 100, 101))
    tp1 = [c for c in closed if c.exit_reason == "tp1"]
    assert len(tp1) == 1
    # TP1 закрывает 0.5 объёма.
    assert tp1[0].quantity == pytest.approx(0.5)
    assert tp1[0].fees > 0
    # Комиссия входа на 0.5 объёма = 100.1*0.001*0.5
    assert tp1[0].fees == pytest.approx(100.1 * 0.001 * 0.5 + 101 * 0.999 * 0.001 * 0.5, abs=1e-9)


def test_legacy_positions_without_fill_price(tmp_path):
    """Позиции, сохранённые до введения модели издержек, восстанавливаются
    с fill_price = entry_price и нулевой входной комиссией."""
    state = tmp_path / "pos.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    import json

    state.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "id": "x1",
                        "symbol": "BTC-USDT",
                        "direction": "long",
                        "entry_price": "100",
                        "quantity": "1",
                        "stop_loss": "99",
                        "take_profits": ["101"],
                        "tp_filled": [False],
                        "tp_fractions": [0.5, 0.3, 0.2],
                        "initial_quantity": "1",
                        "trailing_activated": False,
                        "trailing_distance": None,
                        "highest_price": None,
                        "lowest_price": None,
                        "strategy": "test",
                        "opened_at": 1,
                        "notes": {},
                    }
                ],
                "realized_pnl": "0",
                "initial_capital": "1000",
            }
        ),
        encoding="utf-8",
    )
    broker = PaperBroker(
        state_path=state,
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=Decimal("0.001"),
        slippage_pct=Decimal("0.001"),
    )
    assert len(broker.positions) == 1
    assert broker.positions[0].fill_price == Decimal("100")
    assert broker.positions[0].entry_fee_per_unit == Decimal("0")


def test_close_positions_closes_all_for_symbol(broker):
    broker.open_position(
        symbol="BTC/USDT",
        direction="long",
        entry_price=Decimal("60000"),
        stop_loss=Decimal("57000"),
        take_profit=Decimal("0"),
        quantity=Decimal("0.01"),
        strategy="ts_momentum",
    )
    broker.open_position(
        symbol="ETH/USDT",
        direction="long",
        entry_price=Decimal("3000"),
        stop_loss=Decimal("2850"),
        take_profit=Decimal("0"),
        quantity=Decimal("0.1"),
        strategy="ts_momentum",
    )
    closed = broker.close_positions("BTC/USDT", Decimal("61000"), "flip")
    assert len(closed) == 1
    assert closed[0].exit_reason == "flip"
    assert closed[0].pnl > 0
    # ETH-позиция не тронута.
    assert len(broker.positions) == 1
    assert broker.positions[0].symbol == "ETH/USDT"
