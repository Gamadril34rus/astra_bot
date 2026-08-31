"""
ASTRA BOT — Alpha Decay Engine

Движок отслеживания деградации сигналов (Master Specification v2, Section 11)

Для каждого сигнала измеряет сохранение predictive power во времени:
- 0–1m
- 1–5m
- 5–15m
- 15–30m
- 30–60m
- 1–4h

Определяет:
- alpha_half_life
- signal_expiration

Если edge статистически исчез: SIGNAL_EXPIRED
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class SignalStrength:
    """Сила сигнала в определенном временном интервале"""
    time_interval: str  # "0-1m", "1-5m", etc.
    predictive_power: float  # R², Sharpe ratio, или другая метрика
    sample_size: int
    p_value: float  # Статистическая значимость
    is_significant: bool
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "time_interval": self.time_interval,
            "predictive_power": self.predictive_power,
            "sample_size": self.sample_size,
            "p_value": self.p_value,
            "is_significant": self.is_significant,
        }


@dataclass
class AlphaDecayProfile:
    """Профиль деградации альфа-сигнала"""
    signal_name: str
    symbol: str
    timeframe: str
    
    # Сила сигнала по интервалам
    strength_by_interval: dict[str, SignalStrength] = field(default_factory=dict)
    
    # Метрики деградации
    alpha_half_life: timedelta | None = None
    signal_expiration: datetime | None = None
    decay_rate: float | None = None  # Скорость деградации (0-1)
    
    # Статус
    is_active: bool = True
    is_expired: bool = False
    is_weakening: bool = False
    
    # Временные метки
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_name": self.signal_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strength_by_interval": {k: v.to_dict() for k, v in self.strength_by_interval.items()},
            "alpha_half_life": str(self.alpha_half_life) if self.alpha_half_life else None,
            "signal_expiration": self.signal_expiration.isoformat() if self.signal_expiration else None,
            "decay_rate": self.decay_rate,
            "is_active": self.is_active,
            "is_expired": self.is_expired,
            "is_weakening": self.is_weakening,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class DecayMeasurement:
    """Измерение деградации сигнала"""
    signal_name: str
    symbol: str
    timeframe: str
    
    # Временные параметры
    signal_creation_time: datetime
    current_time: datetime
    age: timedelta
    
    # Метрики предсказательной силы
    initial_predictive_power: float
    current_predictive_power: float
    predictive_power_history: list[tuple[datetime, float]]
    
    # Статистические тесты
    decay_p_value: float
    is_statistically_significant: bool
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_name": self.signal_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "age": str(self.age),
            "initial_predictive_power": self.initial_predictive_power,
            "current_predictive_power": self.current_predictive_power,
            "decay_p_value": self.decay_p_value,
            "is_statistically_significant": self.is_statistically_significant,
        }


class AlphaDecayEngine:
    """
    Движок отслеживания деградации альфа-сигнала.
    
    Измеряет сохранение predictive power сигналов во времени и определяет,
    когда сигнал больше не эффективен.
    """
    
    def __init__(self):
        # Интервалы времени для измерения
        self.time_intervals = [
            ("0-1m", timedelta(minutes=1)),
            ("1-5m", timedelta(minutes=4)),
            ("5-15m", timedelta(minutes=10)),
            ("15-30m", timedelta(minutes=15)),
            ("30-60m", timedelta(minutes=30)),
            ("1-4h", timedelta(hours=3)),
        ]
        
        # Пороги значимости
        self.significance_threshold = 0.05
        self.min_sample_size = 30
        
        # Хранение профилей сигналов
        self.signal_profiles: dict[str, AlphaDecayProfile] = {}
        
        # Хранение измерений
        self.measurements: list[DecayMeasurement] = []
    
    def _get_interval_key(self, age: timedelta) -> str:
        """Определить интервал для данного возраста сигнала"""
        for interval_name, interval_duration in self.time_intervals:
            if age <= interval_duration:
                return interval_name
        return "4h+"
    
    def measure_signal_strength(
        self,
        signal_name: str,
        symbol: str,
        timeframe: str,
        predictions: list[float],
        actuals: list[float],
        timestamps: list[datetime] | None = None
    ) -> SignalStrength:
        """
        Измерить силу сигнала в текущем интервале.
        
        Args:
            signal_name: Имя сигнала
            symbol: Символ инструмента
            timeframe: Таймфрейм
            predictions: Предсказания сигнала
            actuals: Фактические значения
            timestamps: Временные метки (опционально)
        
        Returns:
            SignalStrength
        """
        if len(predictions) < self.min_sample_size or len(actuals) < self.min_sample_size:
            return SignalStrength(
                time_interval="0-1m",
                predictive_power=0.0,
                sample_size=len(predictions),
                p_value=1.0,
                is_significant=False
            )
        
        # Рассчитать корреляцию между предсказаниями и фактическими значениями
        correlation = np.corrcoef(predictions, actuals)[0, 1]
        
        # Рассчитать R²
        r_squared = correlation ** 2
        
        # Рассчитать p-value для корреляции
        if len(predictions) > 2:
            t_stat = correlation * np.sqrt((len(predictions) - 2) / (1 - correlation ** 2))
            p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=len(predictions) - 2))
        else:
            p_value = 1.0
        
        # Определить интервал
        if timestamps and len(timestamps) >= 2:
            age = timestamps[-1] - timestamps[0]
            interval_key = self._get_interval_key(age)
        else:
            interval_key = "0-1m"
        
        return SignalStrength(
            time_interval=interval_key,
            predictive_power=r_squared,
            sample_size=len(predictions),
            p_value=p_value,
            is_significant=p_value < self.significance_threshold
        )
    
    def update_signal_profile(
        self,
        signal_name: str,
        symbol: str,
        timeframe: str,
        strength: SignalStrength
    ) -> AlphaDecayProfile:
        """
        Обновить профиль сигнала.
        
        Args:
            signal_name: Имя сигнала
            symbol: Символ инструмента
            timeframe: Таймфрейм
            strength: Сила сигнала в текущем интервале
        
        Returns:
            Обновлённый профиль сигнала
        """
        profile_key = f"{signal_name}:{symbol}:{timeframe}"
        
        if profile_key not in self.signal_profiles:
            self.signal_profiles[profile_key] = AlphaDecayProfile(
                signal_name=signal_name,
                symbol=symbol,
                timeframe=timeframe,
                created_at=datetime.now()
            )
        
        profile = self.signal_profiles[profile_key]
        profile.strength_by_interval[strength.time_interval] = strength
        profile.last_updated = datetime.now()
        
        # Обновить статус профиля
        self._update_profile_status(profile)
        
        return profile
    
    def _update_profile_status(self, profile: AlphaDecayProfile) -> None:
        """Обновить статус профиля на основе текущих измерений"""
        if not profile.strength_by_interval:
            profile.is_active = True
            profile.is_expired = False
            profile.is_weakening = False
            return
        
        # Проверить, есть ли значимые сигналы
        significant_intervals = [
            s for s in profile.strength_by_interval.values() 
            if s.is_significant
        ]
        
        if not significant_intervals:
            profile.is_active = False
            profile.is_expired = True
            profile.is_weakening = False
            return
        
        # Проверить деградацию
        # Рассчитать среднюю силу сигнала
        avg_power = np.mean([
            s.predictive_power for s in profile.strength_by_interval.values()
        ])
        
        # Если средняя сила ниже порога, сигнал слабеет
        if avg_power < 0.1:
            profile.is_weakening = True
            profile.is_active = False
        else:
            profile.is_weakening = False
            profile.is_active = True
        
        profile.is_expired = False
        
        # Рассчитать alpha half-life
        self._calculate_alpha_half_life(profile)
    
    def _calculate_alpha_half_life(self, profile: AlphaDecayProfile) -> None:
        """Рассчитать alpha half-life для профиля"""
        if len(profile.strength_by_interval) < 2:
            profile.alpha_half_life = None
            profile.decay_rate = None
            profile.signal_expiration = None
            return
        
        # Собрать данные для регрессии
        intervals = []
        powers = []
        
        for interval_name, interval_duration in self.time_intervals:
            if interval_name in profile.strength_by_interval:
                strength = profile.strength_by_interval[interval_name]
                intervals.append(interval_duration.total_seconds())
                powers.append(strength.predictive_power)
        
        if len(intervals) < 2:
            profile.alpha_half_life = None
            profile.decay_rate = None
            profile.signal_expiration = None
            return
        
        # Провести линейную регрессию
        X = np.array(intervals).reshape(-1, 1)
        y = np.array(powers)
        
        try:
            # Рассчитать коэффициенты регрессии
            A = np.vstack([X, np.ones(len(X))]).T
            slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
            
            # Рассчитать decay rate
            profile.decay_rate = float(slope)
            
            # Рассчитать half-life (время, за которое сила уменьшается вдвое)
            if slope < 0:
                half_life_seconds = -np.log(2) / slope
                profile.alpha_half_life = timedelta(seconds=half_life_seconds)
            else:
                profile.alpha_half_life = None
            
            # Определить время истечения сигнала
            if profile.alpha_half_life:
                # Сигнал истекает через 3 half-life
                profile.signal_expiration = datetime.now() + profile.alpha_half_life * 3
        except Exception as e:
            logger.debug(f"Error calculating alpha half-life: {e}")
            profile.alpha_half_life = None
            profile.decay_rate = None
            profile.signal_expiration = None
    
    def measure_decay(
        self,
        signal_name: str,
        symbol: str,
        timeframe: str,
        predictive_power_history: list[tuple[datetime, float]]
    ) -> DecayMeasurement:
        """
        Измерить деградацию сигнала во времени.
        
        Args:
            signal_name: Имя сигнала
            symbol: Символ инструмента
            timeframe: Таймфрейм
            predictive_power_history: История предсказательной силы
        
        Returns:
            DecayMeasurement
        """
        if len(predictive_power_history) < 2:
            return DecayMeasurement(
                signal_name=signal_name,
                symbol=symbol,
                timeframe=timeframe,
                signal_creation_time=datetime.now(),
                current_time=datetime.now(),
                age=timedelta(0),
                initial_predictive_power=0.0,
                current_predictive_power=0.0,
                predictive_power_history=predictive_power_history,
                decay_p_value=1.0,
                is_statistically_significant=False
            )
        
        timestamps, powers = zip(*predictive_power_history)
        signal_creation_time = timestamps[0]
        current_time = timestamps[-1]
        age = current_time - signal_creation_time
        
        initial_predictive_power = powers[0]
        current_predictive_power = powers[-1]
        
        # Провести статистический тест на деградацию
        # Используем тест Манна-Кендалла для тренда
        try:
            from scipy.stats import kendalltau
            time_seconds = [(t - timestamps[0]).total_seconds() for t in timestamps]
            tau, p_value = kendalltau(time_seconds, list(powers))
            
            # Если тренд отрицательный и значимый, то есть деградация
            is_significant = p_value < self.significance_threshold and tau < 0
        except Exception:
            p_value = 1.0
            is_significant = False
        
        measurement = DecayMeasurement(
            signal_name=signal_name,
            symbol=symbol,
            timeframe=timeframe,
            signal_creation_time=signal_creation_time,
            current_time=current_time,
            age=age,
            initial_predictive_power=initial_predictive_power,
            current_predictive_power=current_predictive_power,
            predictive_power_history=predictive_power_history,
            decay_p_value=p_value,
            is_statistically_significant=is_significant
        )
        
        self.measurements.append(measurement)
        
        return measurement
    
    def is_signal_expired(self, signal_name: str, symbol: str, timeframe: str) -> bool:
        """
        Проверить, истёк ли сигнал.
        
        Args:
            signal_name: Имя сигнала
            symbol: Символ инструмента
            timeframe: Таймфрейм
        
        Returns:
            True если сигнал истёк
        """
        profile_key = f"{signal_name}:{symbol}:{timeframe}"
        
        if profile_key not in self.signal_profiles:
            return False
        
        profile = self.signal_profiles[profile_key]
        
        # Проверить по времени истечения
        if profile.signal_expiration and datetime.now() > profile.signal_expiration:
            return True
        
        # Проверить по статусу
        if profile.is_expired:
            return True
        
        return False
    
    def is_signal_weakening(self, signal_name: str, symbol: str, timeframe: str) -> bool:
        """
        Проверить, слабеет ли сигнал.
        
        Args:
            signal_name: Имя сигнала
            symbol: Символ инструмента
            timeframe: Таймфрейм
        
        Returns:
            True если сигнал слабеет
        """
        profile_key = f"{signal_name}:{symbol}:{timeframe}"
        
        if profile_key not in self.signal_profiles:
            return False
        
        profile = self.signal_profiles[profile_key]
        return profile.is_weakening
    
    def get_signal_remaining_edge(
        self, 
        signal_name: str, 
        symbol: str, 
        timeframe: str
    ) -> float:
        """
        Получить оставшийся edge сигнала (Section 12).
        
        Args:
            signal_name: Имя сигнала
            symbol: Символ инструмента
            timeframe: Таймфрейм
        
        Returns:
            Оставшийся edge (0-1)
        """
        profile_key = f"{signal_name}:{symbol}:{timeframe}"
        
        if profile_key not in self.signal_profiles:
            return 0.0
        
        profile = self.signal_profiles[profile_key]
        
        if not profile.strength_by_interval:
            return 0.0
        
        # Рассчитать среднюю силу сигнала
        avg_power = np.mean([
            s.predictive_power for s in profile.strength_by_interval.values()
        ])
        
        # Если сигнал слабеет, уменьшить edge
        if profile.is_weakening:
            # Линейное уменьшение edge
            if profile.alpha_half_life:
                time_since_creation = datetime.now() - profile.created_at
                half_life_ratio = time_since_creation / profile.alpha_half_life
                decay_factor = max(0, 1 - half_life_ratio)
                return avg_power * decay_factor
        
        return avg_power
    
    def get_signal_age(
        self, 
        signal_name: str, 
        symbol: str, 
        timeframe: str
    ) -> timedelta | None:
        """
        Получить возраст сигнала (Section 12).
        
        Args:
            signal_name: Имя сигнала
            symbol: Символ инструмента
            timeframe: Таймфрейм
        
        Returns:
            Возраст сигнала или None
        """
        profile_key = f"{signal_name}:{symbol}:{timeframe}"
        
        if profile_key not in self.signal_profiles:
            return None
        
        profile = self.signal_profiles[profile_key]
        return datetime.now() - profile.created_at
    
    def get_expected_lifetime(
        self, 
        signal_name: str, 
        symbol: str, 
        timeframe: str
    ) -> timedelta | None:
        """
        Получить ожидаемое время жизни сигнала (Section 12).
        
        Args:
            signal_name: Имя сигнала
            symbol: Символ инструмента
            timeframe: Таймфрейм
        
        Returns:
            Ожидаемое время жизни или None
        """
        profile_key = f"{signal_name}:{symbol}:{timeframe}"
        
        if profile_key not in self.signal_profiles:
            return None
        
        profile = self.signal_profiles[profile_key]
        
        if profile.alpha_half_life:
            # Ожидаемое время жизни = 3 * half-life
            return profile.alpha_half_life * 3
        
        return None
    
    def cleanup_old_profiles(self, max_age_days: int = 30) -> int:
        """
        Очистить старые профили сигналов.
        
        Args:
            max_age_days: Максимальный возраст профилей в днях
        
        Returns:
            Количество удалённых профилей
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        profiles_to_remove = [
            key for key, profile in self.signal_profiles.items()
            if profile.last_updated < cutoff
        ]
        
        for key in profiles_to_remove:
            del self.signal_profiles[key]
        
        return len(profiles_to_remove)


# Глобальный экземпляр Alpha Decay Engine
_alpha_decay_engine: AlphaDecayEngine | None = None


def get_alpha_decay_engine() -> AlphaDecayEngine:
    """Получить глобальный Alpha Decay Engine"""
    global _alpha_decay_engine
    if _alpha_decay_engine is None:
        _alpha_decay_engine = AlphaDecayEngine()
    return _alpha_decay_engine


def reset_alpha_decay_engine():
    """Сбросить Alpha Decay Engine (для тестов)"""
    global _alpha_decay_engine
    _alpha_decay_engine = AlphaDecayEngine()
