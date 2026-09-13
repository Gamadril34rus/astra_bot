"""Частичная фиксация 2-уровневого тейка плана (решение владельца 13.09.2026).

Контракт: уровни [1.0R x 30%, кламп 2.0-2.3R x 70%]; после первой частички
стоп в сырой вход через apply_tighter (только подтягивание, D1 лучше —
остаётся D1); в статистике позиция с частичкой — ОДИН sample с суммарным
R (иначе kill-switch задвоит n); tp_filled/tp_fractions переживают рестарт.
Бэктестер однотейковый (лимитация в docs/BACKTEST_VS_PAPER_SEMANTICS.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.exit_plan import (
    PLAN_TP_FRACTIONS,
    apply_tighter,
    plan_take_levels,
)
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0
    symbol: str = "BTC-USDT"


def _broker(tmp_path, **kw) -> PaperBroker:
    params = dict(
        state_path=tmp_path / "paper_positions.json",
        trades_path=tmp_path / "paper_trades.jsonl",
        initial_capital=Decimal("10000"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )
    params.update(kw)
    return PaperBroker(**params)


def _engine(tmp_path, broker) -> TradingEngine:
    return TradingEngine(
        exchange=MagicMock(),
        broker=broker,
        config=TradingEngineConfig(
            stats_path=str(tmp_path / "strategy_stats.json"),
            no_trade_observations_path=str(tmp_path / "no_trade.jsonl"),
            no_trade_outcomes_path=str(tmp_path / "no_trade_outcomes.json"),
            hypotheses_path=str(tmp_path / "hypotheses.json"),
        ),
    )


def _open_two_level(broker, direction="long", qty="10", strategy="test_partial"):
    if direction == "long":
        entry, stop, take = Decimal("100"), Decimal("99"), Decimal("105")
    else:
        entry, stop, take = Decimal("100"), Decimal("101"), Decimal("95")
    tp1, tail = plan_take_levels(entry, stop, take, direction, 2.0, 2.3)
    return broker.open_position(
        symbol="BTC-USDT", direction=direction,
        entry_price=entry, stop_loss=stop, take_profit=take,
        quantity=Decimal(qty), strategy=strategy, timeframe="1h",
        take_levels=[tp1, tail], tp_fractions=list(PLAN_TP_FRACTIONS),
    )


# ------------------------------------------------------- уровни плана (1.1)
def test_plan_take_levels_long():
    tp1, tail = plan_take_levels(
        Decimal("100"), Decimal("99"), Decimal("105"), "long", 2.0, 2.3
    )
    assert tp1 == Decimal("101"), "частичка ровно на 1R"
    assert tail == Decimal("102.3"), "хвост на клампе 2.3R (тейк 5R срезан)"


def test_plan_take_levels_short():
    tp1, tail = plan_take_levels(
        Decimal("100"), Decimal("101"), Decimal("95"), "short", 2.0, 2.3
    )
    assert tp1 == Decimal("99")
    assert tail == Decimal("97.7")


def test_plan_take_levels_clamp_edges():
    # Тейк 1.5R — хвост подтягивается к 2.0R; 2.1R — остаётся как есть.
    _, tail_low = plan_take_levels(
        Decimal("100"), Decimal("99"), Decimal("101.5"), "long", 2.0, 2.3
    )
    assert tail_low == Decimal("102.0")
    _, tail_mid = plan_take_levels(
        Decimal("100"), Decimal("99"), Decimal("102.1"), "long", 2.0, 2.3
    )
    assert tail_mid == Decimal("102.1")
    assert PLAN_TP_FRACTIONS == (0.3, 0.7)


def test_fractions_validation(tmp_path):
    b = _broker(tmp_path)
    with pytest.raises(ValueError):
        b.open_position(
            symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
            stop_loss=Decimal("99"), take_profit=Decimal("105"),
            quantity=Decimal("1"), take_levels=[Decimal("101"), Decimal("102")],
            tp_fractions=[0.3],  # длина не совпадает — fail-closed
        )
    with pytest.raises(ValueError):
        b.open_position(
            symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
            stop_loss=Decimal("99"), take_profit=Decimal("105"),
            quantity=Decimal("1"), take_levels=[Decimal("101"), Decimal("102")],
            tp_fractions=[0.3, 0.8],  # сумма > 100% — fail-closed
        )


# ------------------------------------------------- частичка + хвост (1.1/1.2)
def test_partial_30pct_then_tail_70pct(tmp_path):
    b = _broker(tmp_path)
    pos = _open_two_level(b)
    assert pos.take_profits == [Decimal("101"), Decimal("102.3")]
    assert pos.tp_fractions == [0.3, 0.7]
    # Бар бьёт tp1 (101), до хвоста (102.3) не дотягивает.
    closed1 = b.on_bar(Bar(open=100.5, high=101.5, low=100.2, close=101.2))
    assert [t.exit_reason for t in closed1] == ["tp1"]
    assert closed1[0].quantity == pytest.approx(3.0), "30% от 10"
    assert closed1[0].r_multiple == pytest.approx(1.0), "частичка ровно +1R"
    assert len(b.positions) == 1, "позиция жива после частички"
    assert b.positions[0].quantity == Decimal("10") - Decimal("3.0")
    assert b.positions[0].tp_filled == [True, False]
    # Стоп — в сырой вход через apply_tighter.
    assert b.positions[0].stop_loss == Decimal("100")
    # Бар бьёт хвост — остаток закрыт, позиция снята.
    closed2 = b.on_bar(Bar(open=101.2, high=102.5, low=101.0, close=102.4))
    assert [t.exit_reason for t in closed2] == ["tp2"]
    assert closed2[0].quantity == pytest.approx(7.0), "хвост 70%"
    assert closed2[0].r_multiple == pytest.approx(2.3)
    assert not b.positions
    # Ловушка: обе строки несут один id — частичка отличается объёмом.
    assert closed1[0].id == closed2[0].id
    assert closed1[0].quantity < closed1[0].quantity + closed2[0].quantity


def test_partial_short_and_breakeven(tmp_path):
    b = _broker(tmp_path)
    _open_two_level(b, direction="short")
    closed = b.on_bar(Bar(open=99.5, high=99.8, low=98.5, close=98.8))
    assert [t.exit_reason for t in closed] == ["tp1"]
    assert closed[0].r_multiple == pytest.approx(1.0)
    assert b.positions[0].stop_loss == Decimal("100")


def test_better_d1_stop_survives_partial(tmp_path):
    """D1-стоп лучше входа — после частички остаётся D1 (только подтягивание)."""
    b = _broker(tmp_path)
    pos = _open_two_level(b)
    pos.stop_loss = Decimal("100.5")  # D1 уже подтянул выше входа
    closed = b.on_bar(Bar(open=100.8, high=101.5, low=100.6, close=101.0))
    assert [t.exit_reason for t in closed] == ["tp1"]
    assert pos.stop_loss == Decimal("100.5"), "худший кандидат вход не подвинул D1"


def test_stop_invariant_rejects_worse_candidate(tmp_path):
    b = _broker(tmp_path)
    pos = _open_two_level(b)
    assert apply_tighter(pos, Decimal("98")) is False  # расширение — нет
    assert pos.stop_loss == Decimal("99")
    assert apply_tighter(pos, None) is False
    assert apply_tighter(pos, Decimal("99.5")) is True  # подтягивание — да
    assert pos.stop_loss == Decimal("99.5")


def test_single_level_behaves_as_before(tmp_path):
    """Без частички (один уровень, доли по умолчанию [1.0]) — как сейчас."""
    b = _broker(tmp_path)
    pos = b.open_position(
        symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
        stop_loss=Decimal("99"), take_profit=Decimal("105"),
        quantity=Decimal("10"), timeframe="1h",
        take_levels=[Decimal("102")],
    )
    assert pos.tp_fractions == [1.0]
    closed = b.on_bar(Bar(open=100.5, high=102.5, low=100.0, close=102.0))
    assert [t.exit_reason for t in closed] == ["tp1"]
    assert closed[0].quantity == pytest.approx(10.0), "весь объём одной строкой"
    assert not b.positions


def test_vol_expansion_closes_remainder(tmp_path):
    b = _broker(tmp_path)
    pos = _open_two_level(b)
    closed1 = b.on_bar(Bar(open=100.5, high=101.5, low=100.2, close=101.2))
    assert [t.exit_reason for t in closed1] == ["tp1"]
    trade = b.close_position(pos.id, Decimal("100.5"), "VOL_EXPANSION")
    assert trade is not None and trade.exit_reason == "VOL_EXPANSION"
    assert trade.quantity == pytest.approx(7.0), "закрыт остаток 70%"
    assert not b.positions


# ------------------------------------------------------- роллап в статистику
def test_rollup_one_sample_per_position(tmp_path, monkeypatch):
    """Две строки закрытия (tp1 + хвост) — ОДИН sample, R суммарный."""
    monkeypatch.setattr(
        "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
    )
    b = _broker(tmp_path)
    eng = _engine(tmp_path, b)
    _open_two_level(b)
    closed1 = b.on_bar(Bar(open=100.5, high=101.5, low=100.2, close=101.2))
    closed2 = b.on_bar(Bar(open=101.2, high=102.5, low=101.0, close=102.4))
    assert [t.exit_reason for t in closed1 + closed2] == ["tp1", "tp2"]
    eng._record_closed(closed1 + closed2)
    stats = json.loads((tmp_path / "strategy_stats.json").read_text(encoding="utf-8"))
    buckets = stats["buckets"]
    any_bucket = buckets["test_partial|ANY|1h"]
    assert any_bucket["sample_size"] == 1, "n+=1, а не 2 (иначе kill-switch задвоит)"
    # Суммарный R позиции: (3.0 + 16.1) / (1R x 10) = 1.91.
    assert any_bucket["sum_r"] == pytest.approx(1.91)
    assert any_bucket["wins"] == 1 and any_bucket["losses"] == 0


# ------------------------------------------------------- рестарт (1.4)
def test_tp_state_survives_restart(tmp_path):
    b = _broker(tmp_path)
    _open_two_level(b)
    b.on_bar(Bar(open=100.5, high=101.5, low=100.2, close=101.2))
    assert b.positions[0].tp_filled == [True, False]
    b.save()
    # Новый процесс: состояние из файла.
    b2 = PaperBroker(
        state_path=tmp_path / "paper_positions.json",
        trades_path=tmp_path / "paper_trades.jsonl",
        initial_capital=Decimal("10000"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )
    assert len(b2.positions) == 1
    assert b2.positions[0].tp_filled == [True, False]
    assert b2.positions[0].tp_fractions == [0.3, 0.7]
    assert b2.positions[0].stop_loss == Decimal("100"), "БУ пережил рестарт"
    # Хвост добивается после рестарта.
    closed = b2.on_bar(Bar(open=101.2, high=102.5, low=101.0, close=102.4))
    assert [t.exit_reason for t in closed] == ["tp2"]
    assert closed[0].quantity == pytest.approx(7.0)
    assert not b2.positions
