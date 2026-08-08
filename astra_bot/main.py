#!/usr/bin/env python3
"""
ASTRA BOT — Main Entry Point
Основной модуль системы
"""

import asyncio
import logging
import sys
import os
from pathlib import Path
from datetime import datetime
from decimal import Decimal

# Добавляем проект в путь
project_root = Path(__file__).parent.parent if '__file__' in dir() else Path.cwd()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from astra_bot.core.config import load_settings, get_settings
from astra_bot.core.logger import setup_logging, get_component_logger
from astra_bot.core.events import get_event_bus
from astra_bot.data.database import init_database, close_database, get_db
from astra_bot.adapters.okx import OKXClient, OKXWebSocket, OKXOrderManager
from astra_bot.engines.regime_detector import MarketRegimeDetector, get_regime_detector
from astra_bot.engines.risk_engine import RiskEngine, RiskConfig, get_risk_engine
from astra_bot.engines.execution_engine import ExecutionEngine, ExecutionConfig, get_execution_engine
from astra_bot.strategies import MomentumStrategy, MeanReversionStrategy, AdaptiveGridStrategy
from astra_bot.paperengine.paper_engine import PaperTradingEngine, get_paper_engine

# Настройка логирования
log_dir = os.environ.get("LOG_DIR", "/app/logs")
Path(log_dir).mkdir(parents=True, exist_ok=True)

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
        
        self._strategies = {}
        self._running = False
        self._shutdown_event = asyncio.Event()
    
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
    
    async def start(self):
        """Запуск системы"""
        if not self._exchange_client:
            raise RuntimeError("System not initialized")
        
        self._running = True
        logger.info("ASTRA BOT Starting...")
        
        # Запуск WebSocket
        if self._exchange_websocket and get_settings().market_data.get("websocket_reconnect_delay", 5) > 0:
            asyncio.create_task(self._exchange_websocket.start())
        
        # Запуск paper engine если включен
        if get_settings().paper_trading:
            asyncio.create_task(self._paper_engine.start())
        
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
        """Один тик системы"""
        # TODO: Реализовать основной цикл
        # 1. Получить рыночные данные
        # 2. Обновить regime detector
        # 3. Оценить стратегии
        # 4. Проверить риск
        # 5. Исполнить сделки
        pass
    
    async def stop(self):
        """Остановка системы"""
        logger.info("ASTRA BOT Stopping...")
        self._running = False
        
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
