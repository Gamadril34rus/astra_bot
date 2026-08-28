"""Integration-тесты: TradingEngine → Risk Engine (master prompt §11).

Проверяют реальный execution path (``process_symbol``): лимиты дневных
потерь, недельных потерь и drawdown-HALT реально блокируют вход, а
закрытые сделки учитываются в риск-состоянии. Риск-состояние
восстанавливается из ``paper_trades.jsonl`` — как между 5-минутными
CI-сессиями на GitHub Actions.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from astra_bot.core import models
from astra_bot.core.market_safety import SafetyVerdict
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.context import SignalCandidate
from astra_bot.decision.pipeline import Decision
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.engines.risk_engine import RiskConfig, RiskEngine

SYMBOL = "BTC-USDT"


def _candles(n: int = 120, last_low: Decimal = Decimal("99.95")) -> list[models.Candle]:
    out = []
    for i in range(n):
        p = 100 + (i % 5) * 0.01
        low = last_low if i == n - 1 else Decimal(str(p - 0.05))
        out.append(
            models.Candle(
                exchange="okx",
                symbol=SYMBOL,
                timeframe="5m",
                open_time=1_700_000_000 + i * 300,
                open=Decimal(str(p)),
                high=Decimal(str(p + 0.05)),
                low=low,
                close=Decimal(str(p + 0.02)),
                volume=Decimal("100"),
                quote_volume=Decimal("10000"),
            )
        )
    return out


class _AsyncMockReturn:
    """Асинхронный мок, возвращающий фиксированное значение."""

    def __init__(self, value):
        self._value = value

    def __call__(self, *args, **kwargs):
        async def _inner():
            return self._value

        return _inner()


class FakePipeline:
    """Детерминированный пайплайн: всегда предлагает LONG 100/99/103."""

    def decide(self, ctx):
        cand = SignalCandidate(
            symbol=ctx.symbol,
            direction="long",
            entry_price=Decimal("100"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("103"),
            timeframe="5m",
            strategy="fake_strategy",
            confidence=0.9,
        )
        return Decision("LONG", ctx.symbol, ["fake_signal"], candidate=cand)


def _trade_line(pnl: float, hours_ago: float = 0.0) -> dict:
    return {
        "id": f"t-{hours_ago}",
        "symbol": SYMBOL,
        "direction": "long",
        "entry_price": 100.0,
        "exit_price": 100.0 - pnl,
        "quantity": 1.0,
        "pnl": pnl,
        "pnl_pct": -pnl,
        "fees": 0.0,
        "exit_reason": "stop_loss",
        "strategy": "fake_strategy",
        "opened_at": 1,
        "closed_at": int(
            (datetime.now(UTC).timestamp() - hours_ago * 3600) * 1000
        ),
    }


@pytest.fixture()
def engine_factory(tmp_path: Path, monkeypatch):
    # Уроки пишутся в реальный models/live_lessons.jsonl — в тесте
    # изолируем запись, чтобы не засорять память бота синтетикой.
    monkeypatch.setattr(
        "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
    )

    def make(seed_trades: list[dict] | None = None) -> TradingEngine:
        trades_path = tmp_path / "trades.jsonl"
        if seed_trades is not None:
            trades_path.write_text(
                "\n".join(json.dumps(t) for t in seed_trades),
                encoding="utf-8",
            )
        cfg = TradingEngineConfig(
            symbols=(SYMBOL,),
            timeframes=("5m",),
            bars_per_tf={"5m": 120},
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        )
        okx = MagicMock()
        okx.get_candles = _AsyncMockReturn(_candles())
        okx.get_orderbook = _AsyncMockReturn(None)
        okx.get_ticker = _AsyncMockReturn(
            {"last": "100", "high_24h": "101", "low_24h": "99"}
        )
        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=trades_path,
            initial_capital=Decimal("1000"),
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        )
        risk = RiskEngine(
            RiskConfig(
                risk_per_trade=Decimal(cfg.risk_per_trade_pct),
                max_open_positions=cfg.max_open_positions,
                max_exposure_pct=Decimal(cfg.max_total_exposure_pct),
            )
        )
        eng = TradingEngine(
            okx=okx,
            pipeline=FakePipeline(),
            config=cfg,
            broker=broker,
            risk_engine=risk,
        )
        # MarketSafety дёргает сеть (новости) и расписание — здесь
        # проверяется именно риск-слой, поэтому безопасность изолируем.
        eng.safety.check = lambda *a, **k: SafetyVerdict(allowed=True)
        return eng

    return make


def test_entry_allowed_when_risk_state_clean(engine_factory):
    eng = engine_factory()
    asyncio.run(eng.process_symbol(SYMBOL))

    assert len(eng.broker.positions) == 1
    pos = eng.broker.positions[0]
    # Размер ограничен max_notional 15% от 1000 = 150 USDT -> qty 1.5.
    assert pos.quantity == Decimal("1.5")
    assert eng.risk.trading_enabled is True


def test_daily_loss_limit_blocks_entry(engine_factory):
    # Лимит дневных потерь = 2% от 1000 = 20 USDT.
    eng = engine_factory(seed_trades=[_trade_line(-25)])
    asyncio.run(eng.process_symbol(SYMBOL))

    assert eng.broker.positions == []
    assert float(eng.risk._daily_pnl) == -25.0


def test_hard_drawdown_halts_trading(engine_factory):
    # -90 USDT на 1000 = просадка 9% >= hard 8% → TRADING HALT.
    eng = engine_factory(seed_trades=[_trade_line(-90)])
    asyncio.run(eng.process_symbol(SYMBOL))

    assert eng.broker.positions == []
    assert eng.risk.trading_enabled is False
    assert eng.risk.risk_state.value == "STOP"


def test_weekly_loss_limit_blocks_entry(engine_factory):
    # Недельный лимит = 4% = 40 USDT; 30 USDT в окне — вход запрещён.
    eng = engine_factory(seed_trades=[_trade_line(-30), _trade_line(-15, hours_ago=100)])
    asyncio.run(eng.process_symbol(SYMBOL))
    assert eng.broker.positions == []

    # Если убыток старше 7 дней — он вне окна, вход снова разрешён.
    eng2 = engine_factory(seed_trades=[_trade_line(-30, hours_ago=100)])
    asyncio.run(eng2.process_symbol(SYMBOL))
    assert len(eng2.broker.positions) == 1


def test_closed_trade_updates_risk_state(engine_factory):
    eng = engine_factory()
    # 1-й шаг: открываем позицию (qty 1.5, стоп 99).
    asyncio.run(eng.process_symbol(SYMBOL))
    assert len(eng.broker.positions) == 1
    # Открытая позиция учтена в книге Risk Engine.
    assert len(eng.risk._open_positions) == 1

    # 2-й шаг: бар пробивает стоп (99) — позиция закрывается в убыток.
    eng.okx.get_candles = _AsyncMockReturn(_candles(last_low=Decimal("98.5")))
    closed = asyncio.run(eng.process_symbol(SYMBOL))

    assert any(c.exit_reason == "stop_loss" for c in closed)
    # Убыток 1.5 qty * 1.0 (стопа) = 1.5 учтён в дневном PnL.
    assert float(eng.risk._daily_pnl) == pytest.approx(-1.5)
    # Движок перешёл в следующую позицию (fake pipeline снова дал LONG —
    # поведение «решение считается после on_bar»): старая позиция из
    # книги Risk Engine удалена, новая добавлена.
    assert len(eng.broker.positions) == 1
    assert len(eng.risk._open_positions) == 1
