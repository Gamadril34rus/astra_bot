"""Регрессионные тесты фиксов BaseStrategy.

Покрывают три бага, из-за которых стратегии «падали» в проде:

1. Совместимость с режимом рынка искалась СТРОКОЙ в словаре с Enum-ключами
   → всегда "OFF" → momentum/mean_reversion молча переставали торговать.
2. Profit Factor считался из net_pnl (фактически отношения количества
   сделок), серия без убытков оставляла PF=0 → ложный kill switch.
3. ``events.emit_async`` без await — событие STRATEGY_KILLED терялось.
"""

from astra_bot.core import events
from astra_bot.engines.regime_detector import STRATEGY_REGIME_COMPATIBILITY, MarketRegime
from astra_bot.strategies.base import BaseStrategy, StrategyConfig
from astra_bot.strategies.momentum import MomentumConfig, MomentumStrategy


class _MinimalStrategy(BaseStrategy):
    """Минимальная concrete-стратегия для тестов базы."""

    async def evaluate(self, symbol, candles, orderbook=None, current_price=None, market_regime=None):
        return None

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        return entry_price

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        return []


class TestRegimeCompatibility:
    def test_dict_keys_are_enums(self):
        """Контракт данных: внутренние ключи — члены MarketRegime."""
        for strategy_map in STRATEGY_REGIME_COMPATIBILITY.values():
            assert strategy_map, "у стратегии пустая карта режимов"
            for key in strategy_map:
                assert isinstance(key, MarketRegime), (
                    f"ключ {key!r} не является MarketRegime — "
                    "строковый поиск в check_regime_compatibility сломается"
                )

    def test_string_lookup_returns_not_off(self):
        s = _MinimalStrategy(StrategyConfig(name="momentum"))
        assert s.check_regime_compatibility("BULL_TREND") == "ON"
        assert s.check_regime_compatibility("PANIC") == "OFF"
        assert s.check_regime_compatibility("RANGE") == "REDUCED"

    def test_enum_lookup_works(self):
        s = _MinimalStrategy(StrategyConfig(name="mean_reversion"))
        assert s.check_regime_compatibility(MarketRegime.RANGE) == "ON"

    def test_unknown_regime_is_off_fail_closed(self):
        s = _MinimalStrategy(StrategyConfig(name="momentum"))
        assert s.check_regime_compatibility("NOT_A_REGIME") == "OFF"

    def test_get_regime_compatibility_no_off_blackhole(self):
        """get_regime_compatibility не должен возвращать OFF на валидном режиме."""
        s = _MinimalStrategy(StrategyConfig(name="ts_momentum"))
        assert s.get_regime_compatibility("BULL_TREND") == "ON"
        assert s.get_regime_compatibility("BEAR_TREND") == "ON"
        assert s.get_regime_compatibility("RANGE") == "OFF"  # мёртвая зона по дизайну

    def test_momentum_trades_in_bull_trend(self):
        """Моментум-стратегия проходит режимный гейт в бычьем тренде.

        До фикса get_regime_compatibility возвращала OFF в ЛЮБОМ режиме
        и strategy.evaluate всегда отдавала None.
        """
        s = MomentumStrategy(MomentumConfig())
        assert s.get_regime_compatibility("BULL_TREND") == "ON"


class TestProfitFactor:
    def test_pf_uses_gross_not_net(self):
        cfg = StrategyConfig(name="t")
        perf = cfg  # StrategyConfig сам хранит статистику
        perf.update_performance(won=True, pnl=10.0)
        perf.update_performance(won=True, pnl=30.0)
        perf.update_performance(won=False, pnl=-10.0)
        # gross 40 / gross loss 10 = 4.0 (раньше получалось 2.0 = wins/losses)
        assert cfg.profit_factor == 3.9999999999999996 or abs(cfg.profit_factor - 4.0) < 1e-9
        assert cfg.net_pnl == 30.0

    def test_win_streak_keeps_pf_high(self):
        cfg = StrategyConfig(name="t")
        for _ in range(5):
            cfg.update_performance(won=True, pnl=5.0)
        # Раньше PF оставался 0.0 при отсутствии убытков.
        assert cfg.profit_factor == 99.0
        assert cfg.is_healthy

    def test_no_trades_pf_zero(self):
        cfg = StrategyConfig(name="t")
        assert cfg.profit_factor == 0.0


class TestKillSwitchSampling:
    def test_single_loss_does_not_kill(self):
        s = _MinimalStrategy(StrategyConfig(name="t", decay_threshold=1.0))
        s.update_performance(won=False, pnl=-10.0)
        assert not s.config.kill_switch
        assert s.performance.total_trades == 1

    def test_kill_after_min_sample_of_losses(self):
        s = _MinimalStrategy(StrategyConfig(name="t", decay_threshold=1.0))
        for _ in range(s.MIN_TRADES_FOR_KILL_SWITCH):
            s.update_performance(won=False, pnl=-10.0)
        assert s.config.kill_switch

    def test_strategy_killed_event_published(self):
        """STRATEGY_KILLED публикуется синхронно и доходит до подписчиков."""
        received: list[dict] = []

        def _handler(event) -> None:
            received.append(event.data)

        bus = events.get_event_bus()
        bus.subscribe(events.EventType.STRATEGY_KILLED, _handler)
        try:
            s = _MinimalStrategy(StrategyConfig(name="doomed", decay_threshold=1.0))
            for _ in range(s.MIN_TRADES_FOR_KILL_SWITCH):
                s.update_performance(won=False, pnl=-10.0)
        finally:
            bus.unsubscribe(events.EventType.STRATEGY_KILLED, _handler)
        assert s.config.kill_switch
        assert received, "событие STRATEGY_KILLED потеряно (emit без await?)"


class TestAdapterSmoke:
    def test_signal_adapter_shapes_candidate(self):
        """PipelineStrategyAdapter конвертирует SignalCandidate в Signal."""
        import asyncio
        from decimal import Decimal

        from astra_bot.core import models
        from astra_bot.decision.strategies.adapter import PipelineStrategyAdapter
        from astra_bot.decision.strategies.volume_filtered import TrendFollowingStrategyV2

        candles = []
        price = 100.0
        for i in range(220):
            o, c = price, price * 1.004
            candles.append(
                models.Candle(
                    symbol="BTC-USDT",
                    open_time=1_700_000_000_000 + i * 60_000,
                    open=Decimal(str(o)),
                    high=Decimal(str(c * 1.001)),
                    low=Decimal(str(o * 0.999)),
                    close=Decimal(str(c)),
                    volume=Decimal("100" if i < 219 else "300"),  # всплеск на последней
                    exchange="bingx",
                    timeframe="5m",
                    quote_volume=Decimal("10000"),
                )
            )
            price = c

        adapter = PipelineStrategyAdapter(TrendFollowingStrategyV2())
        coro = adapter.evaluate(
            symbol="BTC-USDT",
            candles=candles,
            current_price=float(candles[-1].close),
            market_regime="BULL_TREND",
        )
        sig = asyncio.run(coro)
        assert sig is not None, "adapter не вернул сигнал на явном аптренде"
        assert sig.direction.value == "long"
        assert 0 < sig.confidence <= 1
        assert sig.risk_amount > 0
        assert sig.stop_loss < sig.entry_price < sig.take_profit


class TestTpSelection:
    def test_momentum_tp_satisfies_min_rr(self):
        """Основной TP обязан удовлетворять min_risk_reward.

        Раньше всегда брали tp_levels[0] (1R) при min_risk_reward=1.5:
        проверка R:R была невыполнимой и стратегия не давала сигналов
        вообще — даже в идеальном тренде.
        """
        from decimal import Decimal

        s = MomentumStrategy(MomentumConfig())
        entry = Decimal("100")
        stop = Decimal("99")
        levels = s.calculate_take_profit(entry, stop, [])
        assert levels[0]["r_multiple"] == 1.0  # первый уровень — 1R
        # Эмулируем выбор, как в evaluate: первый уровень >= min_risk_reward.
        chosen = next(
            (lv for lv in levels if lv["r_multiple"] >= s.config.min_risk_reward),
            levels[-1],
        )
        assert chosen["r_multiple"] >= s.config.min_risk_reward
        rr = float(abs(chosen["price"] - entry)) / float(abs(entry - stop))
        assert rr >= s.config.min_risk_reward


class TestPipelineStrategyNames:
    def test_pipeline_strategy_names_unique(self):
        """Имена стратегий в пайплайне не должны коллизировать.

        Статистика (strategy_stats) ключуется именем стратегии: две
        стратегии с одним именем загрязняли бы EV друг друга.
        """
        from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig

        eng = TradingEngine(TradingEngineConfig(symbols=("BTC-USDT",)))
        names = [getattr(x, "name", "?") for x in eng.pipeline.strategies]
        assert len(names) == len(set(names)), f"коллизия имён: {sorted(names)}"

    def test_pattern_strategies_loaded(self):
        """18 pattern/V2-стратегий должны присутствовать в пайплайне
        (14 паттернных + 4 V2)."""
        from astra_bot.decision.strategies.adapter import PipelineStrategyAdapter
        from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig

        eng = TradingEngine(TradingEngineConfig(symbols=("BTC-USDT",)))
        adapted = [x.name for x in eng.pipeline.strategies if isinstance(x, PipelineStrategyAdapter)]
        assert len(adapted) == 18
        assert "falling_wedge" in adapted and "trend_following" in adapted


class TestStrategyContextImport:
    def test_strategy_context_importable(self):
        """Точка прошлого падения: StrategyContext должен существовать.

        Раньше pattern_strategies/volume_filtered падали на импорте
        (cannot import name 'StrategyContext') и молча отключались.
        """
        from astra_bot.decision.context import SignalCandidate, StrategyContext  # noqa: F401
        from astra_bot.decision.strategies import pattern_strategies, volume_filtered

        assert hasattr(pattern_strategies, "StrategyContext")
        assert hasattr(volume_filtered, "StrategyContext")
