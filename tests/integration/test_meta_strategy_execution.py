"""Integration: реальный production execution path (TZ §34).

Маршрут TRADE:
    market data → feature engine → regime detector → strategy →
    meta strategy (EV per regime) → EV → risk engine → position sizing →
    paper broker → trade → lesson + stats by regime.

Маршрут NO_TRADE:
    market data → NO_TRADE (LOW_EV) → observation → future outcome →
    исходы в research memory, без дублей при повторной обработке.

Реальные объекты: DecisionPipeline (regime/feature/EV/liquidity/scorer),
ScalpStrategy, StrategyStatsStore, MetaStrategy, RiskEngine, PaperBroker.
Мок только внешний мир (OKX), как в production-раундах.
"""

from __future__ import annotations

import asyncio
import json
import time as _time
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.core.market_safety import SafetyVerdict
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.config import DecisionConfig
from astra_bot.decision.pipeline import DecisionPipeline
from astra_bot.decision.strategy_stats import StrategyStatsStore
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.strategies import ScalpStrategy

# Свежие бары (последние часы): старые timestamps не проходят
# pruning исходов (30 дней) и не отражают «живой» поток.
BASE_TS = int(_time.time() // 900 * 900) - 235 * 900
STEP = 900


def gen_candles(n: int = 230, tf: str = "5m") -> list[models.Candle]:
    """Плавный тренд вверх + откат + бычья свеча: ScalpStrategy срабатывает."""
    out = []
    price = 100.0
    for i in range(n):
        o = price
        if i < n - 4:
            c = o + 0.025
        elif i < n - 1:
            c = o - 0.06
        else:
            c = o + 0.05
        h = max(o, c) + 0.02
        lo = min(o, c) - 0.02
        out.append(
            models.Candle(
                exchange="okx",
                symbol="BTC-USDT",
                timeframe=tf,
                open_time=BASE_TS + i * STEP,
                open=Decimal(str(o)),
                high=Decimal(str(h)),
                low=Decimal(str(lo)),
                close=Decimal(str(c)),
                volume=Decimal("100"),
                quote_volume=Decimal("10000"),
            )
        )
        price = c
    return out


def stop_hit_bar(prev: models.Candle, stop: Decimal) -> models.Candle:
    """Бара, пробивающий стоп вниз (для закрытия позиции)."""
    o = float(prev.close)
    c = float(stop) - 0.1
    lo = float(stop) - 0.5
    h = o + 0.2
    return models.Candle(
        exchange="okx",
        symbol="BTC-USDT",
        timeframe="5m",
        open_time=prev.open_time + STEP,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
    )


class OkxStub:
    """Мок OKX: возвращает управляемый набор свечей (внешний мир)."""

    def __init__(self, candles: list[models.Candle]):
        self.candles = candles

    async def get_candles(self, symbol, **kwargs):
        return self.candles

    async def get_orderbook(self, symbol, depth=20):
        return None

    async def get_ticker(self, symbol):
        last = float(self.candles[-1].close)
        return {"last": str(last), "high_24h": str(last + 1), "low_24h": str(last - 1)}


def make_engine(tmp_path, okx, pipeline, lessons_collector):
    cfg = TradingEngineConfig(
        symbols=("BTC-USDT",),
        timeframes=("5m",),
        bars_per_tf={"5m": 250},
        state_path=str(tmp_path / "pos.json"),
        trades_path=str(tmp_path / "trades.jsonl"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
        stats_path=str(tmp_path / "stats.json"),
        no_trade_observations_path=str(tmp_path / "obs.jsonl"),
        no_trade_outcomes_path=str(tmp_path / "outcomes.json"),
        # Hermetic: гипотезы — в tmp, не в repo-state.
        hypotheses_path=str(tmp_path / "hypotheses.json"),
    )
    broker = PaperBroker(
        state_path=__import__("pathlib").Path(cfg.state_path),
        trades_path=__import__("pathlib").Path(cfg.trades_path),
        initial_capital=Decimal("1000"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )
    eng = TradingEngine(
        okx=okx, pipeline=pipeline, config=cfg, broker=broker
    )
    # Внешние зависимости изолируем: сеть (новости) и расписание.
    eng.safety.check = lambda *a, **k: SafetyVerdict(allowed=True)
    return eng


def make_pipeline(tmp_path, store):
    cfg = DecisionConfig()
    cfg.min_rr = 0.7
    cfg.min_ml_probability = 0.0
    cfg.min_expected_edge_pct = 0.0
    cfg.max_spread_pct = 0.30
    cfg.slippage_buffer_pct = 0.02
    cfg.min_book_depth = 1_000.0
    cfg.min_ev_r = 0.05
    return DecisionPipeline(cfg, strategies=[ScalpStrategy()], stats_store=store)


class TestTradeExecutionPath:
    """market data → … → trade → lesson + stats."""

    def test_full_trade_flow(self, tmp_path, monkeypatch):
        lessons: list[dict] = []
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons",
            lambda trades: lessons.extend(trades) or 1,
        )
        store = StrategyStatsStore(tmp_path / "stats.json")
        okx = OkxStub(gen_candles())
        eng = make_engine(tmp_path, okx, make_pipeline(tmp_path, store), lessons)

        # Шаг 1: сигнал → позиция (через риск-контур).
        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        assert closed == []
        assert len(eng.broker.positions) == 1
        pos = eng.broker.positions[0]
        assert pos.strategy == "scalp"
        assert pos.regime  # контекст режима зафиксирован на входе
        assert pos.timeframe == "1h"
        assert pos.risk_distance > 0
        # Позиция учтена в Risk Engine.
        assert len(eng.risk._open_positions) == 1
        # Meta-Strategy записала EV в заметки позиции.
        assert pos.notes.get("ev_r") is not None

        # Шаг 2: бар пробивает стоп → закрытие → lesson + stats.
        stop_bar = stop_hit_bar(okx.candles[-1], pos.stop_loss)
        okx.candles = [*okx.candles, stop_bar]
        closed = asyncio.run(eng.process_symbol("BTC-USDT"))
        stops = [c for c in closed if c.exit_reason == "stop_loss"]
        assert len(stops) == 1
        # Net R при закрытии ровно по стопу (без издержек) = -1.0.
        assert stops[0].r_multiple == pytest.approx(-1.0, abs=1e-9)
        assert stops[0].mae_r >= 1.0
        assert stops[0].mfe_r > 0
        assert stops[0].regime == pos.regime

        # Lesson содержит статистическое основание (TZ §15).
        assert len(lessons) >= 1
        lesson = next(tr for tr in lessons if tr["id"] == stops[0].id)
        assert lesson["r_multiple"] == pytest.approx(-1.0, abs=1e-9)
        assert lesson["regime"] == pos.regime
        assert lesson["timeframe"] == "1h"

        # Статистика по режиму обновлена: и бакет режима, и агрегированный ANY.
        bucket = store.get("scalp", pos.regime, "1h")
        assert bucket is not None
        assert bucket.sample_size >= 1
        assert bucket.expectancy_r < 0
        any_bucket = store.get_any("scalp", "1h")
        assert any_bucket.sample_size >= 1

        # Risk Engine: убыток в дневном PnL, позиция убрана из книги.
        assert float(eng.risk._daily_pnl) < 0

    def test_no_trade_low_ev_with_observation_and_outcome(self, tmp_path, monkeypatch):
        """Стратегия убыточна в текущем режиме → NO_TRADE LOW_EV,
        наблюдение записано, обогащается будущим исходом, дублей нет."""
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons",
            lambda trades: 1,
        )
        base = gen_candles()
        store = StrategyStatsStore(tmp_path / "stats.json")
        # 40 убыточных сделок scalp в LOW_VOLATILITY (режим этого паттерна).
        for _ in range(40):
            store.record(
                strategy="scalp",
                regime="LOW_VOLATILITY",
                timeframe="1h",
                r_multiple=-0.9,
            )
        okx = OkxStub(base)
        eng = make_engine(
            tmp_path, okx, make_pipeline(tmp_path, store), []
        )

        # Шаг 1: NO_TRADE (EV в режиме отрицательный).
        asyncio.run(eng.process_symbol("BTC-USDT"))
        assert eng.broker.positions == []
        obs_path = tmp_path / "obs.jsonl"
        lines = obs_path.read_text().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["reason_code"] == "LOW_EV"
        assert row["market_regime"] == "LOW_VOLATILITY"
        assert row["candidate"]["strategy"] == "scalp"
        assert row["candidate"]["ev_r"] < 0

        # Шаг 2: та же свеча, повторная обработка — дубля нет (TZ §30).
        asyncio.run(eng.process_symbol("BTC-USDT"))
        assert len(obs_path.read_text().strip().splitlines()) == 1

        # Шаг 3: новый бар → обогащение исходом (TZ §13).
        okx.candles = [*okx.candles, stop_hit_bar(okx.candles[-1], Decimal("90"))]
        asyncio.run(eng.process_symbol("BTC-USDT"))
        outcomes = json.loads((tmp_path / "outcomes.json").read_text())["outcomes"]
        assert row["id"] in outcomes
        assert "1" in outcomes[row["id"]]["horizons"]
        assert "future_return" in outcomes[row["id"]]["horizons"]["1"]

