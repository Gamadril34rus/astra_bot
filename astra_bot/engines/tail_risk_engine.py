"""
ASTRA BOT — Tail Risk Engine

Движок оценки хвостового риска (Master Specification v2, Section 26)

Добавляет:
- VaR (Value at Risk)
- CVaR (Conditional Value at Risk)
- Expected Shortfall
- tail loss
- gap risk
- liquidation risk

Не оптимизировать систему только по Sharpe/PnL.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class VaRResult:
    """Результат расчёта VaR"""
    value: float  # VaR значение
    confidence_level: float  # Уровень доверия (0-1)
    method: str  # Метод расчёта
    time_horizon: str  # Временной горизонт
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "confidence_level": self.confidence_level,
            "method": self.method,
            "time_horizon": self.time_horizon,
        }


@dataclass
class CVaRResult:
    """Результат расчёта CVaR"""
    value: float  # CVaR значение
    confidence_level: float  # Уровень доверия (0-1)
    method: str  # Метод расчёта
    time_horizon: str  # Временной горизонт
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "confidence_level": self.confidence_level,
            "method": self.method,
            "time_horizon": self.time_horizon,
        }


@dataclass
class TailRiskMetrics:
    """Метрики хвостового риска"""
    var_95: float  # VaR 95%
    var_99: float  # VaR 99%
    cvar_95: float  # CVaR 95%
    cvar_99: float  # CVaR 99%
    expected_shortfall: float
    tail_loss: float
    gap_risk: float
    liquidation_risk: float
    
    # Параметры
    confidence_levels: list[float] = field(default_factory=lambda: [0.95, 0.99])
    time_horizon: str = "1d"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "var_95": self.var_95,
            "var_99": self.var_99,
            "cvar_95": self.cvar_95,
            "cvar_99": self.cvar_99,
            "expected_shortfall": self.expected_shortfall,
            "tail_loss": self.tail_loss,
            "gap_risk": self.gap_risk,
            "liquidation_risk": self.liquidation_risk,
            "confidence_levels": self.confidence_levels,
            "time_horizon": self.time_horizon,
        }


@dataclass
class TailRiskResult:
    """Результат оценки хвостового риска"""
    symbol: str
    metrics: TailRiskMetrics
    
    # Дополнительная информация
    historical_returns: list[float] = field(default_factory=list)
    distribution: str = "normal"
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "symbol": self.symbol,
            "metrics": self.metrics.to_dict(),
            "historical_returns": self.historical_returns,
            "distribution": self.distribution,
            "timestamp": self.timestamp.isoformat(),
        }
        return result


class TailRiskEngine:
    """
    Движок оценки хвостового риска.
    
    Рассчитывает различные метрики хвостового риска для оценки
    экстремальных убытков.
    """
    
    def __init__(self):
        # Уровни доверия
        self.confidence_levels = [0.95, 0.99]
        
        # Временные горизонты
        self.time_horizons = ["1h", "1d", "1w"]
        
        # Параметры для расчётов
        self.default_window = 100  # Окно для расчётов
    
    def calculate_var(
        self,
        returns: list[float],
        confidence_level: float = 0.95,
        method: str = "historical"
    ) -> VaRResult:
        """
        Рассчитать VaR (Value at Risk).
        
        Args:
            returns: Исторические доходности
            confidence_level: Уровень доверия
            method: Метод расчёта (historical, parametric, monte_carlo)
        
        Returns:
            VaRResult
        """
        if not returns or len(returns) < 2:
            return VaRResult(
                value=0.0,
                confidence_level=confidence_level,
                method=method,
                time_horizon="1d"
            )
        
        if method == "historical":
            # Исторический метод
            sorted_returns = sorted(returns)
            index = int((1 - confidence_level) * len(sorted_returns))
            index = min(index, len(sorted_returns) - 1)
            var_value = sorted_returns[index]
            
        elif method == "parametric":
            # Параметрический метод (предполагаем нормальное распределение)
            mu = np.mean(returns)
            sigma = np.std(returns)
            var_value = stats.norm.ppf(1 - confidence_level, loc=mu, scale=sigma)
            
        elif method == "monte_carlo":
            # Метод Монте-Карло
            mu = np.mean(returns)
            sigma = np.std(returns)
            
            # Сгенерировать случайные доходности
            np.random.seed(42)
            simulated_returns = np.random.normal(mu, sigma, 10000)
            
            sorted_simulated = sorted(simulated_returns)
            index = int((1 - confidence_level) * len(sorted_simulated))
            var_value = sorted_simulated[index]
        else:
            var_value = 0.0
        
        return VaRResult(
            value=var_value,
            confidence_level=confidence_level,
            method=method,
            time_horizon="1d"
        )
    
    def calculate_cvar(
        self,
        returns: list[float],
        confidence_level: float = 0.95,
        method: str = "historical"
    ) -> CVaRResult:
        """
        Рассчитать CVaR (Conditional Value at Risk).
        
        Args:
            returns: Исторические доходности
            confidence_level: Уровень доверия
            method: Метод расчёта
        
        Returns:
            CVaRResult
        """
        if not returns or len(returns) < 2:
            return CVaRResult(
                value=0.0,
                confidence_level=confidence_level,
                method=method,
                time_horizon="1d"
            )
        
        if method == "historical":
            # Исторический метод
            sorted_returns = sorted(returns)
            threshold_index = int((1 - confidence_level) * len(sorted_returns))
            threshold_index = min(threshold_index, len(sorted_returns) - 1)
            
            # CVaR = среднее всех убытков за порогом VaR
            losses_beyond_var = sorted_returns[:threshold_index + 1]
            cvar_value = np.mean(losses_beyond_var)
            
        elif method == "parametric":
            # Параметрический метод
            mu = np.mean(returns)
            sigma = np.std(returns)
            
            # Для нормального распределения
            # CVaR ≈ mu - sigma * pdf(norm.ppf(1-confidence_level)) / (1-confidence_level)
            z = stats.norm.ppf(1 - confidence_level)
            pdf_z = stats.norm.pdf(z)
            cvar_value = mu - sigma * pdf_z / (1 - confidence_level)
            
        else:
            cvar_value = 0.0
        
        return CVaRResult(
            value=cvar_value,
            confidence_level=confidence_level,
            method=method,
            time_horizon="1d"
        )
    
    def calculate_expected_shortfall(
        self,
        returns: list[float],
        confidence_level: float = 0.95
    ) -> float:
        """
        Рассчитать Expected Shortfall.
        
        Args:
            returns: Исторические доходности
            confidence_level: Уровень доверия
        
        Returns:
            Expected Shortfall
        """
        if not returns:
            return 0.0
        
        # Expected Shortfall = CVaR
        cvar_result = self.calculate_cvar(returns, confidence_level, "historical")
        return cvar_result.value
    
    def calculate_tail_loss(
        self,
        returns: list[float],
        tail_threshold: float = 0.05
    ) -> float:
        """
        Рассчитать Tail Loss (средний убыток в хвосте).
        
        Args:
            returns: Исторические доходности
            tail_threshold: Порог хвоста (доля убытков)
        
        Returns:
            Tail Loss
        """
        if not returns:
            return 0.0
        
        # Отсортировать доходности
        sorted_returns = sorted(returns)
        
        # Найти порог
        threshold_index = int(tail_threshold * len(sorted_returns))
        threshold_index = min(threshold_index, len(sorted_returns) - 1)
        
        # Средний убыток в хвосте
        tail_returns = sorted_returns[:threshold_index + 1]
        tail_loss = np.mean(tail_returns)
        
        return tail_loss
    
    def calculate_gap_risk(
        self,
        prices: list[float],
        time_window: int = 5
    ) -> float:
        """
        Рассчитать Gap Risk (риск разрыва цен).
        
        Args:
            prices: Исторические цены
            time_window: Временное окно
        
        Returns:
            Gap Risk
        """
        if not prices or len(prices) < 2:
            return 0.0
        
        # Рассчитать разницы между соседними ценами
        price_diffs = [abs(prices[i] - prices[i - 1]) / prices[i - 1] 
                      for i in range(1, len(prices))]
        
        # Найти максимальный разрыв
        gap_risk = max(price_diffs) if price_diffs else 0.0
        
        return gap_risk
    
    def calculate_liquidation_risk(
        self,
        position_size: float,
        entry_price: float,
        stop_loss: float,
        volatility: float,
        liquidity: float
    ) -> float:
        """
        Рассчитать Liquidation Risk.
        
        Args:
            position_size: Размер позиции
            entry_price: Цена входа
            stop_loss: Цена стопа
            volatility: Волатильность
            liquidity: Ликвидность
        
        Returns:
            Liquidation Risk
        """
        if volatility <= 0 or liquidity <= 0:
            return 0.0
        
        # Расстояние до стопа
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return 0.0
        
        # Риск ликвидации зависит от:
        # 1. Волатильности (выше волатильность = выше риск)
        # 2. Ликвидности (ниже ликвидность = выше риск)
        # 3. Расстояния до стопа (меньше расстояние = выше риск)
        
        vol_factor = volatility / 0.01  # Нормализовать
        liq_factor = 1 / (liquidity / 1000) if liquidity > 0 else 1
        dist_factor = 0.01 / stop_distance if stop_distance > 0 else 1
        
        # Объединить факторы
        liquidation_risk = min(1.0, (vol_factor + liq_factor + dist_factor) / 3)
        
        return liquidation_risk
    
    def calculate_tail_risk_metrics(
        self,
        returns: list[float],
        prices: list[float] | None = None,
        confidence_levels: list[float] | None = None
    ) -> TailRiskMetrics:
        """
        Рассчитать все метрики хвостового риска.
        
        Args:
            returns: Исторические доходности
            prices: Исторические цены (опционально)
            confidence_levels: Уровни доверия
        
        Returns:
            TailRiskMetrics
        """
        if confidence_levels is None:
            confidence_levels = self.confidence_levels
        
        # Рассчитать VaR
        var_95 = self.calculate_var(returns, 0.95, "historical").value
        var_99 = self.calculate_var(returns, 0.99, "historical").value
        
        # Рассчитать CVaR
        cvar_95 = self.calculate_cvar(returns, 0.95, "historical").value
        cvar_99 = self.calculate_cvar(returns, 0.99, "historical").value
        
        # Рассчитать Expected Shortfall
        expected_shortfall = self.calculate_expected_shortfall(returns, 0.95)
        
        # Рассчитать Tail Loss
        tail_loss = self.calculate_tail_loss(returns, 0.05)
        
        # Рассчитать Gap Risk
        gap_risk = 0.0
        if prices:
            gap_risk = self.calculate_gap_risk(prices)
        
        # Liquidation Risk требует дополнительных данных
        liquidation_risk = 0.0
        
        return TailRiskMetrics(
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            cvar_99=cvar_99,
            expected_shortfall=expected_shortfall,
            tail_loss=tail_loss,
            gap_risk=gap_risk,
            liquidation_risk=liquidation_risk,
            confidence_levels=confidence_levels,
            time_horizon="1d"
        )
    
    def assess_tail_risk(
        self,
        symbol: str,
        returns: list[float],
        prices: list[float] | None = None
    ) -> TailRiskResult:
        """
        Полная оценка хвостового риска.
        
        Args:
            symbol: Символ инструмента
            returns: Исторические доходности
            prices: Исторические цены
        
        Returns:
            TailRiskResult
        """
        metrics = self.calculate_tail_risk_metrics(returns, prices)
        
        # Определить распределение
        distribution = self._detect_distribution(returns)
        
        return TailRiskResult(
            symbol=symbol,
            metrics=metrics,
            historical_returns=returns,
            distribution=distribution
        )
    
    def _detect_distribution(self, returns: list[float]) -> str:
        """
        Обнаружить тип распределения доходностей.
        
        Args:
            returns: Исторические доходности
        
        Returns:
            Тип распределения
        """
        if not returns or len(returns) < 3:
            return "unknown"
        
        # Проверить на нормальность (тест Шапиро-Уилка)
        try:
            from scipy.stats import shapiro
            stat, p_value = shapiro(returns)
            if p_value > 0.05:
                return "normal"
        except Exception:
            pass
        
        # Проверить на асимметрию
        skewness = stats.skew(returns)
        if abs(skewness) > 0.5:
            if skewness > 0:
                return "right_skewed"
            else:
                return "left_skewed"
        
        # Проверить на тяжелые хвосты (тест на эксцесс)
        kurtosis = stats.kurtosis(returns)
        if kurtosis > 0:
            return "fat_tailed"
        
        return "normal"
    
    def compare_tail_risk(
        self,
        symbol1: str,
        returns1: list[float],
        symbol2: str,
        returns2: list[float]
    ) -> dict[str, Any]:
        """
        Сравнить хвостовый риск двух инструментов.
        
        Args:
            symbol1: Первый символ
            returns1: Доходности первого символа
            symbol2: Второй символ
            returns2: Доходности второго символа
        
        Returns:
            Сравнение хвостового риска
        """
        metrics1 = self.calculate_tail_risk_metrics(returns1)
        metrics2 = self.calculate_tail_risk_metrics(returns2)
        
        comparison = {
            "symbol1": symbol1,
            "symbol2": symbol2,
            "metrics1": metrics1.to_dict(),
            "metrics2": metrics2.to_dict(),
            "comparison": {
                "var_95_ratio": metrics1.var_95 / metrics2.var_95 if metrics2.var_95 != 0 else float('inf'),
                "var_99_ratio": metrics1.var_99 / metrics2.var_99 if metrics2.var_99 != 0 else float('inf'),
                "cvar_95_ratio": metrics1.cvar_95 / metrics2.cvar_95 if metrics2.cvar_95 != 0 else float('inf'),
                "expected_shortfall_ratio": metrics1.expected_shortfall / metrics2.expected_shortfall if metrics2.expected_shortfall != 0 else float('inf'),
            }
        }
        
        return comparison


# Глобальный экземпляр Tail Risk Engine
_tail_risk_engine: TailRiskEngine | None = None


def get_tail_risk_engine() -> TailRiskEngine:
    """Получить глобальный Tail Risk Engine"""
    global _tail_risk_engine
    if _tail_risk_engine is None:
        _tail_risk_engine = TailRiskEngine()
    return _tail_risk_engine


def reset_tail_risk_engine():
    """Сбросить Tail Risk Engine (для тестов)"""
    global _tail_risk_engine
    _tail_risk_engine = TailRiskEngine()
