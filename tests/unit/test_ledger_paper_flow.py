"""Бэклог B2: двойной реестр честен на paper-потоке с частичным выходом.

Сценарий владельца: позиция с tp1-частичкой. Инварианты:

  - Σfee(ledger.replay) == Σ ClosedTrade.fees (вход + все выходы);
  - realized(replay) == PaperBroker.realized_pnl;
  - инвентарь после полного закрытия пуст (частичный выход пишет fill-строку
    с ``position_delta``);
  - ``event_id`` частичных строк стабильны → dedup не ломается;
  - комиссия учитывается ТОЛЬКО fee-строками (поле ``fee`` в order/fill —
    информационное, replay его не суммирует).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from astra_bot.core.ledger import TradeLedger, reset_ledger
from astra_bot.decision.broker import PaperBroker
from astra_bot.engines.cost_model import bingx_perps_cost_model


def _bar(close: str, high: str | None = None, low: str | None = None):
    c = Decimal(close)
    return SimpleNamespace(
        open=c,
        high=Decimal(high) if high else c,
        low=Decimal(low) if low else c,
        close=c,
        volume=Decimal("1"),
        open_time=1_700_000_000_000,
        symbol="BTC-USDT",
    )


def _broker(tmp_path, *, cost_model=None) -> PaperBroker:
    return PaperBroker(
        initial_capital=Decimal("1000"),
        state_path=tmp_path / "paper_positions.json",
        trades_path=tmp_path / "paper_trades.jsonl",
        cost_model=cost_model or bingx_perps_cost_model(),
    )


def _open(broker: PaperBroker):
    return broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),        # 1R = 5 → tp1 на 105
        take_profit=Decimal("110"),
        quantity=Decimal("1"),
        strategy="b2",
    )


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_partial_tp_ledger_matches_broker(tmp_path, monkeypatch):
    """tp1 (частичный) + закрытие остатка: ledger сходится с брокером."""
    path = tmp_path / "paper_ledger.jsonl"
    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(path))
    reset_ledger(path, initial_cash=Decimal("1000"))
    broker = _broker(tmp_path)
    pos = _open(broker)

    # tp1 = 105 (1R), фиксируем половину объёма.
    partials = broker.on_bar(_bar("105", high="105", low="100"))
    assert [t.exit_reason for t in partials] == ["tp1"]
    assert broker.positions[0].quantity == Decimal("0.5")

    # Остаток закрывается вручную (как финальный выход).
    rest = broker._close(broker.positions[0], Decimal("106"), "take_profit")

    broker_fee = sum(Decimal(str(t.fees)) for t in [*partials, rest])
    snapped = TradeLedger(path, initial_cash=Decimal("1000")).replay(
        initial_cash=Decimal("1000")
    )

    assert snapped.fees == broker_fee
    assert snapped.realized_pnl == broker.realized_pnl
    assert snapped.cash == Decimal("1000") + broker.realized_pnl
    # Частичный выход уменьшил инвентарь → после полного закрытия пусто.
    assert snapped.positions == {}

    rows = _rows(path)
    ids = {row["event_id"] for row in rows}
    assert f"fill:{pos.id}:partial:0" in ids
    assert f"fee:{pos.id}:partial:0" in ids
    assert f"fee:{pos.id}:open" in ids
    # Строки открытия остались на месте (совместимость с #71).
    assert f"order:{pos.id}:open" in ids and f"fill:{pos.id}:open" in ids
    # Комиссия в order/fill-строках не дублируется (только fee-строки).
    assert all(row["fee"] == "0" for row in rows if row["kind"] != "fee")


def test_partial_events_are_idempotent(tmp_path, monkeypatch):
    """Стабильные id частичных строк: повтор не пишет дубль."""
    path = tmp_path / "paper_ledger.jsonl"
    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(path))
    reset_ledger(path, initial_cash=Decimal("1000"))
    broker = _broker(tmp_path)
    pos = _open(broker)
    partials = broker.on_bar(_bar("105", high="105", low="100"))
    assert partials

    before = path.read_text(encoding="utf-8").count("\n")
    # Повторный вызов того же события (реплей/повторный тик) — no-op.
    broker._ledger(
        "fill",
        symbol=pos.symbol,
        side=pos.direction,
        qty=Decimal("0.5"),
        price=Decimal("105"),
        ref_id=pos.id,
        event_id=f"fill:{pos.id}:partial:0",
    )
    broker._ledger(
        "fee",
        symbol=pos.symbol,
        side=pos.direction,
        qty=Decimal("0.5"),
        price=Decimal("105"),
        fee=Decimal("0.0525"),
        ref_id=pos.id,
        event_id=f"fee:{pos.id}:partial:0",
    )
    assert path.read_text(encoding="utf-8").count("\n") == before
    snapped = TradeLedger(path, initial_cash=Decimal("1000")).replay(
        initial_cash=Decimal("1000")
    )
    assert snapped.events == before


def test_fill_row_fee_is_informational_only(tmp_path):
    """Контракт: replay считает комиссии только по kind='fee'."""
    ledger = TradeLedger(tmp_path / "l.jsonl", initial_cash=Decimal("1000"))
    ledger.record(
        "fill",
        symbol="BTC-USDT",
        side="long",
        qty="1",
        price="100",
        fee="2.5",              # информационное поле — НЕ учитывается
        cash_delta="0",
        position_delta="1",
    )
    assert ledger.replay(initial_cash=Decimal("1000")).fees == Decimal("0")

    ledger.record("fee", symbol="BTC-USDT", side="long", qty="1", fee="2.5")
    assert ledger.replay(initial_cash=Decimal("1000")).fees == Decimal("2.5")


def test_zero_cost_legacy_mode_writes_no_fee_rows(tmp_path, monkeypatch):
    """Legacy-режим нулевых издержек: fee-строк нет, replay.fees == 0."""
    path = tmp_path / "paper_ledger.jsonl"
    monkeypatch.setenv("ASTRA_LEDGER_PATH", str(path))
    reset_ledger(path, initial_cash=Decimal("1000"))
    broker = PaperBroker(
        initial_capital=Decimal("1000"),
        state_path=tmp_path / "paper_positions.json",
        trades_path=tmp_path / "paper_trades.jsonl",
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )
    assert broker.cost_model is None  # именно legacy-режим
    broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        quantity=Decimal("1"),
        strategy="b2",
    )
    broker._close(broker.positions[0], Decimal("96"), "stop_loss")
    rows = _rows(path)
    assert rows, "fill-строки должны быть записаны"
    assert all(row["kind"] != "fee" for row in rows)
    snapped = TradeLedger(path, initial_cash=Decimal("1000")).replay(
        initial_cash=Decimal("1000")
    )
    assert snapped.fees == Decimal("0")
