#!/usr/bin/env python3
"""
ASTRA BOT — Main Entry Point
Основной модуль системы
"""

import asyncio
import os
import signal
import sys
import time
from decimal import Decimal
from pathlib import Path

# Добавляем проект в путь
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from astra_bot.adapters.okx import OKXClient, OKXOrderManager, OKXWebSocket
from astra_bot.core import readiness
from astra_bot.core.config import get_settings, load_settings
from astra_bot.core.instruments import to_okx
from astra_bot.core.logger import get_component_logger, setup_logging
from astra_bot.data.database import close_database, init_database
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.engines.execution_engine import get_execution_engine
from astra_bot.engines.regime_detector import get_regime_detector
from astra_bot.engines.risk_engine import RiskConfig, get_risk_engine
from astra_bot.paperengine.paper_engine import PaperTradingEngine
from astra_bot.strategies import (
    AdaptiveGridStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    TimeSeriesMomentumConfig,
    TimeSeriesMomentumStrategy,
)

# Настройка логирования. По умолчанию пишем в ./logs (репозиторий), а не в
# ``/app/logs`` — последний путь существует только внутри Docker-контейнера и
# приводит к PermissionError при запуске локально.
_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
log_dir = os.environ.get("LOG_DIR", str(_DEFAULT_LOG_DIR))
try:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
except OSError:
    # Если каталог недоступен для записи, остаёмся на stdout-логировании.
    log_dir = None

setup_logging(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    log_dir=log_dir,
)

logger = get_component_logger("main")


class AstraBot:
    """
    Основной класс ASTRA BOT.

    Управляет всеми компонентами системы.
    """

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.environ.get("ASTRA_CONFIG", "config/settings.yaml")
        self.config = None

        # Компоненты
        self._exchange_client = None
        self._exchange_websocket = None
        self._order_manager = None
        self._regime_detector = None
        self._risk_engine = None
        self._execution_engine = None
        self._paper_engine = None
        # Современный paper-путь (DecisionPipeline + RiskEngine + PaperBroker)
        # — единственный исполнитель решений в _tick.
        self._trading_engine = None
        self._last_tick_at = 0.0

        self._strategies = {}
        self._running = False
        self._shutdown_event = asyncio.Event()
        # Фоновые задачи (WebSocket, paper engine) — удерживаем сильные ссылки.
        self._background_tasks: set[asyncio.Task] = set()

    async def initialize(self):
        """Инициализация системы"""
        logger.info("=" * 60)
        logger.info("ASTRA BOT Initializing")
        logger.info(f"Config: {self.config_path}")
        logger.info("=" * 60)

        # 1. Загрузка конфигурации
        await self._load_config()

        # 2. Инициализация базы данных
        await self._init_database()

        # 3. Инициализация exchange
        await self._init_exchange()

        # 4. Инициализация стратегий
        self._init_strategies()

        # 5. Инициализация движков
        self._init_engines()

        # 6. Инициализация paper engine
        self._init_paper_engine()

        # 7. Современный paper-путь (исполнитель _tick)
        self._init_trading_engine()

        logger.info("ASTRA BOT Initialized Successfully")
        logger.info("=" * 60)

    async def _load_config(self):
        """Загрузить конфигурацию"""
        self.config = load_settings(self.config_path)
        settings = get_settings()

        logger.info(f"Environment: {settings.environment}")
        logger.info(f"Paper trading: {settings.paper_trading}")
        logger.info(f"Trading enabled: {settings.trading_enabled}")
        logger.info(f"Instruments: {settings.instruments}")

    async def _init_database(self):
        """Инициализация базы данных"""
        settings = get_settings()
        if settings.database:
            db_config = {
                "host": settings.database.host,
                "port": settings.database.port,
                "name": settings.database.name,
                "user": settings.database.user,
                "password": settings.database.password,
                "pool_size": settings.database.pool_size,
            }
            await init_database(db_config)
            logger.info("Database connected")
        else:
            logger.warning("Database not configured")

    async def _init_exchange(self):
        """Инициализация exchange"""
        settings = get_settings()

        if "okx" in settings.exchanges:
            okx_config = settings.exchanges["okx"]
            config_dict = {
                "api_key": okx_config.api_key,
                "api_secret": okx_config.api_secret,
                "passphrase": okx_config.passphrase,
                "sandbox": okx_config.sandbox,
                "base_url": okx_config.base_url,
                "enabled": okx_config.enabled,
                "contract_type": okx_config.contract_type,
            }

            # REST клиент
            self._exchange_client = OKXClient(config_dict)
            await self._exchange_client.initialize()

            # WebSocket
            self._exchange_websocket = OKXWebSocket(config_dict)

            # Order manager
            self._order_manager = OKXOrderManager(self._exchange_client)

            # Проверка соединения
            if await self._exchange_client.test_connection():
                logger.info("OKX connection established")
            else:
                logger.warning("OKX connection test failed")

    def _init_strategies(self):
        """Инициализация стратегий"""
        settings = get_settings()

        # Momentum
        if settings.strategies.get("momentum", {}).get("enabled", True):
            self._strategies["momentum"] = MomentumStrategy()
            logger.info("Momentum strategy initialized")

        # Mean Reversion
        if settings.strategies.get("mean_reversion", {}).get("enabled", True):
            self._strategies["mean_reversion"] = MeanReversionStrategy()
            logger.info("Mean Reversion strategy initialized")

        # Adaptive Grid
        if settings.strategies.get("adaptive_grid", {}).get("enabled", False):
            self._strategies["adaptive_grid"] = AdaptiveGridStrategy()
            logger.info("Adaptive Grid strategy initialized")

        # Time Series Momentum
        if settings.strategies.get("ts_momentum", {}).get("enabled", False):
            tsm_cfg = TimeSeriesMomentumConfig(name="ts_momentum")
            self._strategies["ts_momentum"] = TimeSeriesMomentumStrategy(tsm_cfg)
            logger.info("Time Series Momentum strategy initialized")

        # Time Series Momentum + ADX
        if settings.strategies.get("ts_momentum_adx", {}).get("enabled", False):
            tsm_adx_cfg = TimeSeriesMomentumConfig(name="ts_momentum_adx", adx_min=20.0)
            self._strategies["ts_momentum_adx"] = TimeSeriesMomentumStrategy(tsm_adx_cfg)
            logger.info("Time Series Momentum ADX strategy initialized")

    def _init_engines(self):
        """Инициализация движков"""
        # Regime Detector
        self._regime_detector = get_regime_detector()

        # Risk Engine
        config = get_settings().risk
        risk_config = RiskConfig(
            risk_per_trade=config.risk_per_trade,
            daily_loss_limit=config.daily_loss_limit,
            weekly_loss_limit=config.weekly_loss_limit,
            soft_drawdown=config.soft_drawdown,
            hard_drawdown=config.hard_drawdown,
            emergency_drawdown=config.emergency_drawdown,
            max_exposure_pct=config.max_exposure_pct,
            max_open_positions=config.max_open_positions,
        )
        self._risk_engine = get_risk_engine()
        self._risk_engine.config = risk_config

        # Execution Engine
        self._execution_engine = get_execution_engine()

        # Set exchange for execution engine
        if self._exchange_client:
            self._execution_engine.set_exchange(self._exchange_client)

        logger.info("Engines initialized")

    def _init_paper_engine(self):
        """Инициализация paper engine"""
        settings = get_settings()

        initial_capital = Decimal(settings.risk.max_exposure_pct * 1000) if settings.risk.max_exposure_pct else Decimal("1000")

        self._paper_engine = PaperTradingEngine(
            initial_capital=initial_capital,
        )

        # Добавляем стратегии
        for name, strategy in self._strategies.items():
            self._paper_engine.add_strategy(name, strategy)

        logger.info("Paper engine initialized")

    def _init_trading_engine(self):
        """Современный paper-путь: DecisionPipeline → RiskEngine → PaperBroker.

        Единственный исполнитель решений в ``_tick``: по каждому символу
        сам тянет рыночные данные, собирает MarketContext, обновляет
        regime, решает, проверяет риск и исполняет в paper-брокере.
        Без exchange-клиента движок НЕ создаётся (fail-closed).
        """
        if self._exchange_client is None:
            logger.warning(
                "TradingEngine не создан: нет exchange-клиента (fail-closed)"
            )
            return
        settings = get_settings()
        symbols = tuple(to_okx(s) for s in settings.instruments)
        # Каталог state: по умолчанию models/ (общий с CI-сессиями);
        # ASTRA_STATE_DIR позволяет изолировать локальный run.
        state_dir = os.environ.get("ASTRA_STATE_DIR", "models")
        self._trading_engine = TradingEngine(
            okx=self._exchange_client,
            config=TradingEngineConfig(
                symbols=symbols,
                state_path=f"{state_dir}/paper_positions.json",
                trades_path=f"{state_dir}/paper_trades.jsonl",
                stats_path=f"{state_dir}/strategy_stats.json",
                no_trade_observations_path=f"{state_dir}/no_trade_observations.jsonl",
                no_trade_outcomes_path=f"{state_dir}/no_trade_outcomes.json",
                hypotheses_path=f"{state_dir}/research/hypotheses.json",
            ),
        )
        logger.info("TradingEngine (modern paper path) initialized: %s", symbols)

    def _spawn_background(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def start(self):
        """Запуск системы"""
        if not self._exchange_client:
            raise RuntimeError("System not initialized")

        self._running = True
        logger.info("ASTRA BOT Starting...")

        # Запуск WebSocket
        if self._exchange_websocket and (
            get_settings().market_data.websocket_reconnect_delay > 0
        ):
            self._spawn_background(self._exchange_websocket.start())

        # Legacy paper engine: свой цикл запускаем ТОЛЬКО если современный
        # путь (TradingEngine) недоступен — иначе двойная торговля.
        if get_settings().paper_trading and self._paper_engine:
            if self._trading_engine is not None:
                logger.info(
                    "Legacy PaperTradingEngine loop disabled: "
                    "используется TradingEngine (modern path)"
                )
            else:
                self._spawn_background(self._paper_engine.start())

        # Запуск основного цикла
        await self._run()

    async def _run(self):
        """Основной цикл"""
        while self._running:
            try:
                # Получение рыночных данных
                await self._tick()

                # Ожидание до следующего тика
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(5)

        await self.stop()

    async def _tick(self):
        """Один тик системы.

        Поток тика (реализован в TradingEngine, логика сюда не дублируется):
        1) рыночные данные по universe (REST, кэш в engine);
        2) MarketContext по символам;
        3) regime detector (внутри pipeline.decide);
        4) DecisionPipeline.decide() — решения + структурированные
           NO_TRADE reasons (логи engine: NO_TRADE ... REASON=...);
        5) RiskEngine.check_trade — независимый слой, обхода нет;
        6) исполнение только через PaperBroker (paper-контур);
        7) обновление state/metrics/lessons (engine: stats, lessons,
           NO_TRADE-наблюдения, risk-state).

        Один упавший символ не останавливает остальные: per-symbol
        изоляция внутри TradingEngine.step (try/except на символ).
        Ошибка всего тика пробрасывается наверх — цикл _run её логирует
        и продолжает (не роняя бота).
        """
        # Fail-closed: без исполнителя решений тик ничего не делает.
        if self._trading_engine is None:
            return

        # Троттлинг: тик не чаще, чем раз в tick_interval_seconds.
        interval = get_settings().market_data.tick_interval_seconds
        now = time.monotonic()
        if now - self._last_tick_at < interval:
            return
        self._last_tick_at = now

        # Readiness gate: paper-торговля идёт (накапливает данные),
        # score уходит в лог/метрики; LIVE-переход разрешён только после
        # readiness (двойной safety-gate, см. live_orders_allowed).
        try:
            readiness_info = readiness.evaluate()
            logger.info(
                "Tick start: readiness score=%s/%s ready=%s",
                readiness_info["score"],
                readiness_info["threshold"],
                readiness_info["ready"],
            )
        except Exception as exc:
            logger.debug("Readiness check error: %s", exc)

        started = time.monotonic()
        await self._trading_engine.step()
        # Периодический checkpoint (Этап 3): атомарный state-бандл.
        self._save_state()
        logger.debug("Tick done in %.1fs", time.monotonic() - started)

    def _save_state(self) -> None:
        """Собрать и атомарно сохранить state-бандл (Этап 3)."""
        if self._trading_engine is None:
            return
        try:
            readiness_info = None
            try:
                readiness_info = readiness.evaluate()
            except Exception as exc:
                logger.debug("Readiness для бандла: %s", exc)
            registry = None
            if Path("models/registry/registry.json").exists():
                from astra_bot.ml.model_registry import get_registry

                registry = get_registry()
            bundle = self._trading_engine.state_store.snapshot(
                broker=self._trading_engine.broker,
                risk=self._trading_engine.risk,
                readiness_info=readiness_info,
                registry=registry,
                hypotheses=self._trading_engine.hypotheses,
            )
            self._trading_engine.state_store.save(bundle)
        except Exception as exc:
            logger.debug("State bundle save: %s", exc)

    async def stop(self):
        """Остановка системы"""
        logger.info("ASTRA BOT Stopping...")
        self._running = False

        # Финальный checkpoint перед остановкой (Этап 3).
        self._save_state()

        # Отменяем все фоновые задачи (WebSocket, paper engine и т.п.).
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        if self._paper_engine:
            await self._paper_engine.stop()

        if self._exchange_websocket:
            await self._exchange_websocket.disconnect()

        if self._exchange_client:
            await self._exchange_client.close()

        await close_database()

        logger.info("ASTRA BOT Stopped")

    async def get_status(self) -> dict:
        """Получить текущий статус"""
        return {
            "running": self._running,
            "environment": get_settings().environment if self.config else None,
            "strategies": {
                name: strategy.to_dict()
                for name, strategy in self._strategies.items()
            },
            "risk_state": self._risk_engine.risk_state.value if self._risk_engine else None,
            "exchange": "okx" if self._exchange_client else None,
        }


async def main():
    """Главная функция"""
    import argparse

    parser = argparse.ArgumentParser(description="ASTRA BOT — Autonomous Crypto Trading Platform")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("ASTRA_CONFIG", "config/settings.yaml"),
        help="Path to config file"
    )
    parser.add_argument(
        "--action",
        type=str,
        choices=["start", "status", "test", "report", "preflight"],
        default="start",
        help="Action to perform"
    )
    parser.add_argument(
        "--env",
        type=str,
        choices=["development", "paper", "production"],
        default=os.environ.get("ENVIRONMENT", "development"),
        help="Environment"
    )

    args = parser.parse_args()

    # Установка переменной окружения
    os.environ["ENVIRONMENT"] = args.env

    bot = AstraBot(config_path=args.config)

    try:
        await bot.initialize()

        if args.action == "status":
            status = await bot.get_status()
            print(status)
        elif args.action == "test":
            logger.info("Running tests...")
            # TODO: Запустить тесты
        elif args.action == "report":
            # Генерация отчёта
            sys.path.insert(0, str(project_root / "scripts"))
            from daily_report import generate_daily_report
            report = generate_daily_report()
            print(report)
        elif args.action == "preflight":
            # Проверка перед запуском
            sys.path.insert(0, str(project_root / "scripts"))
            from preflight import main as preflight_main
            exit_code = await preflight_main()
            sys.exit(exit_code)
        else:
            # Graceful shutdown: SIGINT/SIGTERM → плавная остановка
            # (отмена фоновых задач, закрытие клиента и БД в bot.stop()).
            loop = asyncio.get_running_loop()

            def _request_stop():
                logger.info("Сигнал остановки — graceful shutdown")
                bot._running = False

            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _request_stop)
                except NotImplementedError:
                    pass
            await bot.start()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
