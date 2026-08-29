"""A2 (МТЗ §10–14): Regime 2.0 — оси trend×volatility×liquidity, кросс-маркет,
миграция ключей статистики (старые бакеты читаются, новые пишутся)."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from astra_bot.core import models
from astra_bot.decision import (
    CrossMarketContext,
    DecisionConfig,
    DecisionPipeline,
    LiquidityAxis,
    MarketContext,
    MarketRegime,
    RegimeAxes,
    RegimeEngine,
    TrendAxis,
    VolatilityAxis,
    derive_axes,
)
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.meta_strategy import MetaStrategy
from astra_bot.decision.strategy_stats import StrategyStatsStore
from astra_bot.strategies.base import BaseStrategy, Signal, StrategyConfig


# ------------------------------------------------------------------ helpers
def _candles(symbol: str, n: int = 400, bull: bool = True, seed: int = 1,
             drift: float = 0.0025) -> list[models.Candle]:
    random.seed(seed)
    out = []
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    base = 30000.0
    for i in range(n):
        d = drift if bull else -drift
        base *= 1 + random.gauss(d, 0.008)
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


def _book(price: float, spread_pct: float, depth_each: float, levels: int = 20):
    half = 1 + spread_pct / 200.0
    bids = [models.OrderBookEntry(price=Decimal(str(round(price / half, 2))),
                                  quantity=Decimal(str(round(depth_each / price / levels, 6))))]
    asks = [models.OrderBookEntry(price=Decimal(str(round(price * half, 2))),
                                  quantity=Decimal(str(round(depth_each / price / levels, 6))))]
    # дублируем уровни до нужной глубины
    bids = [models.OrderBookEntry(price=Decimal(str(round(price * (0.999 - 0.0005 * k), 2))),
                                  quantity=Decimal(str(round(depth_each / levels / price, 6))))
             for k in range(levels)]
    asks = [models.OrderBookEntry(price=Decimal(str(round(price * (1.001 + 0.0005 * k), 2))),
                                  quantity=Decimal(str(round(depth_each / levels / price, 6))))
            for k in range(levels)]
    return models.OrderBook(exchange="x", symbol="BTC/USDT", bids=bids, asks=asks)


# ------------------------------------------------------------------ оси: чистые функции
class TestAxisFunctions:
    def test_trend_boundaries(self):
        assert derive_axes(adx=45, aligned_bull=True).trend is TrendAxis.STRONG
        assert derive_axes(adx=30, aligned_bull=True).trend is TrendAxis.WEAK
        assert derive_axes(adx=10, aligned_bull=False).trend is TrendAxis.RANGE
        # ADX высокий, но структура не выровнена — смена тренда.
        assert derive_axes(adx=30, aligned_bull=False).trend is TrendAxis.TRANSITION
        # breakout перекрывает выровненный сильный тренд.
        assert derive_axes(
            adx=45, aligned_bull=True, breakout=True
        ).trend is TrendAxis.TRANSITION
        assert derive_axes(
            adx=45, aligned_bear=True
        ).trend is TrendAxis.STRONG

    def test_volatility_boundaries(self):
        assert derive_axes(atr_pct=0.4).volatility is VolatilityAxis.VERY_LOW
        assert derive_axes(atr_pct=0.9).volatility is VolatilityAxis.LOW
        assert derive_axes(atr_pct=2.0).volatility is VolatilityAxis.NORMAL
        assert derive_axes(atr_pct=6.0).volatility is VolatilityAxis.HIGH
        assert derive_axes(atr_pct=12.0).volatility is VolatilityAxis.EXTREME

    def test_liquidity_priority_and_neutral(self):
        # нет данных стакана — нейтральный NORMAL (fail-soft для ключа)
        assert derive_axes().liquidity is LiquidityAxis.NORMAL
        assert derive_axes(
            spread_pct=0.05, depth_usd=1000
        ).liquidity is LiquidityAxis.THIN
        assert derive_axes(
            spread_pct=0.5, depth_usd=1e9  # спред ≥ 2×max — стресс важнее глубины
        ).liquidity is LiquidityAxis.STRESSED
        assert derive_axes(
            spread_pct=0.05, depth_usd=20000
        ).liquidity is LiquidityAxis.DEEP
        assert derive_axes(
            spread_pct=0.05, depth_usd=8000, vol_spike=3.5
        ).liquidity is LiquidityAxis.STRESSED

    def test_axes_key_format(self):
        a = RegimeAxes(
            trend=TrendAxis.STRONG,
            volatility=VolatilityAxis.HIGH,
            liquidity=LiquidityAxis.THIN,
        )
        assert a.axes_key() == "T:STRONG/V:HIGH/L:THIN"
        assert "|" not in a.axes_key()  # не конфликтует с разделителем ключей


# ------------------------------------------------------------------ cross-market
class TestCrossMarket:
    def test_from_global_market_relative_strength(self):
        ctx = CrossMarketContext.from_global_market({
            "btc_change_pct_24h": 1.0,
            "eth_change_pct_24h": 0.5,
            "sol_change_pct_24h": -0.2,
            "symbol_change_pct_24h": 3.5,
        })
        assert ctx is not None
        assert ctx.relative_strength_pct == pytest.approx(2.5)
        assert ctx.rs_bucket == "OUTPERFORM"
        ctx2 = CrossMarketContext.from_global_market({
            "btc_change_pct_24h": 4.0,
            "symbol_change_pct_24h": 0.5,
        })
        assert ctx2.rs_bucket == "UNDERPERFORM"
        ctx3 = CrossMarketContext.from_global_market({
            "btc_change_pct_24h": 1.0,
            "symbol_change_pct_24h": 1.4,
        })
        assert ctx3.rs_bucket == "NEUTRAL"

    def test_none_when_no_data(self):
        assert CrossMarketContext.from_global_market(None) is None
        assert CrossMarketContext.from_global_market({}) is None
        assert CrossMarketContext.from_global_market({"junk": "x"}) is None

    def test_btc_regime_only_and_risk_off(self):
        ctx = CrossMarketContext.from_global_market({"btc_regime": "PANIC"})
        assert ctx is not None
        assert ctx.majors_risk_off is True
        assert ctx.rs_bucket == "UNKNOWN"


# ------------------------------------------------------------------ RegimeEngine
class TestRegimeEngineAxes:
    def test_bull_report_has_axes_and_legacy_shape(self):
        engine = RegimeEngine()
        candles = _candles("BTC/USDT", 500, bull=True, drift=0.004)
        report = engine.classify(candles)
        assert report.regime in {
            MarketRegime.STRONG_BULL, MarketRegime.WEAK_BULL,
            MarketRegime.BREAKOUT,
        }
        assert report.axes is not None
        d = report.to_dict()
        # legacy-форма не сломана
        assert set(d) >= {"regime", "confidence", "details"}
        assert d["axes_key"] == report.axes.axes_key()
        assert d["axes"]["trend"] in {"STRONG", "WEAK", "RANGE", "TRANSITION"}

    def test_short_signature_still_works(self):
        """Старые вызовы classify(candles, news_score=..., btc_regime=...) живы."""
        engine = RegimeEngine()
        candles = _candles("BTC/USDT", 500)
        rep = engine.classify(candles, news_score=0, btc_regime=None)
        assert rep.regime != MarketRegime.UNKNOWN

    def test_panic_axes(self):
        engine = RegimeEngine()
        rep = engine.classify(_candles("BTC/USDT", 300), news_score=90)
        assert rep.regime == MarketRegime.PANIC
        assert rep.axes.volatility is VolatilityAxis.EXTREME
        assert rep.axes.trend is TrendAxis.TRANSITION

    def test_unknown_has_no_axes(self):
        rep = RegimeEngine().classify(_candles("BTC/USDT", 30))
        assert rep.regime == MarketRegime.UNKNOWN
        assert rep.axes is None
        assert "axes" not in rep.to_dict()

    def test_orderbook_and_cross_inputs_feed_axes(self):
        engine = RegimeEngine()
        candles = _candles("BTC/USDT", 300, drift=0.0005)
        thin = _book(30000.0, spread_pct=0.05, depth_each=1000.0)
        rep = engine.classify(
            candles,
            orderbook=thin,
            current_price=30000.0,
            cross_market={"btc_change_pct_24h": 1.0, "symbol_change_pct_24h": 4.0},
        )
        assert rep.axes.liquidity is LiquidityAxis.THIN
        assert rep.axes.cross is not None
        assert rep.axes.cross.rs_bucket == "OUTPERFORM"
        assert rep.details.get("cross_market")["relative_strength_pct"] == pytest.approx(3.0)


# ------------------------------------------------------------------ миграция статистики
class TestStatsMigration:
    AX = "T:RANGE/V:NORMAL/L:NORMAL"

    def test_dual_write(self, tmp_path: Path):
        store = StrategyStatsStore(path=tmp_path / "s.json")
        store.record(strategy="s1", regime="RANGE", timeframe="5m",
                     r_multiple=1.0, regime_axes=self.AX)
        assert f"s1|{self.AX}|5m" in store.buckets
        assert "s1|RANGE|5m" in store.buckets  # legacy тоже пополняется
        assert "s1|ANY|5m" in store.buckets

    def test_record_without_axes_is_legacy_only(self, tmp_path: Path):
        store = StrategyStatsStore(path=tmp_path / "s.json")
        store.record(strategy="s1", regime="RANGE", timeframe="5m", r_multiple=1.0)
        assert list(store.buckets) == ["s1|RANGE|5m", "s1|ANY|5m"]

    def test_old_file_readable_and_fallback(self, tmp_path: Path):
        """Файл до A2: только legacy-бакеты; get с новым ключом — фолбэк на legacy."""
        legacy = {
            "updated": "x",
            "buckets": {
                "s1|WEAK_BULL_TREND|5m": {
                    "sample_size": 100, "wins": 60, "losses": 40,
                    "sum_r": 25.0, "wins_sum_r": 55.0, "losses_sum_r": -30.0,
                    "sum_mfe_r": 0, "sum_mae_r": 0, "sum_fees": 0, "last_updated": None,
                }
            },
        }
        p = tmp_path / "s.json"
        p.write_text(json.dumps(legacy), encoding="utf-8")
        store = StrategyStatsStore(path=p)
        # новый композитный ключ ещё пуст — читаем legacy
        got = store.get("s1", "WEAK_BULL_TREND", "5m", regime_axes=self.AX)
        assert got is not None and got.sample_size == 100
        _, conf, _ = store.expectancy("s1", "WEAK_BULL_TREND", "5m", 0.0,
                                       regime_axes=self.AX)
        assert conf == pytest.approx(100 / 130)

    def test_axes_bucket_preferred_over_legacy(self, tmp_path: Path):
        from astra_bot.decision.strategy_stats import StrategyRegimeStats
        store = StrategyStatsStore(path=tmp_path / "s.json")
        bad = StrategyRegimeStats()
        for _ in range(60):
            bad.record(-1.0)
        good = StrategyRegimeStats()
        for _ in range(60):
            good.record(1.0)
        store.buckets[f"s1|{self.AX}|5m"] = bad
        store.buckets["s1|RANGE|5m"] = good
        # axes-бакет отрицательный — приоритет у него
        got = store.get("s1", "RANGE", "5m", regime_axes=self.AX)
        assert got.expectancy_r < 0
        # без axes-ключа — читаем legacy как раньше
        got2 = store.get("s1", "RANGE", "5m")
        assert got2.expectancy_r > 0

    def test_any_fallback_preserved(self, tmp_path: Path):
        store = StrategyStatsStore(path=tmp_path / "s.json")
        for _ in range(5):
            store.record(strategy="s1", regime="RANGE", timeframe="5m", r_multiple=0.5)
        got = store.get("s1", "PANIC", "5m", regime_axes="T:RANGE/V:EXTREME/L:STRESSED")
        assert got is not None and got.sample_size == 5  # ANY

    def test_save_load_roundtrip(self, tmp_path: Path):
        p = tmp_path / "s.json"
        store = StrategyStatsStore(path=p)
        store.record(strategy="s1", regime="RANGE", timeframe="5m",
                     r_multiple=2.0, regime_axes=self.AX)
        re = StrategyStatsStore(path=p)
        assert f"s1|{self.AX}|5m" in re.buckets
        assert re.buckets[f"s1|{self.AX}|5m"].sum_r == pytest.approx(2.0)


# ------------------------------------------------------------------ meta-strategy
class TestMetaStrategyAxes:
    @staticmethod
    def _candidate(strategy: str = "trend_a"):
        from astra_bot.decision.context import SignalCandidate
        return SignalCandidate(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("99"),
            take_profit=Decimal("103"), timeframe="1h",
            strategy=strategy, confidence=0.7,
        )

    def test_axes_bucket_changes_choice(self, tmp_path: Path):
        from astra_bot.decision.strategy_stats import StrategyRegimeStats
        store = StrategyStatsStore(path=tmp_path / "s.json", min_samples=30)
        # legacy: good (позитивный EV); axes: bad (отрицательный EV)
        good = StrategyRegimeStats()
        for _ in range(100):
            good.record(1.0)
        store.buckets["trend_a|RANGE|1h"] = good
        bad = StrategyRegimeStats()
        for _ in range(100):
            bad.record(-1.0)
        store.buckets["trend_a|T:RANGE/V:HIGH/L:THIN|1h"] = bad
        meta = MetaStrategy(store, DecisionConfig(min_ev_r=0.1, min_ev_confidence=0.0))
        dec = meta.select([self._candidate()], regime="RANGE")
        assert dec.chosen is not None  # без axes — legacy выборка хорошая
        dec2 = meta.select([self._candidate()], regime="RANGE",
                           regime_axes="T:RANGE/V:HIGH/L:THIN")
        assert dec2.chosen is None  # axes-статистика хуже — она приоритетна


# ------------------------------------------------------------------ broker
class TestBrokerAxesPassthrough:
    def test_position_and_closed_trade_carry_axes(self, tmp_path: Path):
        broker = PaperBroker(state_path=tmp_path / "p.json",
                             trades_path=tmp_path / "t.jsonl")
        pos = broker.open_position(
            symbol="BTC/USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("95"),
            take_profit=Decimal("110"), quantity=Decimal("1"),
            strategy="s1", regime="RANGE", timeframe="5m",
            regime_axes="T:RANGE/V:NORMAL/L:NORMAL",
        )
        assert pos.regime_axes == "T:RANGE/V:NORMAL/L:NORMAL"
        closed = broker.close_positions("BTC/USDT", Decimal("101"), "test")
        assert closed and closed[0].regime_axes == "T:RANGE/V:NORMAL/L:NORMAL"

    def test_legacy_state_loads_without_axes(self, tmp_path: Path):
        """Старое состояние позиций без нового поля загружается."""
        p = tmp_path / "p.json"
        p.write_text(json.dumps({
            "positions": [{
                "id": "1", "symbol": "BTC/USDT", "direction": "long",
                "entry_price": "100", "quantity": "1", "stop_loss": "95",
                "take_profits": ["110"], "tp_filled": [False],
                "tp_fractions": [0.5, 0.3, 0.2], "initial_quantity": "1",
                "trailing_activated": False, "trailing_distance": None,
                "highest_price": None, "lowest_price": None, "strategy": "s",
                "opened_at": 0, "notes": {}, "fill_price": "100",
                "entry_fee_per_unit": "0", "risk_distance": "5",
                "regime": "RANGE", "timeframe": "5m", "bars_held": 0,
            }],
            "realized_pnl": "0",
        }), encoding="utf-8")
        broker = PaperBroker(state_path=p, trades_path=tmp_path / "t.jsonl")
        assert broker.positions[0].regime_axes == ""


# ------------------------------------------------------------------ pipeline E2E
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
            symbol=symbol, strategy_name=self.name,
            direction=models.TradeDirection.LONG,
            entry_price=price, stop_loss=price * Decimal("0.98"),
            take_profit=price * Decimal("1.04"), confidence=0.8,
        )


async def test_pipeline_attaches_axes_to_diagnostics(tmp_path: Path):
    store = StrategyStatsStore(path=tmp_path / "s.json")
    pipe = DecisionPipeline(DecisionConfig(), strategies=[_LongStrategy()],
                            stats_store=store)
    ctx = MarketContext(
        symbol="BTC/USDT",
        current_price=Decimal("30000"),
        candles={"1h": _candles("BTC/USDT", 500, bull=True, drift=0.004)},
        orderbook=_book(30000.0, spread_pct=0.05, depth_each=80000.0),
        global_market={"btc_regime": "WEAK_BULL_TREND",
                       "btc_change_pct_24h": 0.5, "symbol_change_pct_24h": 1.2},
    )
    decision = await pipe.decide(ctx)
    reg = decision.diagnostics.get("regime") or {}
    assert reg.get("axes_key", "").startswith("T:")
    assert "|" not in reg["axes_key"]
    assert reg["axes"]["cross"]["rs_bucket"] == "NEUTRAL"


async def test_pipeline_closed_record_dual_buckets(tmp_path: Path):
    """record() через стор пайплайна с axes-ключом создаёт оба бакета."""
    store = StrategyStatsStore(path=tmp_path / "s.json")
    ax = "T:WEAK/V:NORMAL/L:DEEP"
    store.record(strategy="test_long", regime="WEAK_BULL_TREND",
                 timeframe="1h", r_multiple=1.5, regime_axes=ax)
    assert f"test_long|{ax}|1h" in store.buckets
    assert "test_long|WEAK_BULL_TREND|1h" in store.buckets
