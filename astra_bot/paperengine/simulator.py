"""
ASTRA BOT — Paper Trading Simulator
Симулятор рынка для тестирования стратегий
"""

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np

from .paper_engine import PaperTrade, PaperTradingEngine

logger = logging.getLogger(__name__)


class MarketDataSimulator:
    """
    Симулятор рыночных данных.

    Генерирует реалистичные рыночные данные для тестирования.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_price: Decimal = Decimal("50000"),
        volatility: float = 0.02,  # 2% дневная волатильность
        trend: float = 0.0001,  # Ежечасный тренд
        seed: int = 42,
    ):
        self.symbol = symbol
        self.initial_price = initial_price
        self.volatility = volatility
        self.trend = trend
        self.seed = seed

        np.random.seed(seed)

        self._current_price = initial_price
        self._current_time = datetime.utcnow()

    def generate_tick(self) -> dict:
        """Сгенерировать новый тик"""
        # Geometric Brownian Motion
        dt = 1 / 24 / 365  # Ежесекундный шаг (упрощённо)

        # Логнормальное распределение для цен
        drift = float(self.trend) * dt
        shock = np.random.normal(0, float(self.volatility) * np.sqrt(dt))

        # Новая цена - используем float для расчётов
        current_float = float(self._current_price)
        price_change = current_float * (drift + shock)
        self._current_price = Decimal(str(max(0.01, current_float + price_change)))

        self._current_time += timedelta(seconds=1)

        return {
            "symbol": self.symbol,
            "price": self._current_price,
            "timestamp": self._current_time,
            "volume": np.random.random() * 100,
        }

    def simulate_candles(
        self,
        num_candles: int = 100,
        timeframe_seconds: int = 3600,  # 1 час
    ) -> list[dict]:
        """Сгенерировать свечи"""
        candles = []
        current_time = self._current_time

        for _ in range(num_candles):
            # Генерируем OHLC внутри свечи
            opens = []

            for _ in range(20):  # 20 тиков на свечу для реалистичности
                tick = self.generate_tick()
                opens.append(float(tick["price"]))

            open_price = opens[0]
            close_price = opens[-1]
            high_price = max(opens)
            low_price = min(opens)

            candle = {
                "open_time": int(current_time.timestamp()),
                "open": Decimal(str(open_price)),
                "high": Decimal(str(high_price)),
                "low": Decimal(str(low_price)),
                "close": Decimal(str(close_price)),
                "volume": Decimal(str(np.random.random() * 1000)),
                "quote_volume": Decimal(str(np.random.random() * 1000 * float(close_price))),
            }

            candles.append(candle)
            current_time += timedelta(seconds=timeframe_seconds)

        return candles


class PaperTradingSimulator:
    """
    Полный симулятор бумажной торговли.

    Объединяет:
    - Симулятор рынка
    - Paper trading engine
    - Запуск/остановка
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("1000"),
        symbol: str = "BTC/USDT",
        initial_price: Decimal = Decimal("50000"),
        update_interval: float = 0.1,  # 100ms
    ):
        self.initial_capital = initial_capital
        self.symbol = symbol
        self.update_interval = update_interval

        # Симулятор рынка
        self._market_sim = MarketDataSimulator(
            symbol=symbol,
            initial_price=initial_price,
        )

        # Paper engine
        self._paper_engine = PaperTradingEngine(
            initial_capital=initial_capital,
        )

        # Запуск
        self._running = False
        self._task: asyncio.Task | None = None

    def add_strategy(self, name: str, strategy):
        """Добавить стратегию"""
        self._paper_engine.add_strategy(name, strategy)

    async def start(self):
        """Запустить симуляцию"""
        self._running = True
        logger.info(f"Starting paper trading simulation: {self.symbol}")

        self._task = asyncio.create_task(self._run_simulation())

    async def stop(self):
        """Остановить симуляцию"""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Paper trading simulation stopped")

    async def _run_simulation(self):
        """Основной цикл симуляции"""
        while self._running:
            try:
                # Генерируем новый тик
                tick = self._market_sim.generate_tick()

                # Обрабатываем данные в paper engine
                await self._paper_engine.process_market_data(
                    symbol=self.symbol,
                    current_price=tick["price"],
                )

                # Логирование каждые 100 тиков
                if int(tick["timestamp"].timestamp()) % 100 == 0:
                    info = self._paper_engine.get_account_info()
                    logger.debug(
                        f"Tick {tick['timestamp']}: "
                        f"Price={tick['price']:.2f}, "
                        f"Equity={info['equity']}, "
                        f"Positions={info['open_positions']}"
                    )

                # Ожидание до следующего тика
                await asyncio.sleep(self.update_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Simulation error: {e}")
                await asyncio.sleep(1)

    def get_account_info(self) -> dict:
        """Получить информацию об аккаунте"""
        return self._paper_engine.get_account_info()

    def get_positions(self) -> list[PaperTrade]:
        """Получить позиции"""
        return self._paper_engine.get_positions()

    @property
    def is_running(self) -> bool:
        return self._running


# Утилита для быстрого запуска симуляции
async def run_simulation(
    capital: Decimal = Decimal("1000"),
    symbol: str = "BTC/USDT",
    initial_price: Decimal = Decimal("50000"),
    duration_minutes: int = 5,
    update_interval: float = 0.1,
) -> dict:
    """
    Запустить симуляцию на заданное время.

    Returns:
        Результаты симуляции
    """
    simulator = PaperTradingSimulator(
        initial_capital=capital,
        symbol=symbol,
        initial_price=initial_price,
        update_interval=update_interval,
    )

    # Расчёт количества тиков
    num_ticks = int(duration_minutes * 60 / update_interval)

    # Запуск
    simulator._running = True

    for _ in range(num_ticks):
        if not simulator._running:
            break

        tick = simulator._market_sim.generate_tick()
        await simulator._paper_engine.process_market_data(
            symbol=symbol,
            current_price=tick["price"],
        )

    simulator._running = False

    return simulator.get_account_info()


# Фабрика
def create_paper_simulator(
    capital: Decimal = Decimal("1000"),
    symbol: str = "BTC/USDT",
    initial_price: Decimal = Decimal("50000"),
) -> PaperTradingSimulator:
    """Создать симулятор"""
    return PaperTradingSimulator(
        initial_capital=capital,
        symbol=symbol,
        initial_price=initial_price,
    )
