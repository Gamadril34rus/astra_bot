"""
ASTRA BOT — ML Feature Pipeline
Генерация признаков для ML модели
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np

from ..core import models
from ..core.utils import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_rsi,
    exponential_moving_average,
)

logger = logging.getLogger(__name__)


class FeatureType(Enum):
    """Тип признака"""
    PRICE = "price"
    VOLUME = "volume"
    VOLATILITY = "volatility"
    MOMENTUM = "momentum"
    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    ORDER_BOOK = "order_book"
    TIME = "time"
    MARKET_REGIME = "market_regime"
    CORRELATION = "correlation"


@dataclass
class FeatureConfig:
    """Конфигурация признаков"""
    # Ценовые признаки
    include_price_features: bool = True
    price_periods: list[int] = field(default_factory=lambda: [1, 5, 15, 60, 240, 1440])

    # Объёмные признаки
    include_volume_features: bool = True
    volume_periods: list[int] = field(default_factory=lambda: [20, 50, 100])

    # Волатильные признаки
    include_volatility_features: bool = True
    atr_periods: list[int] = field(default_factory=lambda: [14, 28, 56])

    # Momentum признаки
    include_momentum_features: bool = True
    rsi_period: int = 14
    momentum_periods: list[int] = field(default_factory=lambda: [10, 20, 50])

    # Трендовые признаки
    include_trend_features: bool = True
    ema_periods: list[int] = field(default_factory=lambda: [20, 50, 200])
    adx_period: int = 14

    # Mean reversion признаки
    include_mean_reversion_features: bool = True
    bb_period: int = 20
    bb_std_dev: float = 2.0

    # Time признаки
    include_time_features: bool = True

    # Market regime признаки
    include_regime_features: bool = True

    # Order book признаки
    include_orderbook_features: bool = True

    # Корреляционные признаки
    include_correlation_features: bool = True
    correlation_periods: list[int] = field(default_factory=lambda: [1, 5, 24])

    @property
    def all_periods(self) -> list[int]:
        """Все используемые периоды"""
        periods = set()
        periods.update(self.price_periods)
        periods.update(self.volume_periods)
        periods.update(self.atr_periods)
        periods.update(self.ema_periods)
        periods.update(self.momentum_periods)
        periods.update(self.correlation_periods)
        return sorted(periods)


@dataclass
class FeatureVector:
    """Вектор признаков"""
    symbol: str
    timestamp: datetime
    features: dict[str, float]
    feature_hash: str = ""
    is_valid: bool = True

    def to_array(self, feature_names: list[str]) -> np.ndarray:
        """Конвертировать в numpy массив"""
        return np.array([self.features.get(name, 0.0) for name in feature_names])

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать в словарь"""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "features": self.features,
            "feature_hash": self.feature_hash,
            "is_valid": self.is_valid,
        }


class FeaturePipeline:
    """
    Пайплайн генерации признаков для ML модели.

    Генерирует признаки на основе рыночных данных:
    - Ценовые (returns, price levels)
    - Объёмные (volume ratios, volume MA)
    - Волатильные (ATR, historical volatility)
    - Momentum (RSI, MACD-like)
    - Trend (EMA distances, trend strength)
    - Mean reversion (Bollinger Bands position, z-score)
    - Time (hour, day of week)
    - Order book (imbalance, spread)
    """

    def __init__(self, config: FeatureConfig = None):
        self.config = config or FeatureConfig()

        # Кэш последних признаков
        self._last_features: dict[str, FeatureVector] = {}

        # Список всех возможных имён признаков
        self._feature_names: list[str] = []
        self._init_feature_names()

    def _init_feature_names(self):
        """Инициализировать список имён признаков"""
        names = []

        # Price features
        if self.config.include_price_features:
            for period in self.config.price_periods:
                names.append(f"returns_{period}m")

        # Volume features
        if self.config.include_volume_features:
            names.append("volume_ratio")
            names.append("volume_zscore")
            for period in self.config.volume_periods:
                names.append(f"volume_ma_{period}")

        # Volatility features
        if self.config.include_volatility_features:
            names.append("atr")
            names.append("atr_ratio")
            names.append("historical_volatility_24h")
            for period in self.config.atr_periods:
                names.append(f"atr_{period}")

        # Momentum features
        if self.config.include_momentum_features:
            names.append("rsi")
            names.append("momentum_10")
            names.append("momentum_20")
            names.append("momentum_50")

        # Trend features
        if self.config.include_trend_features:
            names.append("ema_distance_20_50")
            names.append("ema_distance_50_200")
            names.append("trend_strength")
            names.append("trend_direction")

        # Mean reversion features
        if self.config.include_mean_reversion_features:
            names.append("bb_position")
            names.append("bb_width")
            names.append("z_score")

        # Time features
        if self.config.include_time_features:
            names.extend(["hour", "day_of_week", "is_weekend"])

        # Market regime features
        if self.config.include_regime_features:
            names.append("regime_encoded")
            names.append("volatility_regime")

        # Order book features
        if self.config.include_orderbook_features:
            names.append("spread_pct")
            names.append("order_book_imbalance")
            names.append("order_book_depth")

        # Correlation features
        if self.config.include_correlation_features:
            for period in self.config.correlation_periods:
                names.append(f"btc_correlation_{period}h")

        self._feature_names = sorted(list(set(names)))

    @property
    def feature_names(self) -> list[str]:
        """Получить список имён признаков"""
        return self._feature_names.copy()

    def generate_features(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook: models.OrderBook | None = None,
        market_regime: str | None = None,
        btc_correlation: float | None = None,
        current_time: datetime | None = None,
    ) -> FeatureVector:
        """
        Сгенерировать вектор признаков.

        Args:
            symbol: Торговый символ
            candles: Исторические свечи (минимум 200 для EMA200)
            orderbook: Стакан заявок (опционально)
            market_regime: Текущий режим рынка (опционально)
            btc_correlation: Корреляция с BTC (опционально)
            current_time: Текущее время (опционально)

        Returns:
            FeatureVector с рассчитанными признаками
        """
        if len(candles) < 50:
            return FeatureVector(
                symbol=symbol,
                timestamp=current_time or datetime.utcnow(),
                features={},
                is_valid=False,
            )

        # Конвертируем в numpy массивы
        closes = np.array([float(c.close) for c in candles])
        highs = np.array([float(c.high) for c in candles])
        lows = np.array([float(c.low) for c in candles])
        volumes = np.array([float(c.volume) for c in candles])

        features = {}

        # === PRICE FEATURES ===
        if self.config.include_price_features:
            current_price = closes[-1]

            for period in self.config.price_periods:
                if len(closes) > period:
                    returns = (closes[-1] - closes[-period - 1]) / closes[-period - 1]
                    features[f"returns_{period}m"] = float(returns)

        # === VOLUME FEATURES ===
        if self.config.include_volume_features:
            if len(volumes) >= 20:
                avg_volume = np.mean(volumes[-20:])
                current_volume = volumes[-1]

                if avg_volume > 0:
                    features["volume_ratio"] = float(current_volume / avg_volume)
                    features["volume_zscore"] = float(
                        (current_volume - avg_volume) / np.std(volumes[-20:])
                    )

                for period in self.config.volume_periods:
                    if len(volumes) > period:
                        ma = np.mean(volumes[-period:])
                        features[f"volume_ma_{period}"] = float(ma)

        # === VOLATILITY FEATURES ===
        if self.config.include_volatility_features:
            if len(highs) >= 15:
                atr = calculate_atr(
                    highs[-15:].tolist(),
                    lows[-15:].tolist(),
                    closes[-15:].tolist(),
                    14,
                )
                if atr:
                    features["atr"] = float(atr)
                    features["atr_ratio"] = float(atr / current_price if current_price > 0 else 0)

            # Historical volatility 24h
            if len(closes) >= 25:
                returns_24h = np.diff(closes[-25:]) / closes[-25:-1]
                features["historical_volatility_24h"] = float(np.std(returns_24h) * np.sqrt(24))

            for period in self.config.atr_periods:
                if len(highs) >= period + 1:
                    atr_val = calculate_atr(
                        highs[-period-1:].tolist(),
                        lows[-period-1:].tolist(),
                        closes[-period-1:].tolist(),
                        min(period, 14),
                    )
                    if atr_val:
                        features[f"atr_{period}"] = float(atr_val)

        # === MOMENTUM FEATURES ===
        if self.config.include_momentum_features:
            # RSI
            if len(closes) >= self.config.rsi_period + 1:
                rsi = calculate_rsi(closes.tolist(), self.config.rsi_period)
                if rsi:
                    features["rsi"] = float(rsi)

            # Momentum
            for period in self.config.momentum_periods:
                if len(closes) > period:
                    momentum = (closes[-1] - closes[-period-1]) / closes[-period-1] * 100
                    features[f"momentum_{period}"] = float(momentum)

        # === TREND FEATURES ===
        if self.config.include_trend_features:
            # EMA distances
            if len(closes) >= 200:
                ema20 = exponential_moving_average(closes[-20:].tolist(), 20)
                ema50 = exponential_moving_average(closes[-50:].tolist(), 50)
                ema200 = exponential_moving_average(closes[-200:].tolist(), 200)

                if ema20 and ema50:
                    features["ema_distance_20_50"] = float(
                        (ema20 - ema50) / ema50 * 100 if ema50 > 0 else 0
                    )

                if ema50 and ema200:
                    features["ema_distance_50_200"] = float(
                        (ema50 - ema200) / ema200 * 100 if ema200 > 0 else 0
                    )

                # Trend strength (упрощённый ADX)
                if len(highs) >= 15:
                    high_range = np.mean(np.diff(highs[-15:]))
                    low_range = np.mean(np.diff(lows[-15:]))
                    price_change = closes[-1] - closes[-15]
                    trend_strength = abs(price_change) / (high_range + low_range + 0.0001)
                    features["trend_strength"] = float(min(1.0, trend_strength))
                    features["trend_direction"] = float(
                        1.0 if price_change > 0 else (-1.0 if price_change < 0 else 0.0)
                    )

        # === MEAN REVERSION FEATURES ===
        if self.config.include_mean_reversion_features:
            if len(closes) >= self.config.bb_period:
                bb = calculate_bollinger_bands(
                    closes.tolist(),
                    self.config.bb_period,
                    self.config.bb_std_dev,
                )
                if bb:
                    bb_upper = bb["upper"]
                    bb_lower = bb["lower"]
                    bb_middle = bb["middle"]

                    if bb_upper > bb_lower:
                        features["bb_position"] = float(
                            (closes[-1] - bb_lower) / (bb_upper - bb_lower) * 2 - 1
                        )
                        features["bb_width"] = float(bb["bandwidth"] * 100)

                    # Z-score
                    std = bb.get("std", 0)
                    if std > 0:
                        features["z_score"] = float(
                            (closes[-1] - bb_middle) / std
                        )

        # === TIME FEATURES ===
        if self.config.include_time_features:
            time = current_time or datetime.utcnow()
            features["hour"] = float(time.hour) / 24.0
            features["day_of_week"] = float(time.weekday()) / 7.0
            features["is_weekend"] = float(1.0 if time.weekday() >= 5 else 0.0)

        # === MARKET REGIME FEATURES ===
        if self.config.include_regime_features:
            # Режим кодируется one-hot (упрощённо)
            regime_map = {
                "BULL_TREND": 0,
                "BEAR_TREND": 1,
                "RANGE": 2,
                "BREAKOUT": 3,
                "HIGH_VOLATILITY": 4,
                "LOW_VOLATILITY": 5,
                "PANIC": 6,
                "UNKNOWN": 7,
            }
            features["regime_encoded"] = float(
                regime_map.get(market_regime or "UNKNOWN", 7)
            ) / 7.0

            # Волатильный режим
            atr_val = features.get("atr", 0)
            features["volatility_regime"] = float(
                1.0 if atr_val > (current_price * 0.03) else 0.0
            )

        # === ORDER BOOK FEATURES ===
        if self.config.include_orderbook_features and orderbook:
            spread = orderbook.spread
            mid_price = orderbook.mid_price

            if spread and mid_price and mid_price > 0:
                features["spread_pct"] = float(spread / mid_price * 100)

            imbalance = orderbook.get_imbalance()
            features["order_book_imbalance"] = float(imbalance)
            features["order_book_depth"] = float(
                sum(e.quantity for e in orderbook.bids[:10]) +
                sum(e.quantity for e in orderbook.asks[:10])
            )

        # === CORRELATION FEATURES ===
        if self.config.include_correlation_features and btc_correlation is not None:
            for period in self.config.correlation_periods:
                features[f"btc_correlation_{period}h"] = float(
                    np.clip(btc_correlation, -1.0, 1.0)
                )

        # Хэш признаков
        feature_hash = self._calculate_hash(features)

        return FeatureVector(
            symbol=symbol,
            timestamp=current_time or datetime.utcnow(),
            features=features,
            feature_hash=feature_hash,
            is_valid=True,
        )

    def _calculate_hash(self, features: dict[str, float]) -> str:
        """Рассчитать хэш признаков"""
        # Простой хэш на основе отсортированных ключей и значений
        items = sorted(features.items())
        hash_str = ",".join(f"{k}:{v:.6f}" for k, v in items)

        # Простой хэш (в продакшене использовать hashlib)
        hash_value = 0
        for char in hash_str:
            hash_value = ((hash_value << 5) - hash_value) + ord(char)
            hash_value &= 0xFFFFFFFF

        return str(hash_value)

    def validate_features(self, vector: FeatureVector) -> bool:
        """Валидировать вектор признаков"""
        if not vector.is_valid:
            return False

        # Проверка на NaN и inf
        for value in vector.features.values():
            if np.isnan(value) or np.isinf(value):
                return False

        # Проверка минимального количества признаков
        if len(vector.features) < len(self._feature_names) * 0.5:
            return False

        return True

    def normalize_features(
        self,
        vector: FeatureVector,
        mean: dict[str, float] = None,
        std: dict[str, float] = None,
    ) -> FeatureVector:
        """Нормализовать признаки"""
        normalized = {}

        for name, value in vector.features.items():
            if mean and name in mean and std and name in std and std[name] > 0:
                normalized[name] = (value - mean[name]) / std[name]
            else:
                normalized[name] = value

        return FeatureVector(
            symbol=vector.symbol,
            timestamp=vector.timestamp,
            features=normalized,
            feature_hash=vector.feature_hash,
            is_valid=vector.is_valid,
        )


# Глобальный пайплайн
_feature_pipeline: FeaturePipeline | None = None


def get_feature_pipeline() -> FeaturePipeline:
    """Получить глобальный пайплайн признаков"""
    global _feature_pipeline
    if _feature_pipeline is None:
        _feature_pipeline = FeaturePipeline()
    return _feature_pipeline


def reset_feature_pipeline():
    """Сбросить пайплайн (для тестов)"""
    global _feature_pipeline
    _feature_pipeline = None
