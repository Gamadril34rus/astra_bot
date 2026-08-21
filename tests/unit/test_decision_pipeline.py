"""Тесты цепочки принятия решений."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal

from astra_bot.core import models
from astra_bot.core import models as m
from astra_bot.decision import (
    CorrelationEngine,
    DecisionConfig,
    DecisionPipeline,
    EVEngine,
    MarketContext,
    MarketRegime,
    OrderBookEngine,
    RegimeEngine,
    StructureEngine,
    TechnicalEngine,
)
from astra_bot.strategies.base import BaseStrategy, Signal, StrategyConfig


class _LongStrategy(BaseStrategy):
    name = "test_long"

    def __init__(self):
        super().__init__(StrategyConfig(name=self.name))

    def calculate_stop_loss(self, *a, **k):
        return Decimal("0")

    def calculate_take_profit(self, *a, **k):
        return []

    async def evaluate(self, symbol, candles, **kwargs):
        if len(candles) < 60:
            return None
        price = Decimal(str(candles[-1].close))
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            direction=m.TradeDirection.LONG,
            entry_price=price,
            stop_loss=price * Decimal("0.98"),
            take_profit=price * Decimal("1.04"),
            confidence=0.8,
        )


def _candles(symbol: str, n: int = 400, bull: bool = True, seed: int = 1, drift: float = 0.0025):
    random.seed(seed)
    out = []
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    base = 30000.0
    for i in range(n):
        drift = drift if bull else -drift
        base *= 1 + random.gauss(drift, 0.008)
        op = base * (1 + random.gauss(0, 0.002))
        hi = max(base, op) * 1.003
        lo = min(base, op) * 0.997
        out.append(models.Candle(
            exchange="x", symbol=symbol, timeframe="1h",
            open_time=start + i * 3_600_000,
            open=Decimal(str(round(op, 2))),
            high=Decimal(str(round(hi, 2))),
            low=Decimal(str(round(lo, 2))),
            close=Decimal(str(round(base, 2))),
            volume=Decimal(str(round(random.uniform(5, 50), 2))),
            quote_volume=Decimal("1"),
        ))
    return out


def test_regime_detects_bull_trend():
    engine = RegimeEngine()
    candles = _candles("BTC/USDT", 500, bull=True, drift=0.004)
    report = engine.classify(candles)
    assert report.regime in {
        MarketRegime.STRONG_BULL,
        MarketRegime.WEAK_BULL,
        MarketRegime.BREAKOUT,
    }


def test_regime_panic_blocks_on_critical_news():
    engine = RegimeEngine()
    candles = _candles("BTC/USDT", 500, bull=True)
    report = engine.classify(candles, news_score=90)
    assert report.regime == MarketRegime.PANIC


def test_technical_engine_returns_structured_report():
    rep = TechnicalEngine().analyse(_candles("BTC/USDT", 400))
    assert rep.trend in (-1, 0, 1)
    assert rep.volatility in {"LOW", "NORMAL", "HIGH", "EXTREME"}
    assert 0 <= (rep.rsi or 0) <= 100


def test_structure_engine_detects_swing_levels():
    rep = StructureEngine().analyse(_candles("BTC/USDT", 400))
    assert rep.pattern in {"HH_HL", "LH_LL", "MIXED", "UNKNOWN"}


def test_orderbook_engine_neutral_when_no_book():
    rep = OrderBookEngine().analyse(None, mid_price=50000)
    assert rep.is_healthy is True
    assert rep.imbalance == 0.0


def test_ev_engine_rejects_negative_edge():
    ev = EVEngine(min_edge_pct=0.5)
    r = ev.calculate(p_win=0.4, entry=100, stop=99, take=101, fees_pct=0.1, slippage_pct=0.1)
    assert r.is_positive is False


def test_correlation_blocks_alt_long_when_btc_panic():
    rep = CorrelationEngine().assess(
        btc_regime=MarketRegime.PANIC.value,
        direction="long",
        is_btc=False,
    )
    assert rep.blocked is True


def test_pipeline_blocks_when_no_data():
    pipe = DecisionPipeline(DecisionConfig())
    ctx = MarketContext(symbol="BTC/USDT", current_price=Decimal("100"), candles={})
    decision = pipe.decide(ctx)
    assert decision.action == "NO_TRADE"
    assert "insufficient_data" in decision.reasons


def test_pipeline_runs_end_to_end_with_bull_trend():
    cfg = DecisionConfig()
    pipe = DecisionPipeline(cfg, strategies=[_LongStrategy()])
    candles = _candles("BTC/USDT", 500, bull=True)
    # 4h snapshot = подмножество каждые 4 бара.
    ctx = MarketContext(
        symbol="BTC/USDT",
        current_price=candles[-1].close,
        candles={"1h": candles, "4h": candles[::4], "15m": candles},
        global_market={"btc_regime": MarketRegime.WEAK_BULL.value},
    )
    decision = pipe.decide(ctx)
    assert decision.action in {"LONG", "NO_TRADE"}
    if decision.action == "LONG":
        assert decision.candidate is not None
        assert decision.candidate.position_size > 0
    else:
        # NO_TRADE допустим, если EV/ML/liquidity что-то зарубили.
        assert decision.reasons


def test_pipeline_blocks_panic_regime_globally():
    cfg = DecisionConfig()
    pipe = DecisionPipeline(cfg, strategies=[_LongStrategy()])
    candles = _candles("BTC/USDT", 500)
    ctx = MarketContext(
        symbol="BTC/USDT",
        current_price=candles[-1].close,
        candles={"1h": candles},
        news_score=90,
    )
    decision = pipe.decide(ctx)
    assert decision.action == "NO_TRADE"


class _FlipStubStrategy(BaseStrategy):
    """Стратегия-заглушка: отдаёт flat или flip-сигнал по флагу."""

    name = "tsm_stub"

    def __init__(self, tsm_action: float, direction=m.TradeDirection.LONG):
        super().__init__(StrategyConfig(name=self.name))
        self.tsm_action = tsm_action
        self.direction = direction

    def calculate_stop_loss(self, *a, **k):
        return Decimal("0")

    def calculate_take_profit(self, *a, **k):
        return []

    async def evaluate(self, symbol, candles, **kwargs):
        if len(candles) < 60:
            return None
        price = Decimal(str(candles[-1].close))
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            direction=self.direction,
            entry_price=price,
            stop_loss=price * Decimal("0.98"),
            take_profit=Decimal("0"),
            confidence=0.5,
            features={"tsm_action": self.tsm_action, "no_take_profit": 1.0},
        )


def _tsm_context(candles):
    return MarketContext(
        symbol="BTC/USDT",
        current_price=candles[-1].close,
        candles={"1h": candles, "4h": candles[::4], "15m": candles},
    )


def test_pipeline_flat_signal_returns_close():
    from astra_bot.strategies.ts_momentum import TSM_ACTION_FLAT

    pipe = DecisionPipeline(DecisionConfig(), strategies=[_FlipStubStrategy(TSM_ACTION_FLAT)])
    candles = _candles("BTC/USDT", 500, bull=True)
    decision = pipe.decide(_tsm_context(candles))
    assert decision.action == "CLOSE"
    assert decision.candidate is None


def test_pipeline_flip_signal_returns_flip_with_candidate():
    from astra_bot.strategies.ts_momentum import TSM_ACTION_FLIP

    pipe = DecisionPipeline(DecisionConfig(), strategies=[_FlipStubStrategy(TSM_ACTION_FLIP)])
    candles = _candles("BTC/USDT", 500, bull=True)
    decision = pipe.decide(_tsm_context(candles))
    assert decision.action == "FLIP"
    assert decision.candidate is not None
    assert decision.candidate.strategy == "tsm_stub"
    assert decision.candidate.features.get("no_take_profit") == 1.0


def test_pipeline_prefers_strategy_timeframe_candles():
    """Стратегия с preferred_timeframe получает свои свечи (4h)."""
    seen: dict = {}

    class _TfSpy(_LongStrategy):
        name = "tf_spy"
        preferred_timeframe = "4h"

        async def evaluate(self, symbol, candles, **kwargs):
            seen["n"] = len(candles)
            seen["step_ms"] = (
                candles[-1].open_time - candles[-2].open_time if len(candles) > 1 else 0
            )
            return None

    pipe = DecisionPipeline(DecisionConfig(), strategies=[_TfSpy()])
    candles = _candles("BTC/USDT", 500, bull=True)
    pipe.decide(_tsm_context(candles))
    assert seen["step_ms"] == 4 * 3_600_000
