"""Фаза-0 тени паттерн-выходов: только журнал, на решения не влияет."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.core import models
from astra_bot.core.market_safety import SafetyVerdict
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.pattern_exit_shadow import (
    PatternExitShadow,
    reversal_pattern,
)
from astra_bot.decision.pipeline import Decision
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.engines.risk_engine import RiskConfig, RiskEngine

SYMBOL = "BTC-USDT"


@dataclass
class C:
    open: float
    high: float
    low: float
    close: float
    open_time: int = 1_700_000_000


def _pos(**kw):
    base = dict(
        id="p1", symbol=SYMBOL, direction="long", strategy="s",
        entry_price=Decimal("100"), stop_loss=Decimal("99"),
        risk_distance=Decimal("1"), bars_held=3, tp_filled=[False, False],
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ------------------------------------------------------- детектор паттернов
def test_bearish_engulfing():
    prev = C(open=99.9, high=100.15, low=99.85, close=100.1)
    cur = C(open=100.15, high=100.2, low=99.8, close=99.85)
    assert reversal_pattern([prev, cur]) == "bearish_engulfing"


def test_shooting_star():
    prev = C(open=99.9, high=100.1, low=99.8, close=100.0)
    cur = C(open=100.0, high=101.0, low=99.95, close=99.96)
    assert reversal_pattern([prev, cur]) == "shooting_star"


def test_bullish_engulfing_and_hammer():
    prev = C(open=100.1, high=100.15, low=99.85, close=99.9)
    cur = C(open=99.85, high=100.2, low=99.8, close=100.15)
    assert reversal_pattern([prev, cur]) == "bullish_engulfing"
    prev2 = C(open=100.1, high=100.2, low=100.0, close=100.0)
    cur2 = C(open=100.0, high=100.05, low=99.0, close=100.04)
    assert reversal_pattern([prev2, cur2]) == "hammer"


def test_no_pattern_and_fail_open():
    flat = C(open=100.0, high=100.05, low=99.95, close=100.02)
    assert reversal_pattern([flat, flat]) is None
    assert reversal_pattern([flat]) is None, "меньше 2 баров — молчим"
    assert reversal_pattern([]) is None
    doji = C(open=100.0, high=100.0, low=100.0, close=100.0)
    assert reversal_pattern([flat, doji]) is None, "вырожденный диапазон"
    broken = SimpleNamespace(open_time=1)
    assert reversal_pattern([flat, broken]) is None, "битые поля"


# ------------------------------------------------------- журнал и дедуп
def test_observe_writes_row_and_dedups(tmp_path):
    shadow = PatternExitShadow(tmp_path / "shadow.jsonl")
    prev = C(open=99.9, high=100.15, low=99.85, close=100.1, open_time=100)
    cur = C(open=100.15, high=100.2, low=99.8, close=99.85, open_time=200)
    forming = C(open=99.85, high=99.9, low=99.6, close=99.7, open_time=300)
    assert shadow.observe(position=_pos(), candles=[prev, cur, forming], price=99.7) is True
    assert shadow.observe(position=_pos(), candles=[prev, cur, forming], price=99.7) is False
    lines = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["pattern"] == "bearish_engulfing"
    assert row["would_exit"] is True, "лонг + медвежий паттерн"
    assert row["bar_time"] == 200, "последний ЗАКРЫТЫЙ бар, не формирующийся"
    assert row["r_now"] == -0.3
    assert row["partial_done"] is False


def test_observe_short_mirror_and_partial_flag(tmp_path):
    shadow = PatternExitShadow(tmp_path / "shadow.jsonl")
    prev = C(open=100.1, high=100.15, low=99.85, close=99.9, open_time=100)
    cur = C(open=99.85, high=100.2, low=99.8, close=100.15, open_time=200)
    forming = C(open=100.15, high=100.2, low=100.0, close=100.1, open_time=300)
    pos = _pos(direction="short", entry_price=Decimal("100"),
               stop_loss=Decimal("101"), tp_filled=[True, False])
    assert shadow.observe(position=pos, candles=[prev, cur, forming], price=100.1) is True
    row = json.loads((tmp_path / "shadow.jsonl").read_text(encoding="utf-8"))
    assert row["pattern"] == "bullish_engulfing"
    assert row["would_exit"] is True, "шорт + бычий паттерн"
    assert row["partial_done"] is True


def test_observe_fail_open_few_closed_bars(tmp_path):
    shadow = PatternExitShadow(tmp_path / "shadow.jsonl")
    only_forming = C(open=100.0, high=100.1, low=99.9, close=100.0)
    assert shadow.observe(position=_pos(), candles=[only_forming], price=100.0) is False
    assert not (tmp_path / "shadow.jsonl").exists(), "файл не создан — молчим"


# ------------------------------------------------------- хук движка (hermetic)
class _AsyncMockReturn:
    def __init__(self, value):
        self._value = value

    def __call__(self, *args, **kwargs):
        async def _inner():
            return self._value

        return _inner()


class _NoTradePipeline:
    async def decide(self, ctx):
        return Decision("NO_TRADE", ctx.symbol, ["shadow_test"])


def _feed_candles() -> list[models.Candle]:
    """10 баров 5m: последний закрытый — медвежье поглощение."""
    bars = [
        # (open, high, low, close)
        (100.0, 100.05, 99.95, 100.0),
        (100.0, 100.05, 99.95, 100.01),
        (100.01, 100.06, 99.96, 100.0),
        (100.0, 100.05, 99.95, 99.99),
        (99.99, 100.04, 99.94, 100.0),
        (100.0, 100.05, 99.95, 100.0),
        (100.0, 100.05, 99.95, 100.0),
        (99.9, 100.15, 99.85, 100.1),  # prev бычий
        (100.15, 100.2, 99.8, 99.85),  # последний закрытый — поглощение
        (99.85, 99.9, 99.6, 99.7),  # формирующийся (стоп 99 не задет)
    ]
    return [
        models.Candle(
            exchange="feed", symbol=SYMBOL, timeframe="5m",
            open_time=1_700_000_000 + i * 300,
            open=Decimal(str(o)), high=Decimal(str(h)),
            low=Decimal(str(l)), close=Decimal(str(c)),
            volume=Decimal("100"), quote_volume=Decimal("10000"),
        )
        for i, (o, h, l, c) in enumerate(bars)
    ]


def _engine(tmp_path: Path, monkeypatch) -> TradingEngine:
    monkeypatch.setattr(
        "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
    )
    monkeypatch.setattr(
        "astra_bot.data.state_manager.get_state_manager",
        lambda: MagicMock(save_trades=lambda x: 0, load_state=dict, save_state=lambda x: None),
    )
    cfg = TradingEngineConfig(
        symbols=(SYMBOL,),
        timeframes=("5m",),
        bars_per_tf={"5m": 120},
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
        stats_path=str(tmp_path / "strategy_stats.json"),
        no_trade_observations_path=str(tmp_path / "no_trade.jsonl"),
        no_trade_outcomes_path=str(tmp_path / "no_trade_outcomes.json"),
        hypotheses_path=str(tmp_path / "hypotheses.json"),
        pattern_exit_shadow_path=str(tmp_path / "pattern_exit_shadow.jsonl"),
    )
    feed = MagicMock()
    feed.get_candles = _AsyncMockReturn(_feed_candles())
    feed.get_orderbook = _AsyncMockReturn(None)
    feed.get_ticker = _AsyncMockReturn(
        {"last": "99.7", "high_24h": "101", "low_24h": "99"}
    )
    broker = PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
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
        exchange=feed, pipeline=_NoTradePipeline(), config=cfg,
        broker=broker, risk_engine=risk,
    )
    eng.safety.check = lambda *a, **k: SafetyVerdict(allowed=True)
    return eng


def test_engine_hook_observes_survivor_without_affecting(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    assert eng.pattern_exit_shadow is not None
    eng.broker.open_position(
        symbol=SYMBOL, direction="long", entry_price=Decimal("100"),
        stop_loss=Decimal("99"), take_profit=Decimal("105"),
        quantity=Decimal("1"), strategy="s", timeframe="5m",
        take_levels=[Decimal("101"), Decimal("102.3")],
        tp_fractions=[0.3, 0.7],
    )
    closed = asyncio.run(eng.process_symbol(SYMBOL))
    assert closed == [], "тень никого не закрывает"
    assert len(eng.broker.positions) == 1, "позиция пережила бар"
    lines = (tmp_path / "pattern_exit_shadow.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["pattern"] == "bearish_engulfing"
    assert row["would_exit"] is True
    # Второй прогон того же бара — дедуп, решений по-прежнему нет.
    closed2 = asyncio.run(eng.process_symbol(SYMBOL))
    assert closed2 == []
    assert len(eng.broker.positions) == 1
    lines2 = (tmp_path / "pattern_exit_shadow.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines2) == 1


def test_engine_shadow_disabled_by_flag(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    eng.config.pattern_exit_shadow_enabled = False
    # Флаг читается в конструкторе: пересоздаём минимальный движок через
    # тот же конфиг-объект нельзя — проверяем контракт напрямую.
    from astra_bot.decision.trading_engine import TradingEngine as TE

    monkeypatch.setattr(
        "astra_bot.data.state_manager.get_state_manager",
        lambda: MagicMock(save_trades=lambda x: 0, load_state=dict, save_state=lambda x: None),
    )
    cfg = TradingEngineConfig(
        symbols=(SYMBOL,), stats_path=str(tmp_path / "s2.json"),
        pattern_exit_shadow_enabled=False,
    )
    eng2 = TE(exchange=MagicMock(), pipeline=_NoTradePipeline(), config=cfg)
    assert eng2.pattern_exit_shadow is None
