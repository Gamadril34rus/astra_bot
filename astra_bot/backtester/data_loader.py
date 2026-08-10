"""
ASTRA BOT — Historical Data Loader
Загрузчик исторических данных для бэктеста
"""

import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class HistoricalDataLoader:
    """
    Загрузчик исторических данных.

    Поддерживает:
    - CSV файлы
    - Pandas DataFrame
    - Генерацию тестовых данных
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_csv(
        self,
        filepath: str,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
    ) -> list[dict]:
        """
        Загрузить данные из CSV.

        Ожидаемые колонки:
        - open_time, open, high, low, close, volume
        """
        df = pd.read_csv(filepath)
        return self._df_to_candles(df, symbol, timeframe)

    def load_from_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
    ) -> list[dict]:
        """Загрузить данные из DataFrame"""
        return self._df_to_candles(df, symbol, timeframe)

    def _df_to_candles(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> list[dict]:
        """Конвертировать DataFrame в список свечей"""
        candles = []

        for _, row in df.iterrows():
            candle = {
                "open_time": self._parse_timestamp(row.get("open_time", row.name)),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0)),
                "quote_volume": float(row.get("quote_volume", row.get("volume", 0) * row["close"])),
            }
            candles.append(candle)

        return candles

    def _parse_timestamp(self, ts) -> int:
        """Парсить timestamp"""
        if isinstance(ts, (int, float)):
            return int(ts)
        elif isinstance(ts, str):
            dt = pd.to_datetime(ts)
            return int(dt.timestamp())
        elif isinstance(ts, datetime):
            return int(ts.timestamp())
        else:
            return int(ts.timestamp()) if hasattr(ts, 'timestamp') else int(ts)

    def generate_test_data(
        self,
        symbol: str = "BTC/USDT",
        start_date: datetime = None,
        end_date: datetime = None,
        timeframe: str = "1h",
        seed: int = 42,
    ) -> list[dict]:
        """
        Сгенерировать тестовые данные.

        Создаёт реалистичные данные с:
        - Общим трендом
        - Сезонностью
        - Шумом
        - Волатильностью
        """
        if start_date is None:
            start_date = datetime(2024, 1, 1)
        if end_date is None:
            end_date = datetime(2024, 12, 31)

        np.random.seed(seed)

        # Расчёт количества свечей
        if timeframe == "1m":
            minutes_per_candle = 1
        elif timeframe == "5m":
            minutes_per_candle = 5
        elif timeframe == "15m":
            minutes_per_candle = 15
        elif timeframe == "1h":
            minutes_per_candle = 60
        elif timeframe == "4h":
            minutes_per_candle = 240
        elif timeframe == "1d":
            minutes_per_candle = 1440
        else:
            minutes_per_candle = 60

        total_minutes = (end_date - start_date).total_seconds() / 60
        num_candles = int(total_minutes / minutes_per_candle)

        # Параметры
        initial_price = 50000
        trend_rate = 0.0001  # Ежечасный тренд
        volatility = 200  # Стандартное отклонение

        candles = []
        current_price = initial_price
        current_time = int(start_date.timestamp())

        for _ in range(num_candles):
            # Тренд
            trend = current_price * trend_rate

            # Сезонность (дневная)
            hour = datetime.fromtimestamp(current_time).hour
            seasonal = np.sin(2 * np.pi * hour / 24) * 100

            # Шум
            noise = np.random.normal(0, volatility)

            # Волатильность (кластеризация)
            if np.random.random() < 0.1:  # 10% времени повышенная волатильность
                noise *= 3

            # Цена
            open_price = current_price + trend + seasonal
            close_price = open_price + noise * 0.5
            high_price = max(open_price, close_price) + abs(noise) * 0.5
            low_price = min(open_price, close_price) - abs(noise) * 0.5

            volume = 100 + np.random.random() * 200 + abs(noise) / volatility * 100

            candle = {
                "open_time": current_time,
                "open": float(open_price),
                "high": float(high_price),
                "low": float(low_price),
                "close": float(close_price),
                "volume": float(volume),
                "quote_volume": float(volume * close_price),
            }

            candles.append(candle)

            current_price = close_price
            current_time += minutes_per_candle * 60

        logger.info(f"Generated {len(candles)} test candles for {symbol}")
        return candles

    def save_candles(self, candles: list[dict], filepath: str):
        """Сохранить свечи в CSV"""
        df = pd.DataFrame(candles)
        df.to_csv(filepath, index=False)
        logger.info(f"Saved {len(candles)} candles to {filepath}")

    def load_or_generate(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = "1h",
        filepath: str | None = None,
    ) -> list[dict]:
        """
        Загрузить данные или сгенерировать если файл не найден.
        """
        if filepath and Path(filepath).exists():
            logger.info(f"Loading data from {filepath}")
            return self.load_csv(filepath, symbol, timeframe)
        else:
            logger.info(f"Generating test data for {symbol}")
            return self.generate_test_data(symbol, start_date, end_date, timeframe)


# Глобальный загрузчик
_data_loader: HistoricalDataLoader | None = None


def get_data_loader(data_dir: str = "data") -> HistoricalDataLoader:
    """Получить глобальный загрузчик данных"""
    global _data_loader
    if _data_loader is None:
        _data_loader = HistoricalDataLoader(data_dir)
    return _data_loader
