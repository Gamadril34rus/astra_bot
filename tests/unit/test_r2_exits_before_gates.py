"""R2-03/R2-04 (внешний аудит 28.09): выходы раньше входных лимитов,
карточки выходов не зависят от SymbolLossGuard.

R2-04: is_paused и дневной лимит стояли ВЫШЕ всех обязательных выходов
(BTC-panic, adjust_stops, check_exits, ликвидации, forced-правила) —
запаузенный символ жил до 4ч без стопов. Фикс: ранние return заменены
на _process_exits_only (выходы работают, входы нет).

R2-03: карточки выходов + symbol_guard.record() были в одной ветке/одном
try: guard=None убирал ВСЕ карточки, исключение на k-й сделке роняло
остаток батча. Фикс: независимые try на итерацию.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.core import models
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.trading_engine import (
    Decision,
    TradingEngine,
    TradingEngineConfig,
)
from astra_bot.engines.risk_engine import RiskConfig, RiskEngine


def _candles(last_low: str = "98.5") -> list[models.Candle]:
    bars = [
        models.Candle(
            exchange="test", symbol="BTC-USDT", timeframe="5m",
            open_time=1_700_000_000_000 + i * 300_000,
            open=Decimal("100"), high=Decimal("100.6"),
            low=Decimal("99.4"), close=Decimal("100.1"),
            volume=Decimal("10"), quote_volume=Decimal("1"),
        )
        for i in range(59)
    ]
    bars.append(
        models.Candle(
            exchange="test", symbol="BTC-USDT", timeframe="5m",
            open_time=1_700_000_000_000 + 59 * 300_000,
            open=Decimal("100.1"), high=Decimal("100.6"),
            low=Decimal(last_low), close=Decimal("100.1"),
            volume=Decimal("10"), quote_volume=Decimal("1"),
        )
    )
    return bars


class _AsyncList:
    """Асинхронный мок: любые args -> один и тот же список."""

    def __init__(self, value):
        self.value = value

    async def __call__(self, *args, **kwargs):
        return self.value


class _NoEntryPipe:
    """Пайплайн-шпион: считает вызовы decide, всегда NO_TRADE."""

    def __init__(self):
        self.calls = 0

    async def decide(self, ctx):
        self.calls += 1
        return Decision("NO_TRADE", ctx.symbol, ["r2test"], candidate=None)


def _engine(tmp_path, monkeypatch):
    """Минимальный движок (схема тестов leverage): мок-фид, tmp-брокер."""
    from astra_bot.core.market_safety import SafetyVerdict

    monkeypatch.setattr(
        "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
    )
    monkeypatch.setattr(
        "astra_bot.data.state_manager.get_state_manager",
        lambda: MagicMock(
            save_trades=lambda x: 0, load_state=dict, save_state=lambda x: None
        ),
    )
    cfg = TradingEngineConfig(
        symbols=("BTC-USDT",),
        timeframes=("5m",),
        bars_per_tf={"5m": 120},
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
        stats_path=str(tmp_path / "stats.json"),
        no_trade_observations_path=str(tmp_path / "obs.jsonl"),
        no_trade_outcomes_path=str(tmp_path / "outcomes.json"),
        hypotheses_path=str(tmp_path / "hyp.json"),
    )
    pipe = _NoEntryPipe()
    feed = MagicMock()
    feed.get_candles = _AsyncList(_candles())
    feed.get_orderbook = _AsyncList(None)
    feed.get_ticker = _AsyncList(
        {"last": "100", "high_24h": "101", "low_24h": "99"}
    )
    eng = TradingEngine(
        exchange=feed,
        pipeline=pipe,
        config=cfg,
        broker=PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("1000"),
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        ),
        risk_engine=RiskEngine(
            RiskConfig(
                risk_per_trade=Decimal("0.01"),
                max_open_positions=3,
                max_exposure_pct=Decimal("0.30"),
            )
        ),
    )
    eng.safety.check = lambda *a, **k: SafetyVerdict(allowed=True)
    eng.symbol_guard = None
    return eng, pipe


def _open_stop_position(eng: TradingEngine) -> None:
    eng.broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("102"),
        quantity=Decimal("1"),
    )


class TestExitsBeforeGates:
    def test_paused_symbol_still_closes_stop(self, tmp_path, monkeypatch):
        """R2-04: пауза SymbolLossGuard НЕ отменяет стопы."""
        eng, pipe = _engine(tmp_path, monkeypatch)
        eng.symbol_guard = SimpleNamespace(is_paused=lambda s: True)
        _open_stop_position(eng)
        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert [t.exit_reason for t in closed] == ["stop_loss"]
        assert not eng.broker.positions
        assert pipe.calls == 0  # решение/входы не выполнялись

    def test_daily_limit_still_closes_stop(self, tmp_path, monkeypatch):
        """R2-04: дневной лимит НЕ отменяет стопы."""
        eng, pipe = _engine(tmp_path, monkeypatch)
        # Фикстура под master: счётчик обнуляется, если дата дня не совпала.
        from datetime import UTC, datetime
        eng._trades_today_date = datetime.now(UTC).strftime("%Y-%m-%d")
        eng._daily_trade_count = 99
        _open_stop_position(eng)
        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert [t.exit_reason for t in closed] == ["stop_loss"]
        assert not eng.broker.positions
        assert pipe.calls == 0

    def test_no_limits_normal_path_unchanged(self, tmp_path, monkeypatch):
        """Без паузы/лимита основной контур жив: выходы + решение."""
        eng, pipe = _engine(tmp_path, monkeypatch)
        _open_stop_position(eng)
        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert [t.exit_reason for t in closed] == ["stop_loss"]
        assert pipe.calls == 1  # decision вызывался как раньше


def _trade(i=1, sym="BTC-USDT", reason="take_profit", pnl=1.0):
    return SimpleNamespace(
        id=f"t{i}", symbol=sym, direction="long",
        entry_price=100.0, exit_price=101.0, quantity=0.5, pnl=pnl,
        pnl_pct=0.5, fees=0.01, r_multiple=0.5, mfe_r=0.6, mae_r=0.0,
        regime="UNKNOWN", regime_axes="", timeframe="5m",
        exit_reason=reason, strategy="s", opened_at=1, closed_at=2,
    )


class TestCardsIndependentOfGuard:
    def test_cards_sent_without_guard(self, tmp_path, monkeypatch):
        """R2-03: guard=None больше не прячет карточки выходов."""
        eng, _ = _engine(tmp_path, monkeypatch)
        sent = []
        eng._trade_cards_enabled = True
        eng._notifier = lambda text, severity="info": sent.append(text)
        eng._record_closed([_trade()])
        assert any("Выход" in t for t in sent)

    def test_guard_failure_does_not_kill_cards(self, tmp_path, monkeypatch):
        """R2-03: сбой record() на одной сделке не роняет остальные."""
        eng, _ = _engine(tmp_path, monkeypatch)
        recs = []

        def _rec(sym, pnl):
            recs.append(sym)
            if sym == "ETH-USDT":
                raise RuntimeError("boom")

        eng.symbol_guard = SimpleNamespace(record=_rec)
        calls = []
        eng._trade_cards_enabled = True
        eng._notifier = lambda text, severity="info": calls.append(text)
        eng._record_closed([_trade(1, "BTC-USDT"), _trade(2, "ETH-USDT")])
        assert recs == ["BTC-USDT", "ETH-USDT"]
        assert len(calls) == 2  # на мастере до фикса здесь 1 — регрессия

    def test_guard_records_losses(self, tmp_path, monkeypatch):
        """guard.record вызывается по каждой сделке с непустым символом."""
        eng, _ = _engine(tmp_path, monkeypatch)
        recs = []
        eng.symbol_guard = SimpleNamespace(
            record=lambda s, p: recs.append((s, p))
        )
        eng._trade_cards_enabled = True
        eng._notifier = lambda text, severity="info": None
        eng._record_closed([_trade(1, "BTC-USDT"), _trade(2, "")])
        assert recs == [("BTC-USDT", 1.0)]
