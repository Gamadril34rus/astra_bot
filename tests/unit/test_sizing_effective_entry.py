"""Аудит A2: сайзинг и риск-гейт — от ФАКТИЧЕСКОЙ цены входа.

Решение владельца (13.09.2026): выравнивание с исполнением PaperBroker
(``fill = сигнальная цена ± slippage``) и с бэктестером (PR #73).
До фикса размер считался от сигнальной цены, а исполнялся по slipped-цене,
поэтому фактический риск на стопе превышал бюджет на ``slip / d``
(d — относительная дистанция стопа).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.engines.cost_model import bingx_perps_cost_model


def _broker(tmp_path, capital: Decimal = Decimal("10000")) -> PaperBroker:
    return PaperBroker(
        state_path=tmp_path / "state.json",
        trades_path=tmp_path / "trades.jsonl",
        initial_capital=capital,
        cost_model=bingx_perps_cost_model(),   # taker 0.05%, slippage 0.1%
    )


def _engine(broker: PaperBroker, tmp_path) -> TradingEngine:
    engine = TradingEngine(
        exchange=MagicMock(),
        pipeline=MagicMock(),
        broker=broker,
        config=TradingEngineConfig(halt_alerts_path=str(tmp_path / "halt.json")),
    )
    engine.risk.set_capital(broker.equity, broker.equity)
    return engine


class _Cand:
    def __init__(self, entry: str, stop: str, take: str = "110") -> None:
        self.entry_price = Decimal(entry)
        self.stop_loss = Decimal(stop)
        self.take_profit = Decimal(take)
        self.strategy = "a2"


def test_effective_entry_matches_broker_fill(tmp_path):
    engine = _engine(_broker(tmp_path), tmp_path)
    assert engine._effective_entry_price(Decimal("100"), "long") == Decimal("100.100")
    assert engine._effective_entry_price(Decimal("100"), "short") == Decimal("99.900")


def test_sizing_on_effective_entry_keeps_risk_budget(tmp_path):
    """Риск по фактическому fill ≤ бюджет; прежний сайзинг давал slip/d сверху."""
    broker = _broker(tmp_path)
    engine = _engine(broker, tmp_path)
    entry, stop = Decimal("100"), Decimal("90")          # d = 10%
    budget = broker.equity * engine.config.risk_per_trade_pct  # 1% = 100

    fill = engine._effective_entry_price(entry, "long")   # 100.1
    size_fixed = engine._position_size(broker.equity, fill, stop)
    size_old = engine._position_size(broker.equity, entry, stop)

    risk_fixed = size_fixed * (fill - stop)
    risk_old = size_old * (fill - stop)
    assert risk_fixed <= budget                           # бюджет соблюдён
    assert size_fixed < size_old
    # Множители (Kelly/ML/ATR) у обоих вызовов одинаковые — размеры
    # отличаются ровно на (d+slip)/d, т.е. перерасход = slip/d от риска.
    # rel=1e-5: сайзер квантует размер до 1e-6, отсюда ~1e-7 погрешность.
    assert float(size_old / size_fixed) == pytest.approx(
        float((fill - stop) / (entry - stop)), rel=1e-5
    )
    assert float((risk_old - risk_fixed) / risk_fixed) == pytest.approx(
        float((fill - entry) / (entry - stop)), rel=1e-5
    )


def test_risk_gate_sees_effective_entry_and_adjusts(tmp_path):
    """Risk Engine: тот же стоп-зазор, что и рынок → размер урезается."""
    broker = _broker(tmp_path)
    engine = _engine(broker, tmp_path)
    cand = _Cand("100", "90")
    size = Decimal("10")                                   # риск 100 = ровно бюджет

    same = engine._risk_check_and_adjust("BTC-USDT", "long", cand, size,
                                         entry_price=Decimal("100"))
    assert same == size                                    # по сигнальной — «влезает»

    adjusted = engine._risk_check_and_adjust("BTC-USDT", "long", cand, size,
                                             entry_price=Decimal("100.1"))
    assert adjusted is not None and adjusted < size        # по фактической — режется
    assert adjusted * (Decimal("100.1") - Decimal("90")) <= Decimal("100.000001")


def test_no_cost_model_falls_back_to_flat_slippage(tmp_path):
    """Legacy-режим (без CostModel): slippage берётся из брокера."""
    broker = PaperBroker(
        state_path=tmp_path / "s.json",
        trades_path=tmp_path / "t.jsonl",
        initial_capital=Decimal("1000"),
        fee_pct=Decimal("0.0005"),
        slippage_pct=Decimal("0.002"),
    )
    engine = _engine(broker, tmp_path)
    assert engine._effective_entry_price(Decimal("100"), "long") == Decimal("100.2")
    assert engine._effective_entry_price(Decimal("100"), "short") == Decimal("99.8")
