"""
ASTRA BOT — Trading engine.

Связывает DecisionPipeline, OKX market data и PaperBroker:

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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..adapters.okx import OKXClient
from ..core import models, trading_schedule
from ..core.market_safety import MarketSafety
from ..engines.risk_engine import RiskConfig, RiskEngine
from ..ml.live_lessons import append_lessons
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


@dataclass
class TradingEngineConfig:
    symbols: tuple[str, ...] = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    # 5m — основной для частых входов; 15m для подтверждения тренда;
    # 4h — для долгосрочного фильтра ts_momentum (флипы по 45-дневному
    # импульсу). Держим 3 таймфрейма, чтобы шаг по 10 монетам был быстрым.
    timeframes: tuple[str, ...] = ("5m", "15m", "4h")
    # Сколько баров тянуть для каждого таймфрейма.
    bars_per_tf: dict[str, int] = field(
        default_factory=lambda: {"5m": 250, "15m": 200, "4h": 320}
    )
    # 0.5% риска на сделку. Капитал в управлении — половина демо-портфеля
    # (~40 000 USDT), поэтому 0.5% = ~200 USDT риска, позиция крупная, но
    # контролируемая. Это даёт много мелких по риску сделок для обучения.
    risk_per_trade_pct: Decimal = Decimal("0.005")
    # Максимум 15% капитала на одну позицию (notional) — защита от раздутия.
    max_notional_pct: Decimal = Decimal("0.15")
    # Сколько позиций держим одновременно. На 28 монетах 8 — достаточно для
    # диверсификации и при этом не перегружает депозит коррелированными
    # входами в один момент.
    max_open_positions: int = 8
    # Лимит однонаправленных позиций (long или short) — чтобы не открыть
    # сразу 6 лонгов, которые все стопнутся вместе.
    max_same_direction: int = 4
    # Максимальная суммарная экспозиция (notional) в % капитала.
    max_total_exposure_pct: Decimal = Decimal("0.70")
    poll_interval_seconds: int = 60 * 5
    state_path: str = "models/paper_positions.json"
    trades_path: str = "models/paper_trades.jsonl"
    # Реальные издержки paper-счёта (тейкер-комиссия / slippage на сторону).
    # База 0.1%/0.1% совпадает с baseline в run_full_research_audit.py.
    fee_pct: Decimal = Decimal("0.001")
    slippage_pct: Decimal = Decimal("0.001")
    # Research memory: статистика стратегий по режимам + NO_TRADE-наблюдения.
    stats_path: str = "models/strategy_stats.json"
    no_trade_observations_path: str = "models/no_trade_observations.jsonl"
    no_trade_outcomes_path: str = "models/no_trade_outcomes.json"
    hypotheses_path: str = "models/research/hypotheses.json"


class TradingEngine:
    def __init__(
        self,
        okx: OKXClient,
        pipeline: DecisionPipeline | None = None,
        config: TradingEngineConfig | None = None,
        broker: PaperBroker | None = None,
        notifier: Any | None = None,
        risk_engine: RiskEngine | None = None,
    ):
        # Колбэк для уведомлений в Telegram: notifier(text, severity).
        self._notifier = notifier
        self.okx = okx
        self.config = config or TradingEngineConfig()
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
            cfg.slippage_buffer_pct = 0.02
            cfg.min_book_depth = 1_000.0
            # Meta-Strategy: cold start — EV-гейт по prior (0.0), как и
            # прежний edge-гейт; по мере накопления статистики по режимам
            # оценка становится эмпирической автоматически (shrinkage).
            cfg.min_ev_r = 0.0
            stats_store = StrategyStatsStore(
                Path(self.config.stats_path),
                shrinkage_k=cfg.ev_shrinkage_k,
                min_samples=cfg.min_ev_samples,
            )
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
                ],
            )
        self.pipeline = pipeline
        self.broker = broker or self._make_broker()
        # Risk Engine — независимый слой защиты (master prompt §11):
        # дневные/недельные лимиты потерь, просадка, exposure, TRADING HALT.
        # Стратегии и ML не имеют права его обойти. Лимиты согласованы
        # с торговым конфигом, чтобы sizing не конфликтовал с чекером.
        self.risk = risk_engine or RiskEngine(
            RiskConfig(
                risk_per_trade=Decimal(self.config.risk_per_trade_pct),
                max_open_positions=self.config.max_open_positions,
                max_exposure_pct=Decimal(self.config.max_total_exposure_pct),
            )
        )
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

        self.exit_controller = ExitController(self.hypotheses)
        # Model Registry (TZ §18): живому пайплайну отдаём только
        # ACTIVE (production) модель; без неё пайплайн работает как
        # раньше (ml_probability = None). Сбой загрузки не роняет бота.
        if getattr(pipeline, "model", None) is None:
            try:
                from ..ml.model_registry import get_registry
                from ..ml.model_trainer import MLModel

                prod = get_registry().get_production_model()
                if prod is not None and prod.model_path and Path(prod.model_path).exists():
                    pipeline.model = MLModel.load(prod.model_path)
                    logger.info(
                        "ML model из registry (production): %s", prod.version
                    )
            except Exception as exc:
                logger.debug("registry model load: %s", exc)
        self._last_bar_ts: dict[str, int] = {}
        self._running = False
        self._capital_synced = False
        self._risk_synced = False
        self._minute_bucket: int | None = None

    def _make_broker(self, initial_capital: Decimal | None = None) -> PaperBroker:
        """Брокер с реальными издержками (fee/slippage) по торговому конфигу."""
        kwargs: dict[str, Any] = dict(
            state_path=Path(self.config.state_path),
            trades_path=Path(self.config.trades_path),
            fee_pct=self.config.fee_pct,
            slippage_pct=self.config.slippage_pct,
        )
        if initial_capital is not None:
            kwargs["initial_capital"] = initial_capital
        return PaperBroker(**kwargs)

    async def sync_capital(self) -> Decimal:
        """Синхронизировать торговый капитал с демо-портфелем OKX.

        В управлении бота — ПОЛОВИНА оценки всего демо-портфеля в USDT
        (BTC+ETH+OKB+USDT по текущим ценам), как просил владелец. Это
        масштаб «сколько реально есть», а не зашитые 2000.
        """
        if self._capital_synced:
            return self.broker.initial_capital
        try:
            bals = await self.okx.get_account_balance()
            # Оцениваем портфель в USDT по текущим ценам.
            total_usdt = Decimal("0")
            for asset, b in bals.items():
                if asset == "USDT":
                    total_usdt += b.total
                else:
                    try:
                        t = await self.okx.get_ticker(f"{asset}-USDT")
                        if t and t.get("last"):
                            total_usdt += b.total * Decimal(str(t["last"]))
                    except Exception:
                        # Нет пары к USDT — учитываем как есть, не валимся.
                        continue
            # Половина бюджета в управлении.
            cap = (total_usdt / Decimal("2")).quantize(Decimal("0.01"))
            if self.broker.positions:
                logger.info(
                    "Портфель демо=%.2f USDT, половина=%.2f, но есть позиции — "
                    "продолжаю с %s", total_usdt, cap, self.broker.initial_capital,
                )
            else:
                logger.info(
                    "Портфель демо OKX=%.2f USDT; в управлении половина=%.2f USDT",
                    total_usdt, cap,
                )
                self.broker = self._make_broker(cap)
            self._capital_synced = True
            return cap
        except Exception as exc:
            logger.debug("Не смог синхронизировать капитал: %s", exc)
        self._capital_synced = True
        return self.broker.initial_capital

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
        # Фактическая оценка брокера (initial + realized PnL) приоритетнее
        # кривой из файла, если состояние было правлено вручную.
        self.risk.set_capital(self.broker.equity, self.broker.initial_capital)
        for pos in self.broker.positions:
            self.risk.add_position(pos.id)
        self._risk_synced = True
        logger.info(
            "Risk state восстановлен: equity=%s, daily_pnl=%s, weekly_pnl=%s, "
            "state=%s, trading_enabled=%s, open_positions=%d",
            self.broker.equity, self.risk.daily_pnl, self.risk.weekly_pnl,
            self.risk.risk_state.value, self.risk.trading_enabled,
            len(self.broker.positions),
        )

    # ----------------------------------------------------------- market data
    async def fetch_context(self, symbol: str) -> MarketContext:
        candles: dict[str, list[models.Candle]] = {}
        for tf in self.config.timeframes:
            candles[tf] = await self.okx.get_candles(
                symbol,
                timeframe=tf,
                limit=self.config.bars_per_tf.get(tf, 300),
            )
        primary = candles.get("5m") or candles.get("15m") or candles.get("1h") or []
        if not primary:
            raise RuntimeError(f"Нет данных по {symbol}")
        price = Decimal(str(primary[-1].close))
        try:
            orderbook = await self.okx.get_orderbook(symbol, depth=20)
        except Exception as exc:
            logger.warning("Стакан %s недоступен: %s", symbol, exc)
            orderbook = None
        return MarketContext(
            symbol=symbol,
            current_price=price,
            candles=candles,
            orderbook=orderbook,
        )

    # ----------------------------------------------------------- sizing
    def _position_size(
        self,
        equity: Decimal,
        entry: Decimal,
        stop: Decimal,
    ) -> Decimal:
        risk_amount = equity * self.config.risk_per_trade_pct
        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            return Decimal("0")
        qty = risk_amount / stop_distance
        # Не допускаем размер больше 30% капитала (notional).
        max_notional = equity * self.config.max_notional_pct
        if qty * entry > max_notional:
            qty = max_notional / entry
        return qty.quantize(Decimal("0.000001"))

    def _risk_check_and_adjust(
        self,
        symbol: str,
        side: str,
        cand: Any,
        size: Decimal,
    ) -> Decimal | None:
        """Прогнать сделку через Risk Engine; вернуть допустимый размер.

        Возвращает ``None``, если вход запрещён (TRADING HALT, дневной/
        недельный лимит потерь, лимиты, которые нельзя закрыть уменьшением
        размера). Иначе — исходный или уменьшенный до лимита размер.
        """
        for _ in range(2):
            verdict = self.risk.check_trade(
                symbol=symbol,
                side=side,
                entry_price=cand.entry_price,
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

    # ----------------------------------------------------------- main loop
    async def process_symbol(self, symbol: str) -> list[Any]:
        # Риск-состояние (лимиты, HALT) живое между CI-сессиями:
        # восстанавливаем из персиста перед любым решением о входе.
        self._sync_risk_state()
        ctx = await self.fetch_context(symbol)
        primary = ctx.candles.get("5m") or ctx.candles.get("1h") or []
        if not primary:
            return []

        # Обновляем уже открытые позиции по последнему бару.
        # Экстремумы обновляем до решения: Exit Controller скорректирует
        # стопы ДО проверки срабатывания на этом же баре (TZ §16).
        last_bar = primary[-1]
        self.broker.update_extremes(last_bar)

        # Решение по стратегиям — вычисляем до обработки выходов и
        # проверки «есть ли позиция», чтобы флип-стратегии (ts_momentum)
        # могли перевернуть/закрыть её.
        decision = self.pipeline.decide(ctx)
        regime_name = str(
            (decision.diagnostics.get("regime") or {}).get("regime", "")
        )

        # Exit Research (TZ §16/§17): активная гипотеза выхода -> план;
        # иначе STATIC_TP — live-поведение не меняется.
        forced: list = []
        try:
            forced = self.exit_controller.apply(
                self.broker, symbol, last_bar, list(primary), regime_name
            )
        except Exception as exc:
            logger.debug("exit_controller: %s", exc)

        closed = forced + self.broker.check_exits(last_bar)

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

        # Дисциплина капитала: лимит числа позиций, однонаправленных
        # входов и суммарной экспозиции. Защищает от пачки
        # коррелированных сделок, которые стопнутся одновременно.
        open_positions = list(self.broker.positions)
        if len(open_positions) >= self.config.max_open_positions:
            return closed
        total_notional = sum(
            float(p.entry_price) * float(p.quantity) for p in open_positions
        )
        equity = float(self.broker.equity)
        if equity > 0 and total_notional / equity >= float(
            self.config.max_total_exposure_pct
        ):
            return closed

        # Рыночная «безопасность»: расписание/бюджет часов, новости,
        # волатильность, спред, дисбаланс стакана. Если не прошли —
        # пропускаем вход (это главный щит от слива депозита).
        try:
            ticker = await self.okx.get_ticker(symbol)
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
        ticker_map = None
        if ticker:
            # get_ticker возвращает high_24h/low_24h без open24h; для
            # проверки резкого движения считаем open из last и диапазона.
            last_f = float(ticker.get("last") or 0)
            hi = float(ticker.get("high_24h") or 0)
            lo = float(ticker.get("low_24h") or 0)
            # Грубая оценка open24h как середины диапазона (нам важен лишь
            # факт резкого движения > 8%, точность до долей процента не нужна).
            open24 = (hi + lo) / 2 if hi and lo else 0.0
            ticker_map = {"last": last_f, "open24h": open24}
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

        size = self._position_size(
            self.broker.equity,
            cand.entry_price,
            cand.stop_loss,
        )
        if size <= 0:
            logger.info("%s: size=0, пропускаю", symbol)
            return closed

        # ---- Risk Engine: независимый слой защиты (master prompt §11).
        # Дневные/недельные лимиты потерь, просадка, exposure, HALT.
        # Если торговля остановлена — вход запрещён независимо от силы
        # сигнала. Если размер можно уменьшить — уменьшаем и проверяем.
        size = self._risk_check_and_adjust(symbol, wanted_dir, cand, size)
        if size is None or size <= 0:
            return closed

        cand_features = cand.features or {}
        regime_info = decision.diagnostics.get("regime") or {}
        pos = self.broker.open_position(
            symbol=symbol,
            direction="long" if cand.direction == "long" else "short",
            entry_price=cand.entry_price,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            quantity=size,
            strategy=cand.strategy,
            no_take_profit=bool(cand_features.get("no_take_profit")),
            regime=str(regime_info.get("regime", "")),
            timeframe=cand.timeframe,
            notes={
                "score": cand.total_score,
                "ml_probability": cand.ml_probability,
                "edge_pct": cand.expected_edge_pct,
                "ev_r": cand_features.get("ev_r"),
                "ev_confidence": cand_features.get("ev_confidence"),
                "rr": cand.risk_reward,
            },
        )
        # Книга позиций Risk Engine живая внутри сессии: экспозиция и
        # лимит числа позиций считаются по актуальному набору.
        try:
            self.risk.add_position(pos.id)
        except Exception as exc:
            logger.debug("risk.add_position: %s", exc)
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
                    "timeframe": getattr(t, "timeframe", ""),
                    "exit_reason": getattr(t, "exit_reason", ""),
                    "strategy": getattr(t, "strategy", ""),
                    "opened_at": getattr(t, "opened_at", 0),
                    "closed_at": getattr(t, "closed_at", 0),
                }
            )
        if not trades:
            return
        try:
            append_lessons(trades)
        except Exception as exc:
            logger.warning("Не смог записать уроки: %s", exc)
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
                self.risk.remove_position(d["id"])
            except Exception as exc:
                logger.debug("risk.record_trade: %s", exc)
            try:
                if d.get("r_multiple") or d.get("regime"):
                    self.stats_store.record(
                        strategy=str(d.get("strategy") or ""),
                        regime=str(d.get("regime") or "UNKNOWN"),
                        timeframe=str(d.get("timeframe") or ""),
                        r_multiple=float(d.get("r_multiple") or 0.0),
                        mfe_r=float(d.get("mfe_r") or 0.0),
                        mae_r=float(d.get("mae_r") or 0.0),
                        fees=float(d.get("fees") or 0.0),
                    )
                    # Live-мониторинг гипотез (TZ §31): статистика ухудшилась
                    # -> DEGRADE. Только по достижившейся live-выборке.
                    self._check_hypothesis_degradation(d)
            except Exception as exc:
                logger.debug("stats_store.record: %s", exc)

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
        # Раз при первом шаге подтягиваем реальный капитал демо OKX.
        if not self._capital_synced:
            await self.sync_capital()

        # Учёт минуты бюджета торговых часов (раз в минуту цикл может
        # вызываться чаще, но тарифицируем только целые минуты).
        from datetime import datetime
        bucket = int(datetime.now(UTC).timestamp() // 60)
        if self._minute_bucket != bucket:
            self._minute_bucket = bucket
            trading_schedule.tick()

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
