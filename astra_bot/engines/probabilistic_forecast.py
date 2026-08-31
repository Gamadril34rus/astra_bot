"""
ASTRA BOT — Probabilistic Forecast Engine

Движок вероятностного прогнозирования (Master Specification v2, Section 4)

Вместо единственного предсказания создаёт распределение возможных исходов.

Минимально:
- P(return < -X)
- P(return ≈ 0)
- P(return > X)

Дополнительно:
- expected_return
- return_std
- tail_probability
- expected_MFE
- expected_MAE
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class ForecastDistribution:
    """Распределение вероятностей исходов"""
    # Вероятности ключевых исходов
    p_return_negative: float  # P(return < 0)
    p_return_positive: float  # P(return > 0)
    p_return_neutral: float  # P(return ≈ 0)
    
    # Распределение доходности
    expected_return: float  # Ожидаемая доходность (%)
    return_std: float  # Стандартное отклонение доходности
    
    # Хвостовые вероятности
    p_return_lt_neg_x: float  # P(return < -X), где X = expected_return
    p_return_gt_x: float  # P(return > X), где X = expected_return
    
    # Дополнительные метрики
    tail_probability: float  # Вероятность экстремальных исходов
    expected_MFE: float | None = None  # Ожидаемый Maximum Favorable Excursion
    expected_MAE: float | None = None  # Ожидаемый Maximum Adverse Excursion
    
    # Метаданные
    distribution_type: str = "normal"  # тип распределения
    parameters: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "p_return_negative": self.p_return_negative,
            "p_return_positive": self.p_return_positive,
            "p_return_neutral": self.p_return_neutral,
            "expected_return": self.expected_return,
            "return_std": self.return_std,
            "p_return_lt_neg_x": self.p_return_lt_neg_x,
            "p_return_gt_x": self.p_return_gt_x,
            "tail_probability": self.tail_probability,
            "expected_MFE": self.expected_MFE,
            "expected_MAE": self.expected_MAE,
            "distribution_type": self.distribution_type,
            "parameters": self.parameters,
        }


@dataclass
class ForecastResult:
    """Результат вероятностного прогноза"""
    symbol: str
    timeframe: str
    forecast_horizon: str  # 1m, 5m, 15m, 1h, 4h
    distribution: ForecastDistribution
    timestamp: datetime
    model_version: str
    uncertainty: float  # Неопределённость прогноза
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "forecast_horizon": self.forecast_horizon,
            "distribution": self.distribution.to_dict(),
            "timestamp": self.timestamp.isoformat(),
            "model_version": self.model_version,
            "uncertainty": self.uncertainty,
        }


class ProbabilisticForecastEngine:
    """
    Движок вероятностного прогнозирования.
    
    Создаёт распределения вероятностей для различных горизонтов прогноза
    и оценивает неопределённость этих прогнозов.
    """
    
    def __init__(self):
        # Поддерживаемые горизонты прогноза
        self.supported_horizons = ["1m", "5m", "15m", "30m", "1h", "4h"]
        
        # Параметры распределений
        self.default_parameters = {
            "normal": {"mu": 0.0, "sigma": 1.0},
            "student_t": {"df": 5.0, "loc": 0.0, "scale": 1.0},
            "skew_normal": {"a": 0.0, "loc": 0.0, "scale": 1.0},
        }
    
    def fit_normal_distribution(
        self, 
        historical_returns: list[float],
        current_prediction: float
    ) -> ForecastDistribution:
        """
        Подогнать нормальное распределение под исторические доходности.
        
        Args:
            historical_returns: Исторические доходности (%)
            current_prediction: Текущее предсказание (%)
        
        Returns:
            ForecastDistribution
        """
        if not historical_returns or len(historical_returns) < 2:
            # Дефолтное распределение при недостатке данных
            return ForecastDistribution(
                p_return_negative=0.5,
                p_return_positive=0.5,
                p_return_neutral=0.0,
                expected_return=current_prediction,
                return_std=2.0,
                p_return_lt_neg_x=0.16,
                p_return_gt_x=0.16,
                tail_probability=0.05,
                distribution_type="normal",
                parameters={"mu": current_prediction, "sigma": 2.0}
            )
        
        # Рассчитать параметры распределения
        mu = np.mean(historical_returns)
        sigma = np.std(historical_returns)
        
        # Скорректировать среднее на основе текущего предсказания
        # (сглаживание к предсказанию)
        alpha = 0.3  # Вес текущего предсказания
        adjusted_mu = alpha * current_prediction + (1 - alpha) * mu
        
        # Рассчитать вероятности
        p_negative = stats.norm.cdf(0, loc=adjusted_mu, scale=sigma)
        p_positive = 1 - p_negative
        p_neutral = 0.05  # Вероятность нейтрального исхода
        
        # Скорректировать вероятности
        p_negative = p_negative * (1 - p_neutral)
        p_positive = p_positive * (1 - p_neutral)
        
        # Рассчитать хвостовые вероятности
        x = abs(adjusted_mu)
        p_lt_neg_x = stats.norm.cdf(-x, loc=adjusted_mu, scale=sigma)
        p_gt_x = 1 - stats.norm.cdf(x, loc=adjusted_mu, scale=sigma)
        
        # Рассчитать вероятность экстремальных исходов (за пределами ±2σ)
        tail_probability = 2 * (1 - stats.norm.cdf(2, loc=0, scale=1))
        
        return ForecastDistribution(
            p_return_negative=p_negative,
            p_return_positive=p_positive,
            p_return_neutral=p_neutral,
            expected_return=adjusted_mu,
            return_std=sigma,
            p_return_lt_neg_x=p_lt_neg_x,
            p_return_gt_x=p_gt_x,
            tail_probability=tail_probability,
            distribution_type="normal",
            parameters={"mu": float(adjusted_mu), "sigma": float(sigma)}
        )
    
    def fit_student_t_distribution(
        self, 
        historical_returns: list[float],
        current_prediction: float
    ) -> ForecastDistribution:
        """
        Подогнать t-распределение Стьюдента под исторические доходности.
        
        Args:
            historical_returns: Исторические доходности (%)
            current_prediction: Текущее предсказание (%)
        
        Returns:
            ForecastDistribution
        """
        if not historical_returns or len(historical_returns) < 3:
            # Дефолтное распределение при недостатке данных
            return ForecastDistribution(
                p_return_negative=0.5,
                p_return_positive=0.5,
                p_return_neutral=0.0,
                expected_return=current_prediction,
                return_std=2.0,
                p_return_lt_neg_x=0.2,
                p_return_gt_x=0.2,
                tail_probability=0.1,
                distribution_type="student_t",
                parameters={"df": 5.0, "loc": current_prediction, "scale": 2.0}
            )
        
        # Оценить параметры t-распределения
        df, loc, scale = stats.t.fit(historical_returns)
        
        # Скорректировать loc на основе текущего предсказания
        alpha = 0.3
        adjusted_loc = alpha * current_prediction + (1 - alpha) * loc
        
        # Рассчитать вероятности
        p_negative = stats.t.cdf(0, df=df, loc=adjusted_loc, scale=scale)
        p_positive = 1 - p_negative
        p_neutral = 0.05
        
        p_negative = p_negative * (1 - p_neutral)
        p_positive = p_positive * (1 - p_neutral)
        
        # Рассчитать хвостовые вероятности
        x = abs(adjusted_loc)
        p_lt_neg_x = stats.t.cdf(-x, df=df, loc=adjusted_loc, scale=scale)
        p_gt_x = 1 - stats.t.cdf(x, df=df, loc=adjusted_loc, scale=scale)
        
        # Рассчитать вероятность экстремальных исходов
        # Для t-распределения хвосты тяжелее, чем у нормального
        tail_probability = 0.15 if df < 10 else 0.10
        
        return ForecastDistribution(
            p_return_negative=p_negative,
            p_return_positive=p_positive,
            p_return_neutral=p_neutral,
            expected_return=adjusted_loc,
            return_std=scale,
            p_return_lt_neg_x=p_lt_neg_x,
            p_return_gt_x=p_gt_x,
            tail_probability=tail_probability,
            distribution_type="student_t",
            parameters={"df": float(df), "loc": float(adjusted_loc), "scale": float(scale)}
        )
    
    def fit_skew_normal_distribution(
        self, 
        historical_returns: list[float],
        current_prediction: float
    ) -> ForecastDistribution:
        """
        Подогнать асимметричное нормальное распределение под исторические доходности.
        
        Args:
            historical_returns: Исторические доходности (%)
            current_prediction: Текущее предсказание (%)
        
        Returns:
            ForecastDistribution
        """
        if not historical_returns or len(historical_returns) < 3:
            return ForecastDistribution(
                p_return_negative=0.5,
                p_return_positive=0.5,
                p_return_neutral=0.0,
                expected_return=current_prediction,
                return_std=2.0,
                p_return_lt_neg_x=0.18,
                p_return_gt_x=0.18,
                tail_probability=0.06,
                distribution_type="skew_normal",
                parameters={"a": 0.0, "loc": current_prediction, "scale": 2.0}
            )
        
        # Оценить параметры асимметричного нормального распределения
        from scipy.stats import skewnorm
        a, loc, scale = skewnorm.fit(historical_returns)
        
        # Скорректировать loc на основе текущего предсказания
        alpha = 0.3
        adjusted_loc = alpha * current_prediction + (1 - alpha) * loc
        
        # Рассчитать вероятности
        p_negative = skewnorm.cdf(0, a=a, loc=adjusted_loc, scale=scale)
        p_positive = 1 - p_negative
        p_neutral = 0.05
        
        p_negative = p_negative * (1 - p_neutral)
        p_positive = p_positive * (1 - p_neutral)
        
        # Рассчитать хвостовые вероятности
        x = abs(adjusted_loc)
        p_lt_neg_x = skewnorm.cdf(-x, a=a, loc=adjusted_loc, scale=scale)
        p_gt_x = 1 - skewnorm.cdf(x, a=a, loc=adjusted_loc, scale=scale)
        
        # Рассчитать вероятность экстремальных исходов
        tail_probability = 0.08
        
        return ForecastDistribution(
            p_return_negative=p_negative,
            p_return_positive=p_positive,
            p_return_neutral=p_neutral,
            expected_return=adjusted_loc,
            return_std=scale,
            p_return_lt_neg_x=p_lt_neg_x,
            p_return_gt_x=p_gt_x,
            tail_probability=tail_probability,
            distribution_type="skew_normal",
            parameters={"a": float(a), "loc": float(adjusted_loc), "scale": float(scale)}
        )
    
    def create_multi_horizon_forecast(
        self,
        symbol: str,
        timeframe: str,
        model_version: str,
        predictions: dict[str, float],  # horizon -> prediction
        historical_returns_by_horizon: dict[str, list[float]],  # horizon -> returns
        uncertainty: float
    ) -> dict[str, ForecastResult]:
        """
        Создать прогнозы для нескольких горизонтов (Section 5).
        
        Args:
            symbol: Символ инструмента
            timeframe: Таймфрейм данных
            model_version: Версия модели
            predictions: Предсказания для каждого горизонта
            historical_returns_by_horizon: Исторические доходности по горизонтам
            uncertainty: Неопределённость прогноза
        
        Returns:
            Словарь с прогнозами для каждого горизонта
        """
        results = {}
        timestamp = datetime.now()
        
        for horizon in self.supported_horizons:
            if horizon not in predictions:
                continue
                
            current_prediction = predictions[horizon]
            historical_returns = historical_returns_by_horizon.get(horizon, [])
            
            # Выбрать тип распределения в зависимости от данных
            # Для коротких горизонтов используем t-распределение (тяжёлые хвосты)
            # Для длинных горизонтов используем асимметричное нормальное
            if horizon in ["1m", "5m"]:
                distribution = self.fit_student_t_distribution(
                    historical_returns, current_prediction
                )
            elif horizon in ["15m", "30m"]:
                distribution = self.fit_normal_distribution(
                    historical_returns, current_prediction
                )
            else:  # 1h, 4h
                distribution = self.fit_skew_normal_distribution(
                    historical_returns, current_prediction
                )
            
            results[horizon] = ForecastResult(
                symbol=symbol,
                timeframe=timeframe,
                forecast_horizon=horizon,
                distribution=distribution,
                timestamp=timestamp,
                model_version=model_version,
                uncertainty=uncertainty
            )
        
        return results
    
    def get_consensus_forecast(
        self,
        forecasts: dict[str, ForecastResult]
    ) -> ForecastResult | None:
        """
        Получить консенсусный прогноз из нескольких горизонтов (Section 5).
        
        Args:
            forecasts: Прогнозы для разных горизонтов
        
        Returns:
            Консенсусный прогноз или None
        """
        if not forecasts:
            return None
        
        # Собрать все ожидаемые доходности
        expected_returns = []
        for horizon, forecast in forecasts.items():
            expected_returns.append(forecast.distribution.expected_return)
        
        # Рассчитать среднюю ожидаемую доходность
        consensus_return = np.mean(expected_returns)
        
        # Рассчитать среднюю неопределённость
        consensus_uncertainty = np.mean([f.uncertainty for f in forecasts.values()])
        
        # Создать консенсусное распределение
        # Используем нормальное распределение для простоты
        std_dev = np.std(expected_returns) if len(expected_returns) > 1 else 1.0
        
        p_negative = stats.norm.cdf(0, loc=consensus_return, scale=std_dev)
        p_positive = 1 - p_negative
        p_neutral = 0.05
        
        p_negative = p_negative * (1 - p_neutral)
        p_positive = p_positive * (1 - p_neutral)
        
        x = abs(consensus_return)
        p_lt_neg_x = stats.norm.cdf(-x, loc=consensus_return, scale=std_dev)
        p_gt_x = 1 - stats.norm.cdf(x, loc=consensus_return, scale=std_dev)
        
        consensus_distribution = ForecastDistribution(
            p_return_negative=p_negative,
            p_return_positive=p_positive,
            p_return_neutral=p_neutral,
            expected_return=consensus_return,
            return_std=std_dev,
            p_return_lt_neg_x=p_lt_neg_x,
            p_return_gt_x=p_gt_x,
            tail_probability=0.05,
            distribution_type="normal",
            parameters={"mu": float(consensus_return), "sigma": float(std_dev)}
        )
        
        # Использовать параметры первого прогноза
        first_forecast = next(iter(forecasts.values()))
        
        return ForecastResult(
            symbol=first_forecast.symbol,
            timeframe=first_forecast.timeframe,
            forecast_horizon="consensus",
            distribution=consensus_distribution,
            timestamp=first_forecast.timestamp,
            model_version=first_forecast.model_version,
            uncertainty=consensus_uncertainty
        )
    
    def estimate_MFE_MAE(
        self,
        historical_trades: list[dict],
        current_prediction: float
    ) -> tuple[float, float]:
        """
        Оценить ожидаемые MFE и MAE на основе исторических сделок.
        
        Args:
            historical_trades: Исторические сделки с MFE/MAE
            current_prediction: Текущее предсказание
        
        Returns:
            (expected_MFE, expected_MAE)
        """
        if not historical_trades:
            return 0.0, 0.0
        
        mfe_values = []
        mae_values = []
        
        for trade in historical_trades:
            if "MFE" in trade and trade["MFE"] is not None:
                mfe_values.append(trade["MFE"])
            if "MAE" in trade and trade["MAE"] is not None:
                mae_values.append(trade["MAE"])
        
        if not mfe_values or not mae_values:
            return 0.0, 0.0
        
        expected_MFE = np.mean(mfe_values)
        expected_MAE = np.mean(mae_values)
        
        # Скорректировать на основе текущего предсказания
        alpha = 0.2
        expected_MFE = alpha * current_prediction + (1 - alpha) * expected_MFE
        expected_MAE = alpha * abs(current_prediction) + (1 - alpha) * expected_MAE
        
        return expected_MFE, expected_MAE


# Глобальный экземпляр Probabilistic Forecast Engine
_forecast_engine: ProbabilisticForecastEngine | None = None


def get_forecast_engine() -> ProbabilisticForecastEngine:
    """Получить глобальный Probabilistic Forecast Engine"""
    global _forecast_engine
    if _forecast_engine is None:
        _forecast_engine = ProbabilisticForecastEngine()
    return _forecast_engine


def reset_forecast_engine():
    """Сбросить Probabilistic Forecast Engine (для тестов)"""
    global _forecast_engine
    _forecast_engine = ProbabilisticForecastEngine()
