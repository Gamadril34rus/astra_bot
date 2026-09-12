"""
ASTRA BOT — Trading engine.

Связывает DecisionPipeline, BingX market data и PaperBroker:

1. Тянет свечи 4h/1h/15m/5m и стакан по инструментам.
2. На каждом 5m-баре вызывает ``pipeline.decide``.
3. При сигнале LONG/SHORT открывает бумажную позицию.
4. По каждому новому бару обновляет стопы/тейки PaperBroker.
5. Пишет метрики и логи.

Это изолированный движок для непрерывной paper-торговли.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..adapters.base import ExchangeAdapter
from ..core import models, trading_schedule
from ..core.market_safety import MarketSafety
from ..core.metrics import (
    DECISION_LATENCY,
    DECISIONS_TOTAL,
    EXITS_TOTAL,
    TICK_LATENCY,
)
from ..engines.cost_model import BINGX_PERPS_TAKER_FEE, bingx_perps_cost_model
from ..engines.risk_engine import RiskConfig, RiskEngine
from ..ml.live_lessons import append_lessons
from . import halt_alerts
from .broker import PaperBroker
from .context import MarketContext
from .pipeline import Decision, DecisionPipeline

# Fire-and-forget задачи (уведомления и т.п.). Ссылки хранятся явно:
# без них задача может быть собрана GC до выполнения и цикл уронит
# "Task was destroyed but it is pending".
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro) -> None:
    """Запустить корутину в фоне текущего event loop, сохранив ссылку."""
    task = asyncio.ensure_future(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

logger = logging.getLogger(__name__)


# Лестница плеча по УВЕРЕННОСТИ: (мин. confidence, мин. EV_R, плечо).
# Пользователь: «если бот уверен, что цена пойдёт по сценарию, точно —
# плечо не ограничивается 2, а доходит до 100. Но только после ПОЛНОЙ
# уверенности». Первая ступень, дающая плечо, — базовые 2x по EV
# (см. leverage_for); лестница повышает только при высокой уверенности.
LEVERAGE_LADDER: tuple[tuple[float, float, int], ...] = (
    (0.95, 3.0, 100),   # «прям точно»: почти железная уверенность + сильный EV
    (0.90, 2.5, 50),
    (0.85, 2.0, 20),
    (0.80, 1.6, 10),
    (0.70, 1.3, 5),
    (0.55, 1.1, 3),
)


def leverage_for(
    confidence: float,
    ev_r: float,
    entry_price: float,
    stop_loss: float,
    max_leverage: int,
    min_ev_r: float,
    maintenance_margin_pct: float = 0.005,
) -> int:
    """Плечо сделки: база 2x по EV, выше — только по уверенности.

    Два обязательных ограничителя:
      - потолок ASTRA_LEVERAGE_MAX (config.leverage_max);
      - СТОП ДОЛЖЕН УМЕРЕТЬ РАНЬШЕ ЛИКВИДАЦИИ: lev < 1/(d + mm), где
        d — дистанция стопа в долях цены. 100x с широким стопом —
        гарантированная ликвидация, поэтому плечо срезается до
        допустимого (а не отменяется сделка).

    PnL по стопу от плеча не зависит (объём считается по риску) —
    плечо меняет маржу, фандинг и близость ликвидации, не риск.
    """
    if max_leverage <= 1:
        return 1
    if ev_r < min_ev_r:
        return 1
    lev = 2  # база: сильный EV уже даёт 2x (прежнее поведение)
    for min_conf, min_ev, rung in LEVERAGE_LADDER:
        if confidence >= min_conf and ev_r >= min_ev:
            lev = max(lev, min(rung, max_leverage))
            break
    lev = min(lev, max_leverage)
    entry = float(entry_price)
    stop = float(stop_loss)
    if entry > 0:
        d = abs(entry - stop) / entry
        if d > 0:
            feasible = int(1.0 / (d + maintenance_margin_pct)) - 1
            if lev > feasible:
                lev = max(1, feasible)
    return max(1, lev)


@dataclass
class TradingEngineConfig:
    symbols: tuple[str, ...] = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    # Block 4.2: Мультитаймфреймовый анализ — 4 таймфрейма
    # 5m — микроподтверждение, 15m — точка входа, 1h — направление, 4h — глобальный тренд
    timeframes: tuple[str, ...] = ("5m", "15m", "1h", "4h")
    # Сколько баров тянуть для каждого таймфрейма.
    bars_per_tf: dict[str, int] = field(
        default_factory=lambda: {"5m": 250, "15m": 200, "1h": 200, "4h": 320}
    )
    # Набор владельца (п.7, 12.09): дневной ряд для об/брейкер/маикросс
    # и дневных гейтов. 500 баров (поправка 4): один запрос
    # (_MAX_KLINES=500), EMA200 на 250 барах нестабилен (посев от
    # values[0]), на 500 — приемлемо. В config.timeframes НЕ входит:
    # выбор ТФ остальных стратегий не меняется.
    htf_daily_tf: str = "1d"
    htf_daily_bars: int = 500
    # Block 6.1: Максимальный риск на сделку 1% (было 0.5%)
    risk_per_trade_pct: Decimal = Decimal("0.01")
    # Block 6.2: Жёсткий максимум 10% баланса на позицию
    max_notional_pct: Decimal = Decimal("0.10")
    # Block 6.1: Максимум 3 открытых позиций (было 8) — защита от корреляции
    max_open_positions: int = 3
    # Лимит однонаправленных позиций — чтобы не открыть сразу 3 лонга в одну сторону
    max_same_direction: int = 2
    # Максимальная суммарная экспозиция 30% (было 70%) — консервативно
    max_total_exposure_pct: Decimal = Decimal("0.30")
    poll_interval_seconds: int = 60 * 5
    state_path: str = "models/paper_positions.json"
    trades_path: str = "models/paper_trades.jsonl"
    # Персистентный dedup HALT-алертов {ключ: дата}. Actions-сессия —
    # свежий процесс, без файла алерт повторялся бы каждые 5 минут.
    # Файл должен быть в Save-state (bot.yml, добавляет владелец).
    halt_alerts_path: str = "models/halt_alerts.json"
    # Реальные издержки paper-счёта (тейкер-комиссия / slippage на сторону).
    # База — тариф перпов BingX USDT-M: тейкер 0.05%, slippage 0.1%.
    fee_pct: Decimal = Decimal("0.0005")
    slippage_pct: Decimal = Decimal("0.001")
    # Research memory: статистика стратегий по режимам + NO_TRADE-наблюдения.
    # Плечо: максимум и порог EV (R), начиная с которого движок берёт
    # плечо. Фандинг перпов начисляется брокером при
    # закрытии — в PnL она попадает всегда.
    # Умные выходы по умолчанию (BE-нетто/трейлинг/MAE_CUT/REGIME_EXIT),
    # пока Hypothesis Engine не продвинул собственный план (TZ §16/§17).
    smart_exit_default: bool = True
    # Структурный стоп: перед сайзингом выносим стоп за свинг по теням.
    structural_stop: bool = True
    # Потолок плеча. Реальный уровень задаёт лестница уверенности
    # (LEVERAGE_LADDER): обычный сетап с сильным EV — 2x, «полная
    # уверенность» (conf >= 0.95 и EV >= 3R) — до 100x.
    leverage_max: int = 100
    leverage_min_ev_r: float = 0.8
    stats_path: str = "models/strategy_stats.json"
    no_trade_observations_path: str = "models/no_trade_observations.jsonl"
    no_trade_outcomes_path: str = "models/no_trade_outcomes.json"
    hypotheses_path: str = "models/research/hypotheses.json"
    # Анти-дребезг (решение владельца 12.09.2026): после выхода по стопу
    # та же стратегия не входит в тот же символ/сторону столько минут.
    reentry_cooldown_minutes: int = 20


class TradingEngine:
    def __init__(
        self,
        exchange: ExchangeAdapter,
        pipeline: DecisionPipeline | None = None,
        config: TradingEngineConfig | None = None,
        broker: PaperBroker | None = None,
        notifier: Any | None = None,
        risk_engine: RiskEngine | None = None,
    ):
        # Колбэк для уведомлений в Telegram: notifier(text, severity).
        self._notifier = notifier
        self.exchange = exchange
        self.config = config or TradingEngineConfig()
        # HALT-алерты: dedup в пределах сессии (set) + персистентный файл
        # между сессиями (halt_alerts.py; Actions поднимает процесс раз в 5 мин).
        self._halt_alerts_sent: set[str] = set()
        self._halt_alerts_path = Path(self.config.halt_alerts_path)
        if pipeline is None:
            from ..strategies import (
                MeanReversionStrategy,
                MomentumStrategy,
                PullbackStrategy,
                Scalp5mStrategy,
                ScalpStrategy,
                TimeSeriesMomentumConfig,
                TimeSeriesMomentumStrategy,
            )
            from .config import DecisionConfig
            from .strategy_stats import StrategyStatsStore
            # Пороги согласованы со стратегиями. Scalp даёт много мелких
            # сделок на 15m для быстрого обучения; Pullback — крупнее на 1h;
            # ts_momentum — флипы по 45-дневному импульсу на 4h, плюс
            # вариант с ADX-подтверждением (оба проверены walk-forward'ом
            # в scripts/strategy_lab.py).
            cfg = DecisionConfig()
            cfg.min_rr = 0.7
            cfg.min_ml_probability = 0.0
            cfg.min_expected_edge_pct = 0.0
            cfg.max_spread_pct = 0.30
            # ЕДИНИЦЫ (аудит A5): ПРОЦЕНТЫ (0.02 = 0.02% цены).
            cfg.slippage_buffer_pct = 0.02
            cfg.min_book_depth = 1_000.0
            # Meta-Strategy: EV-гейт 0.05R (аудит эпохи-2: 0.0 пропускал
            # всё подряд) + быстрый shrinkage k=10, чтобы эмпирика давила
            # оптимистичный prior уже с ~10 сделок.
            cfg.min_ev_r = 0.05
            cfg.ev_shrinkage_k = 10.0
            stats_store = StrategyStatsStore(
                Path(self.config.stats_path),
                shrinkage_k=cfg.ev_shrinkage_k,
                min_samples=cfg.min_ev_samples,
            )
            # Pattern strategies (wedges & triangles) — по ТЗ пользователя: основа
            # V2-стратегии написаны против плоского StrategyContext и возвращают
            # SignalCandidate; через PipelineStrategyAdapter они работают в
            # контракте пайплайна (evaluate(symbol, candles, ...) -> Signal).
            try:
                from ..strategies.base import SignalType
                from .strategies.adapter import PipelineStrategyAdapter
                from .strategies.pattern_strategies import (
                    AscendingTriangleStrategy,
                    CupAndHandleStrategy,
                    DescendingTriangleStrategy,
                    DiamondStrategy,
                    DoubleTopBottomStrategy,
                    ExpandingTriangleStrategy,
                    FallingWedgeStrategy,
                    FlagStrategy,
                    HeadShouldersStrategy,
                    PennantStrategy,
                    RectangleStrategy,
                    RisingWedgeStrategy,
                    RoundedBottomStrategy,
                    RoundedTopStrategy,
                    SymmetricalTriangleStrategy,
                    TripleTopBottomStrategy,
                )
                from .strategies.volume_filtered import (
                    BreakoutStrategyV2,
                    MeanReversionStrategyV2,
                    MomentumStrategyV2,
                    TrendFollowingStrategyV2,
                )

                pattern_strats = [
                    PipelineStrategyAdapter(FallingWedgeStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(RisingWedgeStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(AscendingTriangleStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(DescendingTriangleStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(SymmetricalTriangleStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(HeadShouldersStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(DoubleTopBottomStrategy(), SignalType.MEAN_REVERSION),
                    PipelineStrategyAdapter(TripleTopBottomStrategy(), SignalType.MEAN_REVERSION),
                    PipelineStrategyAdapter(RectangleStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(ExpandingTriangleStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(FlagStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(PennantStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(DiamondStrategy(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(CupAndHandleStrategy(), SignalType.MOMENTUM),
                    # Д-фигуры владельца (12.09): разворотные, как
                    # DoubleTopBottom — тип MEAN_REVERSION.
                    PipelineStrategyAdapter(RoundedTopStrategy(), SignalType.MEAN_REVERSION),
                    PipelineStrategyAdapter(RoundedBottomStrategy(), SignalType.MEAN_REVERSION),
                    PipelineStrategyAdapter(TrendFollowingStrategyV2(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(MeanReversionStrategyV2(), SignalType.MEAN_REVERSION),
                    PipelineStrategyAdapter(BreakoutStrategyV2(), SignalType.MOMENTUM),
                    PipelineStrategyAdapter(MomentumStrategyV2(), SignalType.MOMENTUM),
                ]
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    "Pattern strategies import failed — паттерн-стратегии "
                    "НЕ загружены (состав пайплайна неполный): %s", e
                )
                pattern_strats = []

            from ..strategies.fair_value_gap import FairValueGapStrategy
            from ..strategies.funding_rate_contrarian import FundingRateContrarianStrategy

            # Набор индикаторов владельца (п.7, 12.09): дневные
            # об/брейкер по Люксу + маикросс (ЕМА 20/50).
            from ..strategies.htf_blocks import (
                BreakerBlockStrategy,
                MaiCrossStrategy,
                ObSwingStrategy,
            )
            from ..strategies.liquidity_sweep import LiquiditySweepStrategy
            from ..strategies.open_interest_divergence import OpenInterestDivergenceStrategy
            from ..strategies.order_block import OrderBlockStrategy
            from ..strategies.range_breakout_retest import RangeBreakoutRetestStrategy
            from ..strategies.ts_momentum_cross import TSMomentumCrossStrategy
            from ..strategies.volatility_breakout import VolatilityBreakoutStrategy
            from ..strategies.volume_delta import VolumeDeltaStrategy
            from ..strategies.vwap_deviation import VWAPDeviationStrategy
            from ..strategies.zscore_mean_reversion import ZScoreMeanReversionStrategy

            new_strategies = [
                LiquiditySweepStrategy(),
                FairValueGapStrategy(),
                OrderBlockStrategy(),
                RangeBreakoutRetestStrategy(),
                ZScoreMeanReversionStrategy(),
                VolatilityBreakoutStrategy(),
                TSMomentumCrossStrategy(),
                FundingRateContrarianStrategy(),
                VolumeDeltaStrategy(),
                VWAPDeviationStrategy(),
                OpenInterestDivergenceStrategy(),
                # Набор владельца (п.7): дневные источники входов.
                ObSwingStrategy(),
                BreakerBlockStrategy(),
                MaiCrossStrategy(),
            ]

            pipeline = DecisionPipeline(
                cfg,
                stats_store=stats_store,
                strategies=[
                    Scalp5mStrategy(),
                    ScalpStrategy(),
                    PullbackStrategy(),
                    MomentumStrategy(),
                    MeanReversionStrategy(),
                    TimeSeriesMomentumStrategy(),
                    TimeSeriesMomentumStrategy(
                        TimeSeriesMomentumConfig(
                            name="ts_momentum_adx", adx_min=20.0
                        )
                    ),
                    *pattern_strats,
                    *new_strategies,
                ],
            )
            # Громкая проверка загрузки (урок блока 9: стратегии молча
            # не грузились месяцами). Фактический состав (13.09.2026):
            # 7 ядро + 20 паттернов + 14 новых = 41; число НЕ дублируем
            # константой в тексте (устаревало), считаем от самого списка.
            _names = [getattr(s, "name", type(s).__name__) for s in pipeline.strategies]
            if len(pipeline.strategies) < 29:
                logger.error(
                    "Загружено стратегий %d — состав неполный "
                    "(7 ядро + 20 паттернов + 14 новых = 41): %s",
                    len(pipeline.strategies), _names,
                )
            else:
                logger.info("Загружено стратегий %d (7 ядро + 20 паттернов + 14 новых = 41)", len(pipeline.strategies))
        self.pipeline = pipeline
        # Аудит эпохи-2 (блок A): пер-стратегийный килл-свитч из статистики
        # (пересчёт каждую сессию = самовосстановление).
        try:
            self.apply_kill_switches_from_stats()
        except Exception as exc:
            logger.debug("kill-switch: %s", exc)
        self.broker = broker or self._make_broker()
        # Risk Engine — независимый слой защиты (master prompt §11):
        # дневные/недельные лимиты потерь, просадка, exposure, TRADING HALT.
        # Стратегии и ML не имеют права его обойти. Лимиты согласованы
        # с торговым конфигом, чтобы sizing не конфликтовал с чекером.
        # Block 6.1: Risk Management per spec — 1% risk, 3% daily loss, 10% max DD, max 3 positions
        # Единый RiskConfig (core.config): paper_runtime — явный override
        # поверх YAML/ENV. Числа paper-контура не меняются.
        _risk_cfg = RiskConfig.paper_runtime(
            risk_per_trade=Decimal(self.config.risk_per_trade_pct),
            max_open_positions=self.config.max_open_positions,
            max_exposure_pct=Decimal(self.config.max_total_exposure_pct),
            daily_loss_limit=Decimal("0.03"),  # 3% per spec
            weekly_loss_limit=Decimal("0.06"),
        )
        _risk_cfg.max_gross_exposure_pct = Decimal(self.config.max_total_exposure_pct)
        _risk_cfg.max_net_exposure_pct = Decimal(self.config.max_total_exposure_pct)
        self.risk = risk_engine or RiskEngine(_risk_cfg)
        # StateStore (Этап 3): единый атомарный checkpoint состояния.
        # Компонентные файлы остаются source of truth; бандл — для
        # crash-восстановления (например, утерянный paper_positions.json).
        # Путь бандла — рядом с ФАЙЛОМ БРОКЕРА (а не из config), чтобы
        # checkpoint всегда соответствовал именно этому компонентному
        # файлу (и тесты с hermetic-брокером не зацикливают models/).
        from ..core.state_store import StateStore

        self.state_store = StateStore(
            Path(self.broker.state_path).parent / "state_bundle.json"
        )
        try:
            bundle = self.state_store.load()
            if bundle is not None:
                self.state_store.restore_broker(self.broker, bundle)
        except Exception as exc:
            logger.debug("state bundle restore: %s", exc)
        # Exit Manager (Этап 4): обязательные safety-выходы с причинами
        # (MAX_HOLD / VOL_EXPANSION / BTC_PANIC) поверх ExitController.
        from .exit_manager import ExitManager

        self.exit_manager = ExitManager(exchange=self.exchange, broker=self.broker)
        # Единая проверка «можно ли входить прямо сейчас»: расписание/бюджет
        # часов, новости, волатильность, спред, дисбаланс стакана.
        self.safety = MarketSafety()
        # Research memory: одна статистика на движок+пайплайн, чтобы
        # meta-выбор и запись уроков смотрели на один источник (TZ §14).
        if pipeline is not None and getattr(pipeline, "stats_store", None) is not None:
            self.stats_store = pipeline.stats_store
        else:
            from .strategy_stats import StrategyStatsStore

            self.stats_store = StrategyStatsStore(Path(self.config.stats_path))
        # NO_TRADE — тоже результат модели (TZ §12): журнал + исходы.
        from ..ml.no_trade_observations import NoTradeObservationLog

        self.obs_log = NoTradeObservationLog(
            observations_path=Path(self.config.no_trade_observations_path),
            outcomes_path=Path(self.config.no_trade_outcomes_path),
        )
        # Hypothesis Engine (TZ §9): lifecycle гипотез + live-мониторинг
        # деградации (ACTIVE -> WEAKENING при ухудшении статистики).
        from ..ml.hypothesis_engine import HypothesisStore

        self.hypotheses = HypothesisStore(Path(self.config.hypotheses_path))
        # Exit Controller (TZ §16/§17): применяет план выхода только если
        # Hypothesis Engine допустил вариант до ACTIVE.
        from .exit_controller import ExitController

        self.exit_controller = ExitController(
            self.hypotheses, smart_default=self.config.smart_exit_default
        )
        # B8 этап 2: единый исполнитель плана выхода (docs/EXIT_PLAN_MAP.md).
        # Правила — в exit_plan.py (общие с бэктестером); параметры
        # safety — из exit_manager.config; гипотезы/смарт-флаг — из
        # exit_controller (совместимость с прежними слоями).
        from .exit_plan import ExitPlanEngine

        self.plan_engine = ExitPlanEngine(
            exit_controller=self.exit_controller,
            exit_manager=self.exit_manager,
        )
        # Model Registry (TZ §18): живому пайплайну отдаём только
        # ACTIVE (production) модель; без неё пайплайн работает как
        # раньше (ml_probability = None). Сбой загрузки не роняет бота.
        if getattr(pipeline, "model", None) is None:
            try:
                from ..ml.model_registry import get_registry
                from ..ml.model_trainer import MLModel

                prod = get_registry().get_production_model()
                if prod is not None and prod.model_path and Path(prod.model_path).exists():
                    loaded = MLModel.load(prod.model_path)
                    if not getattr(loaded, "is_fitted", False) or loaded.model is None:
                        logger.warning(
                            "ML model schema mismatch or unfitted — pipeline без ML"
                        )
                        pipeline.model = None
                    else:
                        pipeline.model = loaded
                        logger.info(
                            "ML model из registry (production): %s", prod.version
                        )
            except Exception as exc:
                logger.debug("registry model load: %s", exc)
        self._last_bar_ts: dict[str, int] = {}
        self._running = False
        self._capital_synced = False
        self._risk_synced = False
        # Бэклог A6: кэш Instrument (tick/step/min_notional) по символам.
        self._instruments_cache: dict[str, Any] = {}
        self._minute_bucket: int | None = None

    def _make_broker(self, initial_capital: Decimal | None = None) -> PaperBroker:
        """Брокер с реальными издержками (fee/slippage) по торговому конфигу."""
        if (
            self.config.fee_pct == BINGX_PERPS_TAKER_FEE
            and self.config.slippage_pct == Decimal("0.001")
        ):
            # Дефолт — биржевой пресет перпов (taker 0.05% / maker 0.02%).
            cost_kwargs: dict[str, Any] = {
                "cost_model": bingx_perps_cost_model(
                    slippage_pct=self.config.slippage_pct
                )
            }
        else:
            # Кастомные издержки (тесты/эксперименты) — плоская модель.
            cost_kwargs = {
                "fee_pct": self.config.fee_pct,
                "slippage_pct": self.config.slippage_pct,
            }
        kwargs: dict[str, Any] = dict(
            state_path=Path(self.config.state_path),
            trades_path=Path(self.config.trades_path),
            **cost_kwargs,
            # Потолок плеча движка (иначе брокер зажмёт своим дефолтом 2).
            max_leverage=Decimal(self.config.leverage_max),
        )
        if initial_capital is not None:
            kwargs["initial_capital"] = initial_capital
        return PaperBroker(**kwargs)

    async def sync_capital(self) -> Decimal:
        """Синхронизировать торговый капитал с фьючерсным счётом BingX.

        Если заданы BINGX_API_KEY/BINGX_API_SECRET, в управление берётся
        ПОЛОВИНА оценки фьючерсного (USDT-M) счёта в USDT, как просил
        владелец. Это масштаб «сколько реально есть», а не зашитые 2000.
        Без ключей остаёмся на дефолтном капитале.
        """
        if self._capital_synced:
            return self.broker.initial_capital
        try:
            bals = await self.exchange.get_account_balance()
            # Оцениваем портфель в USDT по текущим ценам.
            total_usdt = Decimal("0")
            for asset, b in bals.items():
                if asset == "USDT":
                    total_usdt += b.total
                else:
                    try:
                        t = await self.exchange.get_ticker(f"{asset}-USDT")
                        if t and t.get("last"):
                            total_usdt += b.total * Decimal(str(t["last"]))
                    except Exception:
                        # Нет пары к USDT — учитываем как есть, не валимся.
                        continue
            # Половина бюджета в управлении.
            cap = (total_usdt / Decimal("2")).quantize(Decimal("0.01"))
            if self.broker.positions:
                logger.info(
                    "Фьючерсный счёт=%.2f USDT, половина=%.2f, но есть позиции — "
                    "продолжаю с %s", total_usdt, cap, self.broker.initial_capital,
                )
            else:
                logger.info(
                    "Фьючерсный счёт BingX=%.2f USDT; в управлении половина=%.2f USDT",
                    total_usdt, cap,
                )
                self.broker = self._make_broker(cap)
            self._capital_synced = True
            return cap
        except Exception as exc:
            logger.debug("Не смог синхронизировать капитал: %s", exc)
        self._capital_synced = True
        return self.broker.initial_capital

    def _sync_risk_equity(self) -> None:
        """Синхронизировать капитал Risk Engine с NET-капиталом брокера.

        Бэклог аудита A1 (H3): раньше риск-движок видел только realized
        (initial + закрытые PnL), и плавающая просадка была ему невидима
        до закрытия позиций — просадка/HWM/сайзинг запаздывали на весь
        цикл удержания. Теперь equity риск-движка = ликвидационный
        капитал (realized + плавающая по mark). set_capital — абсолютное
        значение из брокера (источник правды), поэтому += pnl в
        record_trade внутри шага не даёт двойного счёта.
        """
        try:
            self.risk.set_capital(self.broker.net_equity, self.broker.initial_capital)
        except Exception as exc:
            logger.debug("risk equity sync failed: %s", exc)

    def _sync_risk_state(self) -> None:
        """Однократно за сессию восстановить риск-состояние из персиста.

        GitHub Actions поднимает свежий процесс на каждой 5-минутной
        сессии, поэтому дневной/недельный PnL, high water mark и
        HALT-статус пересобираются из ``paper_trades.jsonl`` (источник
        истины уже персистится в CI). Это делает лимиты потерь и
        TRADING HALT живыми между сессиями, а не только внутри одной.
        """
        if self._risk_synced:
            return
        trades: list[dict] = []
        try:
            if self.broker.trades_path.exists():
                for line in self.broker.trades_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))
        except Exception as exc:
            logger.debug("Не прочитал paper_trades для risk-состояния: %s", exc)
        self.risk.restore_from_trades(trades, self.broker.initial_capital)
        # Фактическая оценка брокера приоритетнее кривой из файла, если
        # состояние было правлено вручную. Бэклог A1: риск-движку — NET
        # ликвидационный капитал (realized + плавающая по mark), а не
        # только realized: просадка/HWM видят рынок, а не только прошлое.
        self._sync_risk_equity()
        for pos in self.broker.positions:
            # Meta для portfolio-лимитов (Этап 5): id-позиции без
            # номинала не видны в gross/net/группе — передаём явно.
            self.risk.add_position(
                pos.id,
                symbol=pos.symbol,
                side=pos.direction,
                notional=abs(pos.quantity * pos.entry_price),
            )
        self._risk_synced = True
        logger.info(
            "Risk state восстановлен: equity=%s, net_equity=%s, daily_pnl=%s, "
            "weekly_pnl=%s, state=%s, trading_enabled=%s, open_positions=%d",
            self.broker.equity, self.broker.net_equity,
            self.risk.daily_pnl, self.risk.weekly_pnl,
            self.risk.risk_state.value, self.risk.trading_enabled,
            len(self.broker.positions),
        )

    # ----------------------------------------------------------- market data
    async def fetch_context(self, symbol: str) -> MarketContext:
        candles: dict[str, list[models.Candle]] = {}
        for tf in self.config.timeframes:
            candles[tf] = await self.exchange.get_candles(
                symbol,
                timeframe=tf,
                limit=self.config.bars_per_tf.get(tf, 300),
            )
        # Набор владельца (п.7): дневные бары — вне config.timeframes,
        # чтобы их не видел никто, кроме стратегий с
        # preferred_timeframe="1d" и дневных гейтов. Ошибка — не фатальна:
        # без дневных стратегия промолчит, гейт сработает фейл-оупен.
        try:
            candles[self.config.htf_daily_tf] = await self.exchange.get_candles(
                symbol,
                timeframe=self.config.htf_daily_tf,
                limit=self.config.htf_daily_bars,
            )
        except Exception as exc:
            logger.warning("Дневные свечи %s недоступны (фейл-оупен): %s", symbol, exc)
        primary = candles.get("5m") or candles.get("15m") or candles.get("1h") or []
        if not primary:
            raise RuntimeError(f"Нет данных по {symbol}")
        price = Decimal(str(primary[-1].close))
        try:
            orderbook = await self.exchange.get_orderbook(symbol, depth=20)
        except Exception as exc:
            logger.warning("Стакан %s недоступен: %s", symbol, exc)
            orderbook = None
        return MarketContext(
            symbol=symbol,
            current_price=price,
            candles=candles,
            orderbook=orderbook,
        )

    # ----------------------------------------------------------- sizing (Block 6.2)
    def _effective_entry_price(self, price: Decimal, direction: str) -> Decimal:
        """Фактическая цена входа с slippage — ровно как исполнит брокер.

        Аудит A2 / решение владельца (13.09.2026): сайзинг и риск-гейт
        считаются от этой цены, а не от сигнальной. Иначе фактический риск
        (fill − stop) систематически превышает бюджет на slip/d, где d —
        относительная дистанция стопа; выравнивание с бэктестером
        (PR #73) и с реальным исполнением PaperBroker.
        """
        try:
            cm = getattr(self.broker, "cost_model", None)
            if cm is not None:
                return cm.effective_entry_price(price, direction)
            slip = Decimal(str(getattr(self.broker, "slippage_pct", 0) or 0))
        except Exception:
            logger.debug("effective entry: fallback без slippage", exc_info=True)
            return price
        if direction in ("long", "buy"):
            return price * (Decimal("1") + slip)
        return price * (Decimal("1") - slip)

    def _position_size(
        self,
        equity: Decimal,
        entry: Decimal,
        stop: Decimal,
        ml_confidence: float | None = None,
        atr_pct: float | None = None,
        strategy: str = "",
    ) -> Decimal:
        # Use new position_sizer with Kelly, ML confidence, volatility adjustments
        try:
            from ..engines.position_sizer import calculate_position_size
            # Try to get strategy stats for Kelly
            # COLD-START PRIOR (B9): пока у стратегии нет статистики
            # (stats_store.get_any -> sample_size < 10), Kelly-сайзинг
            # считает по этому фиксированному prior: win_rate 0.55,
            # средний выигрыш 1.5R, средний проигрыш 1.0R. Значения и
            # имена НЕ менять в рамках B9 — калибровка отдельной задачей.
            win_rate = 0.55
            avg_win = 1.5
            avg_loss = 1.0
            try:
                if strategy:
                    # Get ANY regime stats for this strategy
                    bucket = self.stats_store.get_any(strategy, "")
                    if bucket and bucket.sample_size >= 10:
                        win_rate = bucket.win_rate
                        avg_win = bucket.avg_win_r or 1.5
                        avg_loss = abs(bucket.avg_loss_r) or 1.0
            except Exception:
                pass
            qty = calculate_position_size(
                equity=equity,
                entry_price=entry,
                stop_loss=stop,
                risk_per_trade_pct=self.config.risk_per_trade_pct,
                win_rate=win_rate,
                avg_win_r=avg_win,
                avg_loss_r=avg_loss,
                ml_confidence=ml_confidence,
                atr_pct=atr_pct,
                max_notional_pct=self.config.max_notional_pct,
            )
            return qty
        except Exception:
            # Fallback to simple calculation
            try:
                risk_amount = equity * self.config.risk_per_trade_pct
                stop_distance = abs(entry - stop)
                if stop_distance <= 0:
                    return Decimal("0")
                qty = risk_amount / stop_distance
                max_notional = equity * self.config.max_notional_pct
                if qty * entry > max_notional:
                    qty = max_notional / entry
                return qty.quantize(Decimal("0.000001"))
            except Exception:
                return Decimal("0")

    # ------------------------------------------- анти-дребезг (кулдаун)
    @staticmethod
    def _cooldown_key(strategy: str, symbol: str, side: str) -> str:
        # Имя стратегии в ключе — ровно как в бакетах strategy_stats
        # (там ключ начинается с "стратегия|...").
        return f"{strategy}|{symbol}|{side}"

    def _cooldown_ttl_ms(self) -> int:
        return int(self.config.reentry_cooldown_minutes) * 60_000

    def _cooldown_blocks(self, strategy: str, symbol: str, side: str) -> bool:
        """Активен ли кулдаун анти-дребезга для этой тройки."""
        return (
            self.broker.cooldown_remaining_ms(
                self._cooldown_key(strategy, symbol, side)
            )
            > 0
        )

    def apply_kill_switches_from_stats(self) -> list[str]:
        """Пер-стратегийный килл-свитч из статистики (блок A).

        Агрегирует бакеты strategy_stats.json по имени стратегии. Правило:
        n>=5 и PF=wins/|losses|<1.0 → убрать стратегию из
        pipeline.strategies + logger.error. Именно убрать из списка: флаг
        enabled новые стратегии игнорируют. Вызывается из __init__ (раз в
        сессию) — выправившаяся статистика возвращает стратегию обратно.
        """
        disabled: list[str] = []
        try:
            path = Path(self.config.stats_path)
            if not path.exists():
                return disabled
            data = json.loads(path.read_text(encoding="utf-8"))
            buckets = data.get("buckets") or {}
            agg: dict[str, dict[str, float]] = {}
            for key, row in buckets.items():
                if not isinstance(row, dict):
                    continue
                name = str(key).split("|")[0].strip()
                if not name:
                    continue
                a = agg.setdefault(name, {"n": 0.0, "w": 0.0, "l": 0.0})
                a["n"] += float(row.get("sample_size") or 0)
                a["w"] += float(row.get("wins_sum_r") or 0.0)
                a["l"] += float(row.get("losses_sum_r") or 0.0)
            loaded = [
                getattr(st, "name", type(st).__name__)
                for st in (self.pipeline.strategies or [])
            ]
            for name, a in agg.items():
                if name not in loaded:
                    continue  # мёртвые/переименованные имена — молча
                if a["n"] >= 5 and a["l"] < 0 and (a["w"] / abs(a["l"])) < 1.0:
                    pf = a["w"] / abs(a["l"])
                    self.pipeline.strategies = [
                        st
                        for st in self.pipeline.strategies
                        if getattr(st, "name", type(st).__name__) != name
                    ]
                    logger.error(
                        "KILL-SWITCH %s: n=%d PF=%.2f (wins %+.2fR / losses %+.2fR)"
                        " — убрана до выправления статистики",
                        name, int(a["n"]), pf, a["w"], a["l"],
                    )
                    disabled.append(name)
        except Exception as exc:
            logger.debug("kill-switch read failed: %s", exc)
        return disabled

    def _risk_check_and_adjust(
        self,
        symbol: str,
        side: str,
        cand: Any,
        size: Decimal,
        entry_price: Decimal | None = None,
    ) -> Decimal | None:
        """Прогнать сделку через Risk Engine; вернуть допустимый размер.

        Возвращает ``None``, если вход запрещён (TRADING HALT, дневной/
        недельный лимит потерь, лимиты, которые нельзя закрыть уменьшением
        размера). Иначе — исходный или уменьшенный до лимита размер.

        ``entry_price`` — фактическая цена входа (со slippage, аудит A2):
        риск-движок должен видеть тот же стоп-зазор, что и рынок.
        """
        _entry = entry_price if entry_price is not None else cand.entry_price
        for _ in range(2):
            verdict = self.risk.check_trade(
                symbol=symbol,
                side=side,
                entry_price=_entry,
                stop_loss=cand.stop_loss,
                take_profit=cand.take_profit,
                proposed_size=size,
                strategy_name=cand.strategy,
            )
            if verdict.approved:
                return size
            adjusted = verdict.details.get("adjusted_size")
            if not self.risk.trading_enabled or not adjusted:
                logger.warning(
                    "RISK: вход %s запрещён (%s): %s",
                    symbol, self.risk.risk_state.value, verdict.reason,
                )
                reason_str = str(verdict.reason or "")
                dedup_key, message, severity = self._halt_alert_for(reason_str)
                if dedup_key is not None:
                    self._dispatch_halt_alert(dedup_key, message, severity)
                return None
            size = Decimal(str(adjusted)).quantize(Decimal("0.000001"))
            if size <= 0:
                logger.warning(
                    "RISK: размер %s упирается в лимит: %s", symbol, verdict.reason
                )
                return None
        # Два прохода не помогли (лимиты пересекаются) — не входим.
        logger.warning("RISK: не уложился в лимиты для %s, вход пропущен", symbol)
        return None


    # ------------------------------------------------------- HALT-алерты
    # Причины -> (dedup-ключ, текст по-русски, severity).
    # Эмодзи в тексте НЕТ: send_alert подставляет своё по severity
    # (один источник эмодзи — двойного больше нет).
    _HALT_REASON_RU: dict[str, str] = {
        "Trading is disabled": "торговля отключена риск-двигателем",
    }

    def _halt_alert_for(self, reason_str: str) -> tuple[str | None, str, str]:
        """Построить HALT-алерт из причины Risk Engine.

        Ключи: loss_limit_daily / loss_limit_weekly / state_<state>.
        (None, "", "") — если причина не относится к HALT-алертам.
        """
        if "Daily loss limit" in reason_str or "Weekly loss limit" in reason_str:
            daily = "Daily loss limit" in reason_str
            dedup_key = "loss_limit_daily" if daily else "loss_limit_weekly"
            return dedup_key, self._halt_limit_message(reason_str, daily), "warning"
        if not self.risk.trading_enabled:
            state_val = str(self.risk.risk_state.value)
            reason_ru = self._HALT_REASON_RU.get(reason_str, reason_str)
            message = (
                f"Остановка торговли ({state_val}): {reason_ru}.\n"
                + self._halt_open_positions_line()
            )
            severity = "critical" if state_val == "EMERGENCY" else "warning"
            return f"state_{state_val}", message, severity
        return None, "", ""

    def _halt_limit_message(self, reason_str: str, daily: bool) -> str:
        """``... limit reached: X / Y`` -> русский текст с пояснением.

        Лимит считается по скользящему окну (24ч / 7 дней), а не по
        календарному дню — см. RiskEngine.restore_from_trades.
        """
        loss: Decimal | None = None
        limit: Decimal | None = None
        try:
            numbers = reason_str.split(":", 1)[1].strip().split(" / ")
            loss = Decimal(numbers[0])
            limit = Decimal(numbers[1])
        except Exception:
            pass
        if daily:
            title = "Остановка торговли (дневной лимит потерь)."
            period = "за 24 часа"
            limit_pct = self.risk.config.daily_loss_limit
            unblock = "пока 24-часовой убыток не уйдёт ниже лимита"
        else:
            title = "Остановка торговли (недельный лимит потерь)."
            period = "за 7 дней"
            limit_pct = self.risk.config.weekly_loss_limit
            unblock = "пока недельный убыток не уйдёт ниже лимита"
        lines = [title]
        capital = self.risk.initial_capital
        if loss is not None and limit is not None:
            lines.append(
                f"Убыток {period}: −{loss:.2f} USDT (лимит {limit:.2f}, "
                f"т.е. {float(limit_pct) * 100:g}% от {capital:.2f})."
            )
        else:
            lines.append(f"Убыток {period}: {reason_str}.")
        lines.append(f"Новые входы запрещены {unblock}.")
        lines.append(self._halt_open_positions_line())
        return "\n".join(lines)

    def _halt_open_positions_line(self) -> str:
        try:
            positions = list(self.broker.positions or [])
        except Exception:
            positions = []
        if not positions:
            return "Открытые позиции: нет"
        desc = ", ".join(f"{p.symbol} {p.direction}" for p in positions[:10])
        more = f" (+ещё {len(positions) - 10})" if len(positions) > 10 else ""
        return f"Открытые позиции: {desc}{more}"

    def _dispatch_halt_alert(self, dedup_key: str, message: str, severity: str) -> None:
        """Отправить HALT-алерт: раз в сутки на ключ (персистентный dedup).

        In-memory set — дедуп внутри сессии; halt_alerts.json — между
        сессиями (Actions каждые 5 минут поднимает новый процесс).
        """
        if dedup_key in self._halt_alerts_sent:
            return
        if halt_alerts.already_sent_today(self._halt_alerts_path, dedup_key):
            logger.info(
                "HALT-алерт %s уже отправлен сегодня (UTC) — повтор не шлём",
                dedup_key,
            )
            self._halt_alerts_sent.add(dedup_key)
            return
        self._halt_alerts_sent.add(dedup_key)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Нет event loop (юнит-тесты): синхронная отправка + отметка.
            sent = False
            if self._notifier is not None:
                try:
                    res = self._notifier(message, severity)
                    if asyncio.iscoroutine(res):
                        asyncio.run(res)
                    sent = True
                except Exception as exc:
                    logger.warning("HALT-алерт (%s) не отправлен: %s", dedup_key, exc)
            if sent:
                halt_alerts.mark_sent(self._halt_alerts_path, dedup_key)
            return
        # Живой контур: дожидаемся отправки, ТОЛЬКО тогда отмечаем ключ.
        # Не дождёмся/провалимся — без отметки, следующая сессия повторит.
        _spawn_background(self._send_halt_alert(message, severity, dedup_key))

    async def _send_halt_alert(self, text: str, severity: str, dedup_key: str) -> None:
        """Дождаться отправки HALT-алерта и пометить ключ как отправленный."""
        sent = False
        if self._notifier is not None:
            try:
                res = self._notifier(text, severity)
                if asyncio.iscoroutine(res):
                    await asyncio.wait_for(res, timeout=10.0)
                sent = True
            except Exception as exc:
                logger.warning("HALT-алерт (%s) не отправлен: %s", dedup_key, exc)
        if sent:
            halt_alerts.mark_sent(self._halt_alerts_path, dedup_key)
        else:
            logger.warning(
                "HALT-алерт (%s) не отправлен — повторим в следующей сессии",
                dedup_key,
            )

    async def _sync_perps_state(self, symbol: str, fallback_price: Any) -> None:
        """Подтянуть в брокер живые mark price и ставку фандинга.

        Биржа отдаёт оба значения одним запросом (premiumIndex). Если
        адаптер их не умеет (мок/legacy) — mark = цена последнего бара,
        фандинг остаётся дефолтным у брокера. Ошибки не валят тик.
        """
        broker = self.broker
        if not hasattr(broker, "update_mark_price"):
            return
        try:
            getter = getattr(self.exchange, "get_mark_and_funding", None)
            mark = rate = None
            if getter is not None:
                mark, rate = await getter(symbol)
            if mark is None:
                mark_getter = getattr(self.exchange, "get_mark_price", None)
                if mark_getter is not None:
                    mark = await mark_getter(symbol)
            if mark is None:
                mark = fallback_price
            broker.update_mark_price(symbol, Decimal(str(mark)))
            if rate is None:
                rate_getter = getattr(self.exchange, "get_funding_rate", None)
                if rate_getter is not None:
                    info = await rate_getter(symbol)
                    if isinstance(info, dict):
                        rate = info.get("rate")
            if rate is not None:
                broker.set_funding_rate(symbol, Decimal(str(rate)))
        except Exception as exc:
            logger.debug("perps sync %s: %s", symbol, exc)

    # ----------------------------------------------------------- main loop
    @staticmethod
    def _scaled_tp_with_stop(
        entry: Any, old_stop: Any, new_stop: Any, take_profit: Any, direction: str,
    ) -> Decimal | None:
        """Блок I: новый TP при расширении стопа (пропорционально R, cap 2.2×).

        Возвращает Decimal нового TP или None (не масштабировать: стоп не
        расширился, TP отсутствует или стоит не на своей стороне).
        """
        try:
            r0 = abs(float(entry) - float(old_stop))
            r1 = abs(float(entry) - float(new_stop))
            e = float(entry)
            tp = float(take_profit) if take_profit else 0.0
            tp_ok = (direction == "long" and tp > e) or (
                direction == "short" and 0 < tp < e
            )
            if r0 > 0 and r1 > r0 and tp_ok:
                scale = min(r1 / r0, 2.2)
                return Decimal(str(e + (tp - e) * scale))
        except Exception:
            pass
        return None

    @staticmethod
    def _build_ticker_map(ticker: Any) -> dict[str, float] | None:
        """Тикер -> {last, open24h} для MarketSafety (резкое движение 24ч).

        Бэклог аудита A4: клиент BingX возвращает реальный open24h
        (adapters/bingx/client.py:654 ``"open_24h"`` <- openPrice из
        swap/v2 quote/ticker), старый комментарий «без open24h» устарел.
        Раньше open24h всегда оценивали серединой (hi+lo)/2: после пампа,
        когда цена закрепилась у хая, hi≈lo≈last и сдвиг «исчезал» —
        safety-проверка проваливалась мимо. Середина диапазона теперь
        только fallback для адаптеров без поля (например, simulated.py).
        """
        if not ticker:
            return None
        last_f = float(ticker.get("last") or 0)
        hi = float(ticker.get("high_24h") or 0)
        lo = float(ticker.get("low_24h") or 0)
        open24 = float(ticker.get("open_24h") or ticker.get("open24h") or 0)
        if open24 <= 0:
            open24 = (hi + lo) / 2 if hi and lo else 0.0
        return {"last": last_f, "open24h": open24}

    async def _get_instrument(self, symbol: str) -> Any:
        """Instrument (tick/step/min_notional) с биржи, с кэшем (A6).

        Нет данных (адаптер не отдаёт / ошибка сети) — None: валидация
        ограничений пропускается, вход НЕ блокируется (fail-open: в live
        ордер отклонит сама биржа, в paper ограничений нет).
        """
        if symbol in self._instruments_cache:
            return self._instruments_cache[symbol]
        inst = None
        try:
            inst = await self.exchange.get_instrument(symbol)
        except Exception as exc:
            logger.debug("instrument %s недоступен: %s", symbol, exc)
            inst = None
        self._instruments_cache[symbol] = inst
        return inst

    async def _apply_instrument_constraints(
        self, symbol: str, entry_price: Decimal, size: Decimal
    ) -> Decimal | None:
        """Бэклог A6: сечение размера по ограничениям инструмента.

        step_size — размер округляется ВНИЗ до шага (риск никогда не
        увеличивается); min_quantity / min_notional — не укладываемся,
        вход отклоняем (None); tick_size — офф-тик входной цены лишь
        логируется (paper исполняет по заданной цене, в live биржа
        отклонит — отдельный предмет live-контура).
        Без данных инструмента — size как есть (fail-open).
        """
        inst = await self._get_instrument(symbol)
        if inst is None:
            return size
        try:
            step = Decimal(str(getattr(inst, "step_size", "") or 0))
            if step and step > 0:
                size = (size // step) * step
            min_qty = Decimal(str(getattr(inst, "min_quantity", "") or 0))
            if min_qty and size < min_qty:
                logger.info(
                    "%s: size %s < min_quantity %s — вход пропущен (A6)",
                    symbol, size, min_qty,
                )
                return None
            min_notional = Decimal(str(getattr(inst, "min_notional", "") or 0))
            if min_notional and size * entry_price < min_notional:
                logger.info(
                    "%s: notional %s < min_notional %s — вход пропущен (A6)",
                    symbol, size * entry_price, min_notional,
                )
                return None
            tick = Decimal(str(getattr(inst, "tick_size", "") or 0))
            if tick and tick > 0:
                if abs(entry_price - (entry_price // tick) * tick) > 0:
                    logger.debug(
                        "%s: входная цена %s не на тике %s (A6)",
                        symbol, entry_price, tick,
                    )
        except Exception as exc:
            logger.debug("instrument constraints %s: %s", symbol, exc)
            return size
        return size

    async def process_symbol(self, symbol: str) -> list[Any]:

        # Риск-состояние (лимиты, HALT) живое между CI-сессиями:
        # восстанавливаем из персиста перед любым решением о входе.
        self._sync_risk_state()
        ctx = await self.fetch_context(symbol)
        primary = ctx.candles.get("5m") or ctx.candles.get("1h") or []
        if not primary:
            return []

        # BTC PANIC (Этап 4): это ВЫХОД, а не вход — работает независимо
        # от risk-состояния (HALT не отменяет обязательные выходы).
        if self.exit_manager.btc_panic:
            panic = self.exit_manager.flatten_symbol(
                symbol, float(ctx.current_price)
            )
            if panic:
                self._record_closed(panic)
            return panic

        # Обновляем уже открытые позиции по последнему бару.
        # Экстремумы обновляем до решения: Exit Controller скорректирует
        # стопы ДО проверки срабатывания на этом же баре (TZ §16).
        last_bar = primary[-1]
        self.broker.update_extremes(last_bar)

        # Решение по стратегиям — вычисляем до обработки выходов и
        # проверки «есть ли позиция», чтобы флип-стратегии (ts_momentum)
        # могли перевернуть/закрыть её.
        # Решение + метрики (Этап 7): счётчик по (action, reason_code)
        # и латентность decide. reason_code — кодированный (низкая
        # кардинальность лейблов), свободные причины не в лейблы.
        _dec_started = time.monotonic()
        decision = await self.pipeline.decide(ctx)
        DECISIONS_TOTAL.labels(
            action=decision.action,
            reason=decision.reason_code or "none",
        ).inc()
        DECISION_LATENCY.observe(time.monotonic() - _dec_started)
        regime_name = str(
            (decision.diagnostics.get("regime") or {}).get("regime", "")
        )

        # B8 этап 2 (решения владельца 11.09): единый план выходов.
        # Иерархия: жёсткий стоп и тейк СТАРШЕ плана — поэтому STOP-правила
        # плана (D1-структурный по закрытым барам + БУ + трейлинг; только
        # подтягивание) применяются ДО проверки уровней (TZ §16), а
        # FORCED-правила плана — ПОСЛЕ стопов/тейков и ликвидаций.
        # Ликвидация в paper остаётся после стопов по mark price (не менялось).
        try:
            self.plan_engine.adjust_stops(
                self.broker, symbol,
                [p for p in self.broker.positions if p.symbol == symbol],
                ctx.candles, list(primary), last_bar,
            )
        except Exception as exc:
            logger.debug("plan adjust_stops: %s", exc)

        closed = self.broker.check_exits(last_bar)

        # Перпы: живые mark/фандинг в брокер + ликвидации по mark price.
        # Стопы/тейки уже проверены выше — они срабатывают раньше ликвидации.
        try:
            await self._sync_perps_state(symbol, last_bar.close)
            liq_closed = self.broker.check_liquidations(symbol)
            closed = closed + liq_closed
        except Exception as exc:
            logger.debug("liquidation check %s: %s", symbol, exc)

        # FORCED-правила плана (MAE_CUT/TIME_STOP/MOMENTUM_EXIT/REGIME_EXIT
        # + safety MAX_HOLD/VOL_EXPANSION) — по живой цене, для переживших
        # уровни позиций.
        try:
            closed = closed + self.plan_engine.forced_closes(
                self.broker, symbol,
                [p for p in self.broker.positions if p.symbol == symbol],
                ctx.candles, last_bar.close, ctx.current_price, regime_name,
            )
        except Exception as exc:
            logger.debug("plan forced_closes: %s", exc)

        # Закрытые на этом баре сделки → реальные уроки + уведомления.
        if closed:
            self._record_closed(closed)

        # --- Research: NO_TRADE — результат модели, а не «пустой цикл».
        # Дедупликация по стабильному id (bar_time) — повторная обработка
        # того же бара дубль не создаёт (TZ §30).
        if decision.action == "NO_TRADE":
            self._record_no_trade(symbol, decision, list(primary))

        # --- Research: обогащение прошлых NO_TRADE будущим исходом.
        # Только на новом баре (throttle) — pending() читает файл.
        bar_ts = int(last_bar.open_time)
        if self._last_bar_ts.get(symbol) != bar_ts:
            self._last_bar_ts[symbol] = bar_ts
            try:
                self.obs_log.enrich({symbol: list(primary)})
            except Exception as exc:
                logger.debug("no_trade enrich: %s", exc)

        self._log_decision_line(symbol, decision)

        # CLOSE: режим тренда окончился — закрываем позицию без входа.
        if decision.action == "CLOSE":
            newly = self.broker.close_positions(symbol, ctx.current_price, "flat_regime")
            if newly:
                self._record_closed(newly)
                logger.info("CLOSE %s по flat-сигналу (%d позиций)", symbol, len(newly))
                closed = closed + newly
            return closed

        has_position = any(p.symbol == symbol for p in self.broker.positions)

        # FLIP: переворот — закрываем противоположную позицию и входим заново.
        if decision.action == "FLIP" and has_position:
            newly = self.broker.close_positions(symbol, ctx.current_price, "flip")
            if newly:
                self._record_closed(newly)
                logger.info("FLIP %s: закрыто %d, открываю новое направление", symbol, len(newly))
                closed = closed + newly
            has_position = False

        # Одна позиция на символ (кроме только что обработанного флипа).
        if has_position:
            return closed

        if decision.action == "NO_TRADE" or decision.candidate is None:
            logger.debug("NO_TRADE %s: %s", symbol, decision.reasons)
            return closed

        # Анти-дребезг (решение владельца 12.09): та же стратегия + тот же
        # символ + сторона не входят 20 минут после выхода по стопу.
        # Реестр живёт в существующем state брокера (процесс пересоздаётся
        # каждые ~5 минут — в памяти не удержать).
        if self._cooldown_blocks(
            decision.candidate.strategy, symbol, decision.candidate.direction
        ):
            _cd_left = self.broker.cooldown_remaining_ms(
                self._cooldown_key(
                    decision.candidate.strategy, symbol,
                    decision.candidate.direction,
                )
            )
            logger.info(
                "COOLDOWN symbol=%s strategy=%s side=%s осталось=%d мин",
                symbol,
                decision.candidate.strategy,
                decision.candidate.direction,
                -(-_cd_left // 60000),
            )
            return closed

        # Дисциплина капитала: лимит числа позиций, однонаправленных
        # входов и суммарной экспозиции. Защищает от пачки
        # коррелированных сделок, которые стопнутся одновременно.
        open_positions = list(self.broker.positions)
        if len(open_positions) >= self.config.max_open_positions:
            return closed
        total_notional = sum(
            float(p.entry_price) * float(p.quantity) for p in open_positions
        )
        # Бэклог A1: экспозиция меряется от NET капитала (с плавающей).
        equity = float(self.broker.net_equity)
        if equity > 0 and total_notional / equity >= float(
            self.config.max_total_exposure_pct
        ):
            return closed

        # Рыночная «безопасность»: расписание/бюджет часов, новости,
        # волатильность, спред, дисбаланс стакана. Если не прошли —
        # пропускаем вход (это главный щит от слива депозита).
        try:
            ticker = await self.exchange.get_ticker(symbol)
        except Exception:
            ticker = {}
        ob = ctx.orderbook
        book_dict = None
        if ob is not None and getattr(ob, "bids", None) and getattr(ob, "asks", None):
            bids_depth = sum(float(b.price) * float(b.quantity) for b in ob.bids[:10])
            asks_depth = sum(float(a.price) * float(a.quantity) for a in ob.asks[:10])
            book_dict = {
                "best_bid": float(ob.bids[0].price),
                "best_ask": float(ob.asks[0].price),
                "bids_depth": bids_depth,
                "asks_depth": asks_depth,
            }
        ticker_map = self._build_ticker_map(ticker)
        verdict = self.safety.check(
            symbol,
            ticker=ticker_map,
            orderbook=book_dict,
            candles=list(primary),
        )
        if not verdict.allowed:
            logger.info("%s: вход запрещён: %s", symbol, "; ".join(verdict.reasons))
            return closed

        cand = decision.candidate

        # Не набираем слишком много позиций в одну сторону.
        wanted_dir = "long" if cand.direction == "long" else "short"
        same_dir = sum(
            1 for p in self.broker.positions if p.direction == wanted_dir
        )
        if same_dir >= self.config.max_same_direction:
            logger.info(
                "%s: лимит %s-позиций достигнут (%d), пропуск",
                symbol, wanted_dir, same_dir,
            )
            return closed

        # Структурный стоп (по теням): если стоп стратегии стоит внутри
        # зоны шума — выносим за ближайший свинг-экстремум с буфером.
        # ВАЖНО: до сайзинга, чтобы объём считался уже по новому R и
        # риск на сделку не вырос.
        if self.config.structural_stop and primary:
            try:
                from .exit_controller import structural_stop

                new_stop = structural_stop(
                    cand.entry_price, cand.stop_loss, wanted_dir, list(primary)
                )
                if new_stop != cand.stop_loss:
                    logger.info(
                        "STRUCT-STOP %s %s: %s -> %s (за свинг по теням)",
                        symbol, wanted_dir, cand.stop_loss, new_stop,
                    )
                    _old_stop = cand.stop_loss
                    cand.stop_loss = new_stop
                    # Блок I: стоп расширился — двигаем TP пропорционально,
                    # иначе фактический RR падает ниже гейта (edge пайплайна
                    # посчитан ДО расширения).
                    _tp_scaled = self._scaled_tp_with_stop(
                        cand.entry_price, _old_stop, new_stop,
                        cand.take_profit, wanted_dir,
                    )
                    if _tp_scaled is not None:
                        logger.info(
                            "STRUCT-TP %s %s: %s -> %s (за стопом)",
                            symbol, wanted_dir, cand.take_profit, _tp_scaled,
                        )
                        cand.take_profit = _tp_scaled
            except Exception as exc:
                logger.debug("structural_stop: %s", exc)

        # Block 6.2: position sizing with ML confidence and volatility
        _atr_pct = None
        try:
            # ATR из технических диагностик. Контракт единиц (бэклог A3):
            # ЕДИНЫЙ нормализованный atr_pct — ATR в % цены (так пишет
            # TechnicalReport.to_dict, так ожидает position_sizer).
            # Старый fallback tech.get("atr") убран: абсолютный ATR
            # прочитался бы как проценты и ужал размер в 3+ раза.
            tech = decision.diagnostics.get("technical") or {}
            _atr_pct = float(tech.get("atr_pct") or 0) or None
        except Exception:
            pass
        _ml_conf = None
        try:
            _ml_conf = float(cand.ml_probability) if cand.ml_probability is not None else float(cand.confidence) if cand.confidence else None
        except Exception:
            pass
        # Бэклог A1: сайзинг и риск-движок видят NET ликвидационный
        # капитал (mark по символу уже подтянут _sync_perps_state выше).
        self._sync_risk_equity()
        # Аудит A2 (решение владельца 13.09.2026): сайзинг и риск-гейт
        # считаются от ФАКТИЧЕСКОЙ цены входа (сигнальная ± slippage) —
        # иначе реальный риск на стопе больше бюджета на slip/d.
        entry_for_risk = self._effective_entry_price(cand.entry_price, wanted_dir)
        size = self._position_size(
            self.broker.net_equity,
            entry_for_risk,
            cand.stop_loss,
            ml_confidence=_ml_conf,
            atr_pct=_atr_pct,
            strategy=cand.strategy,
        )
        if size <= 0:
            logger.info("%s: size=0, пропускаю", symbol)
            return closed
        # Бэклог A6: биржевые ограничения инструмента (step/min_qty/
        # min_notional/tick) — до открытия, в paper broker их нет.
        size = await self._apply_instrument_constraints(
            symbol, entry_for_risk, size
        )
        if size is None or size <= 0:
            logger.info("%s: size=0 (инструмент), пропускаю", symbol)
            return closed

        # ---- Risk Engine: независимый слой защиты (master prompt §11).
        # Дневные/недельные лимиты потерь, просадка, exposure, HALT.
        # Если торговля остановлена — вход запрещён независимо от силы
        # сигнала. Если размер можно уменьшить — уменьшаем и проверяем.
        size = self._risk_check_and_adjust(
            symbol, wanted_dir, cand, size, entry_price=entry_for_risk
        )
        if size is None or size <= 0:
            return closed

        cand_features = cand.features or {}
        regime_info = decision.diagnostics.get("regime") or {}
        # Плечо: берём максимум только при сильном EV — платить фандинг
        # и рисковать ликвидацией ради слабого сетапа нельзя. Фандинг
        # закладывается брокером в PnL при закрытии.
        leverage = 1
        if self.config.leverage_max > 1:
            ev_r = float(cand_features.get("ev_r") or 0.0)
            leverage = leverage_for(
                confidence=float(cand.confidence or 0.0),
                ev_r=ev_r,
                entry_price=float(cand.entry_price),
                stop_loss=float(cand.stop_loss),
                max_leverage=self.config.leverage_max,
                min_ev_r=float(self.config.leverage_min_ev_r),
                maintenance_margin_pct=float(
                    getattr(self.broker, "maintenance_margin_pct", 0.005)
                ),
            )
            if leverage > 2:
                logger.info(
                    "LEV-LADDER %s: conf=%.2f ev=%.2fR -> плечо %dx",
                    symbol, float(cand.confidence or 0.0), ev_r, leverage,
                )
        try:
            # B8 этап 2: ОДИН тейк плана (решение владельца — зона 2-2.3R
            # стопа). Уровень считает exit_plan.clamp_take_rr; брокер
            # только исполняет уровень (take_levels).
            _dir = "long" if cand.direction == "long" else "short"
            _plan_take = None
            _take_levels = None
            if not bool(cand_features.get("no_take_profit")) and cand.take_profit:
                from .exit_plan import clamp_take_rr, smart_params

                _sp = smart_params()
                _plan_take = clamp_take_rr(
                    cand.entry_price, cand.stop_loss, cand.take_profit,
                    _dir, _sp.take_rr_min, _sp.take_rr_max,
                )
                _take_levels = [_plan_take]
            pos = self.broker.open_position(
                symbol=symbol,
                direction=_dir,
                entry_price=cand.entry_price,
                stop_loss=cand.stop_loss,
                take_profit=cand.take_profit,
                quantity=size,
                strategy=cand.strategy,
                no_take_profit=bool(cand_features.get("no_take_profit")),
                take_levels=_take_levels,
                regime=str(regime_info.get("regime", "")),
                timeframe=cand.timeframe,
                # A2 (МТЗ §10): композитный ключ осей Regime 2.0 — прокидывается
                # до закрытия в статистику бакетов; пусто => legacy-режим.
                regime_axes=str(regime_info.get("axes_key") or ""),
                leverage=leverage,
                notes={
                    "score": cand.total_score,
                    "ml_probability": cand.ml_probability,
                    "edge_pct": cand.expected_edge_pct,
                    "ev_r": cand_features.get("ev_r"),
                    "ev_confidence": cand_features.get("ev_confidence"),
                    "rr": cand.risk_reward,
                    "leverage": leverage,
                },
            )
            # B8 этап 2: план выбирается НА ВХОДЕ и закрепляется за
            # позицией (аудит: какой план был активен при открытии).
            try:
                pos.plan_variant = self.plan_engine._plan(pos, str(regime_info.get("regime", "")))[0]
                pos.plan_take = _plan_take
                self.broker.save()
            except Exception as exc:
                logger.debug("plan attach: %s", exc)
        except ValueError as exc:
            # Маржа/ликвидация не позволили плечо — сделка отменяется
            # целиком (fail-closed), а не «как-нибудь без плеча».
            logger.warning(
                "%s: сделка отменена брокером (%s) — плечо %s",
                symbol, exc, leverage,
            )
            return
        # Книга позиций Risk Engine живая внутри сессии: экспозиция и
        # лимит числа позиций считаются по актуальному набору.
        # Meta (Этап 5) — для gross/net/групповых portfolio-лимитов.
        try:
            self.risk.add_position(
                pos.id,
                symbol=pos.symbol,
                side=pos.direction,
                notional=abs(pos.quantity * pos.entry_price),
            )
        except Exception as exc:
            logger.debug("risk.add_position: %s", exc)
        # Значимое событие (открыта позиция) — checkpoint (Этап 3).
        self._save_state_bundle()
        # Структурированная строка решения (TZ §32): режим, стратегия,
        # EV, confidence, риск-решение, размер.
        logger.info(
            "DECISION %s REGIME=%s STRATEGY=%s EV=%+.3fR CONF=%.2f RISK=APPROVED SIZE=%s",
            symbol,
            regime_info.get("regime", "?"),
            pos.strategy,
            float(cand_features.get("ev_r") or 0.0),
            float(cand_features.get("ev_confidence") or 0.0),
            pos.quantity,
        )
        # Уведомления по каждой сделке отключены: шлём только утренний
        # отчёт и отвечаем на команды из меню.
        logger.info("OPEN %s %s entry=%s", pos.direction, pos.symbol, pos.entry_price)
        return closed

    def _record_no_trade(self, symbol: str, decision: Decision, primary: list) -> None:
        """Записать NO_TRADE-наблюдение (TZ §12). Append-only, idempotent."""
        try:
            from ..ml.no_trade_observations import (
                NoTradeObservation,
                make_observation_id,
                quick_features,
            )

            bar = primary[-1]
            regime_info = decision.diagnostics.get("regime") or {}
            meta_evals = [
                e for e in (decision.diagnostics.get("meta") or [])
                if isinstance(e, dict)
            ]
            candidate = None
            if meta_evals:
                finite = [
                    e for e in meta_evals
                    if isinstance(e.get("ev_r"), (int, float))
                    and e["ev_r"] > float("-inf")
                ]
                if finite:
                    best = max(finite, key=lambda e: e.get("ev_r", 0.0))
                    candidate = {
                        "strategy": best.get("strategy"),
                        "direction": best.get("direction"),
                        "ev_r": best.get("ev_r"),
                        "confidence": best.get("confidence"),
                        "sample_size": best.get("sample_size"),
                        "score": best.get("total_score"),
                    }
            reason_code = decision.reason_code or "NO_VALID_SETUP"
            obs = NoTradeObservation(
                id=make_observation_id(
                    symbol,
                    int(bar.open_time),
                    reason_code,
                    candidate.get("strategy") if candidate else "",
                    candidate.get("direction") if candidate else "",
                ),
                symbol=symbol,
                bar_time=int(bar.open_time),
                timestamp=int(datetime.now(tz=UTC).timestamp() * 1000),
                market_regime=str(regime_info.get("regime", "UNKNOWN")),
                regime_confidence=float(regime_info.get("confidence", 0.0)),
                reason_code=reason_code,
                reasons=list(decision.reasons),
                candidate=candidate,
                features=quick_features(list(primary)),
            )
            if self.obs_log.add(obs):
                logger.info(
                    "NO_TRADE %s REGIME=%s BEST=%s REASON=%s",
                    symbol,
                    obs.market_regime,
                    candidate.get("strategy") if candidate else "-",
                    reason_code,
                )
        except Exception as exc:
            logger.debug("no_trade record: %s", exc)

    def _log_decision_line(self, symbol: str, decision: Decision) -> None:
        """Одна строка на решение (TZ §32) — для NO_TRADE и TRADE-потока."""
        if decision.action != "NO_TRADE":
            return  # TRADE-ветка логирует собственную строку после size
        meta_evals = decision.diagnostics.get("meta") or []
        best_ev, best_conf = None, None
        if meta_evals:
            finite = [
                e for e in meta_evals
                if isinstance(e, dict)
                and isinstance(e.get("ev_r"), (int, float))
                and e["ev_r"] > float("-inf")
            ]
            if finite:
                top = max(finite, key=lambda e: e.get("ev_r", 0.0))
                best_ev, best_conf = top.get("ev_r"), top.get("confidence")
                best = top.get("strategy")
            else:
                best = None
        else:
            best = None
        logger.info(
            "NO_TRADE %s REGIME=%s BEST_STRATEGY=%s EV=%s CONF=%s REASON=%s",
            symbol,
            (decision.diagnostics.get("regime") or {}).get("regime", "?"),
            best or "-",
            f"{best_ev:+.3f}R" if isinstance(best_ev, (int, float)) else "-",
            f"{best_conf:.2f}" if isinstance(best_conf, (int, float)) else "-",
            decision.reason_code or (decision.reasons[0] if decision.reasons else "?"),
        )

    def _save_state_bundle(self) -> None:
        """Checkpoint состояния (Этап 3): атомарно, не блокирует торговлю."""
        try:
            bundle = self.state_store.snapshot(broker=self.broker, risk=self.risk)
            self.state_store.save(bundle)
        except Exception as exc:
            logger.debug("state bundle save: %s", exc)

    def _aggregate_position_sample(self, pid: str, rows: list[dict]) -> dict:
        """Один обучающий sample на позицию (блок H).

        Все строки закрытий позиции (включая ранние частичные tp1 — они уже
        в trades_path, т.к. брокер пишет _log_trade в момент закрытия)
        сворачиваются: R — взвешенный по объёму, MFE — max, MAE — min,
        fees — сумма. Нет файла (тесты/моки) — агрегируем переданные строки.
        """
        file_rows: list[dict] = []
        try:
            tpath = Path(self.broker.trades_path)
            if tpath.exists():
                for line in tpath.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        t = json.loads(line)
                    except Exception:
                        continue
                    if str(t.get("id") or "") == pid:
                        file_rows.append(t)
        except Exception as exc:
            logger.debug("position rows read: %s", exc)
        use = file_rows or rows
        first = rows[0]
        tot_q = sum(float(r.get("quantity") or 0.0) for r in use)
        if tot_q > 0:
            r_avg = (
                sum(
                    float(r.get("r_multiple") or 0.0) * float(r.get("quantity") or 0.0)
                    for r in use
                )
                / tot_q
            )
        else:
            rs = [float(r.get("r_multiple") or 0.0) for r in use]
            r_avg = sum(rs) / len(rs) if rs else 0.0
        return {
            "strategy": first.get("strategy") or "",
            "regime": first.get("regime") or "UNKNOWN",
            "timeframe": first.get("timeframe") or "",
            "regime_axes": first.get("regime_axes") or "",
            "r_multiple": r_avg,
            "mfe_r": max([float(r.get("mfe_r") or 0.0) for r in use] or [0.0]),
            "mae_r": min([float(r.get("mae_r") or 0.0) for r in use] or [0.0]),
            "fees": sum(float(r.get("fees") or 0.0) for r in use),
        }

    def _record_closed(self, closed: list) -> None:
        """Сохранить закрытые сделки: уроки, Risk Engine, статистика режимов.

        Risk Engine получает результат каждой закрытой сделки: именно это
        подпитывает дневные/недельные лимиты, просадку и HALT-логика.
        StrategyStatsStore — R-метрики по (strategy, regime, timeframe):
        источник EV для Meta-Strategy (TZ §3.1/§5).
        """
        trades = []
        for t in closed:
            trades.append(
                {
                    "id": getattr(t, "id", ""),
                    "symbol": getattr(t, "symbol", ""),
                    "direction": getattr(t, "direction", ""),
                    "entry_price": getattr(t, "entry_price", 0.0),
                    "exit_price": getattr(t, "exit_price", 0.0),
                    "quantity": getattr(t, "quantity", 0.0),
                    "pnl": getattr(t, "pnl", 0.0),
                    "pnl_pct": getattr(t, "pnl_pct", 0.0),
                    "fees": getattr(t, "fees", 0.0),
                    "r_multiple": getattr(t, "r_multiple", 0.0),
                    "mfe_r": getattr(t, "mfe_r", 0.0),
                    "mae_r": getattr(t, "mae_r", 0.0),
                    "regime": getattr(t, "regime", ""),
                    "regime_axes": getattr(t, "regime_axes", ""),
                    "timeframe": getattr(t, "timeframe", ""),
                    "exit_reason": getattr(t, "exit_reason", ""),
                    "strategy": getattr(t, "strategy", ""),
                    "opened_at": getattr(t, "opened_at", 0),
                    "closed_at": getattr(t, "closed_at", 0),
                }
            )
        if not trades:
            return
        # Анти-дребезг: выход по стопу ставит ключ в реестр кулдаунов
        # (только стоп; VOL_EXPANSION и прочие forced-причины не трогаем —
        # решение владельца 12.09). Реестр персистится в state брокера.
        try:
            _cd_changed = False
            _now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
            for t in closed:
                if getattr(t, "exit_reason", "") == "stop_loss":
                    _cd_key = self._cooldown_key(
                        getattr(t, "strategy", "") or "",
                        getattr(t, "symbol", "") or "",
                        getattr(t, "direction", "") or "",
                    )
                    _cd_at = int(getattr(t, "closed_at", 0) or _now_ms)
                    self.broker.register_cooldown(
                        _cd_key, _cd_at + self._cooldown_ttl_ms()
                    )
                    _cd_changed = True
            if _cd_changed:
                self.broker.save()
        except Exception as exc:
            logger.debug("cooldown register skipped: %s", exc)
        # Block 2.1 & 7.1: Save to data/trades.db and data/state.json via StateManager
        try:
            from ..data.state_manager import get_state_manager
            sm = get_state_manager()
            sm.save_trades(trades)
            # Update state.json with latest balance and daily PnL
            try:
                state = sm.load_state()
                state["balance"] = float(self.broker.equity)
                state["realized_pnl"] = float(self.broker.realized_pnl)
                state["positions"] = [
                    {"symbol": p.symbol, "direction": p.direction, "entry_price": str(p.entry_price), "quantity": str(p.quantity)}
                    for p in self.broker.positions
                ]
                # Compute daily stats from recent trades
                # Итоговая дневная агрегация делается в morning_report.
                state["daily_pnl"] = float(sum(float(t.get("pnl",0) or 0) for t in trades))
                state["daily_trades"] = len(trades)
                state["daily_wins"] = sum(1 for t in trades if float(t.get("pnl",0) or 0) > 0)
                state["daily_losses"] = sum(1 for t in trades if float(t.get("pnl",0) or 0) < 0)
                sm.save_state(state)
            except Exception as e:
                logger.debug("state_manager save_state failed: %s", e)
        except Exception as exc:
            logger.debug("state_manager save_trades failed: %s", exc)

        try:
            append_lessons(trades)
        except Exception as exc:
            logger.warning("Не смог записать уроки: %s", exc)
        # Блок H: id позиций, оставшихся открытыми (частичные tp1): их
        # sample для статистики допишем при ПОЛНОМ закрытии, а из учёта
        # риска не снимаем (остаток всё ещё в рынке!).
        try:
            open_ids = {str(getattr(pos, "id", "")) for pos in self.broker.positions}
        except Exception:
            open_ids = set()
        for d in trades:
            try:
                self.risk.record_trade(
                    symbol=d["symbol"],
                    side=d["direction"],
                    entry_price=Decimal(str(d["entry_price"])),
                    quantity=Decimal(str(d["quantity"])),
                    pnl=Decimal(str(d["pnl"])),
                    won=float(d["pnl"]) > 0,
                )
                if str(d.get("id") or "") not in open_ids:
                    self.risk.remove_position(d["id"])
            except Exception as exc:
                logger.debug("risk.record_trade: %s", exc)
            # Метрика выходов (Этап 7): причина закрытия — кодированный
            # набор (STOP_LOSS/TAKE_PROFIT/TRAILING/BREAKEVEN/MAX_HOLD/
            # VOL_EXPANSION/BTC_PANIC/FLIP/CLOSE/STATIC_TP).
            try:
                EXITS_TOTAL.labels(reason=str(d.get("exit_reason") or "unknown")).inc()
            except Exception as exc:
                logger.debug("EXITS_TOTAL: %s", exc)
        # Блок H: один sample на ПОЛНОСТЬЮ закрытую позицию (взвешенный
        # R). Частичные tp1-добивки больше не раздувают n и винрейт.
        by_id: dict[str, list[dict]] = {}
        for d in trades:
            by_id.setdefault(str(d.get("id") or ""), []).append(d)
        for pid, rows in by_id.items():
            if not pid or pid in open_ids:
                continue
            sample = self._aggregate_position_sample(pid, rows)
            try:
                self.stats_store.record(
                    strategy=str(sample.get("strategy") or ""),
                    regime=str(sample.get("regime") or "UNKNOWN"),
                    timeframe=str(sample.get("timeframe") or ""),
                    r_multiple=float(sample.get("r_multiple") or 0.0),
                    mfe_r=float(sample.get("mfe_r") or 0.0),
                    mae_r=float(sample.get("mae_r") or 0.0),
                    fees=float(sample.get("fees") or 0.0),
                    # A2: двойная запись в бакет осей + legacy (миграция).
                    regime_axes=str(sample.get("regime_axes") or "") or None,
                )
                # Live-мониторинг гипотез (TZ §31): статистика ухудшилась
                # -> DEGRADE. Только по достижившейся live-выборке.
                self._check_hypothesis_degradation(sample)
            except Exception as exc:
                logger.debug("stats_store.record: %s", exc)
        # Значимое событие (закрыта сделка → лимиты/PnL изменились) —
        # checkpoint (Этап 3).
        self._save_state_bundle()

    def _check_hypothesis_degradation(self, trade: dict) -> None:
        """ACTIVE-гипотеза стратегии деградирует -> WEAKENING (TZ §31)."""
        strategy = str(trade.get("strategy") or "")
        if not strategy:
            return
        bucket = self.stats_store.get(
            strategy, str(trade.get("regime") or "UNKNOWN"),
            str(trade.get("timeframe") or ""),
        ) or self.stats_store.get_any(strategy, str(trade.get("timeframe") or ""))
        if bucket is None or bucket.sample_size < 20:
            return
        demoted = self.hypotheses.check_live_degradation(
            strategy_id=strategy,
            live_expectancy=bucket.expectancy_r,
            live_samples=bucket.sample_size,
        )
        for hid in demoted:
            logger.warning(
                "HYPOTHESIS %s DEGRADED: ACTIVE -> WEAKENING (live expectancy %.3fR)",
                hid, bucket.expectancy_r,
            )

    def _notify(self, text: str, severity: str = "info") -> None:
        if self._notifier is None:
            return
        try:
            res = self._notifier(text, severity)
            if asyncio.iscoroutine(res):
                # Отправка идёт fire-and-forget, чтобы не блокировать цикл.
                _spawn_background(res)
        except Exception as exc:
            logger.debug("notifier error: %s", exc)

    async def step(self) -> None:
        _step_started = time.monotonic()
        try:
            await self._step_impl()
        finally:
            # Латентность тика (Этап 7): деградация видна в дашборде.
            TICK_LATENCY.observe(time.monotonic() - _step_started)

    async def _step_impl(self) -> None:
        # Раз при первом шаге подтягиваем реальный капитал спот-счёта BingX.
        if not self._capital_synced:
            await self.sync_capital()

        # Учёт минуты бюджета торговых часов (раз в минуту цикл может
        # вызываться чаще, но тарифицируем только целые минуты).
        from datetime import datetime
        bucket = int(datetime.now(UTC).timestamp() // 60)
        if self._minute_bucket != bucket:
            self._minute_bucket = bucket
            trading_schedule.tick()

        # BTC PANIC (Этап 4): глобальный safety-флаг — один запрос 4h
        # за step; процессинг символов флаттит позиции при обвале.
        await self.exit_manager.refresh_btc_panic()

        # Research (Этап 6): auto-retirement устаревших гипотез —
        # «проверили, edge не подтверждён» = negative knowledge (TZ §10).
        try:
            self.hypotheses.auto_retire_stale()
        except Exception as exc:
            logger.debug("auto_retire_stale: %s", exc)

        for symbol in self.config.symbols:
            try:
                await self.process_symbol(symbol)
            except Exception as exc:
                logger.exception("Ошибка обработки %s: %s", symbol, exc)

    async def run_forever(self) -> None:
        self._running = True
        status = trading_schedule.get_status()
        logger.info(
            "Trading engine запущен: %s, интервал %ss; бюджет %.0f ч/мес, "
            "активные часы %s МСК, сейчас торговля %s",
            self.config.symbols,
            self.config.poll_interval_seconds,
            status["budget_hours"],
            status["active_hours_msk"],
            "разрешена" if status["can_trade_now"] else "на паузе",
        )
        while self._running:
            await self.step()
            await asyncio.sleep(self.config.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False
