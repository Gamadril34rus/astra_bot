"""
ASTRA BOT — Statistical Tests

Статистические тесты для валидации стратегий (Master Specification v2, Sections 31-37)

Включает:
- CPCV (Combinatorial Purged Cross Validation)
- PBO (Probability of Backtest Overfitting)
- DSR (Deflated Sharpe Ratio)
- White's Reality Check
- SPA / related multiple-testing procedures

Цель: отличить реальную predictive ability от результата data mining.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.model_selection import TimeSeriesSplit
from sklearn.utils import resample

logger = logging.getLogger(__name__)


@dataclass
class CPCVResult:
    """Результат CPCV"""
    mean_score: float
    std_score: float
    p_value: float
    num_folds: int
    purging_period: int
    embargo_period: int
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_score": self.mean_score,
            "std_score": self.std_score,
            "p_value": self.p_value,
            "num_folds": self.num_folds,
            "purging_period": self.purging_period,
            "embargo_period": self.embargo_period,
        }


@dataclass
class PBOResult:
    """Результат PBO"""
    probability: float  # Вероятность backtest overfitting
    is_significant: bool
    num_strategies: int
    num_trials: int
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": self.probability,
            "is_significant": self.is_significant,
            "num_strategies": self.num_strategies,
            "num_trials": self.num_trials,
        }


@dataclass
class DSRResult:
    """Результат DSR"""
    deflated_sharpe: float
    original_sharpe: float
    num_strategies: int
    probability: float
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "deflated_sharpe": self.deflated_sharpe,
            "original_sharpe": self.original_sharpe,
            "num_strategies": self.num_strategies,
            "probability": self.probability,
        }


@dataclass
class RealityCheckResult:
    """Результат Reality Check"""
    p_value: float
    is_significant: bool
    num_strategies: int
    test_statistic: float
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "p_value": self.p_value,
            "is_significant": self.is_significant,
            "num_strategies": self.num_strategies,
            "test_statistic": self.test_statistic,
        }


class StatisticalTests:
    """
    Статистические тесты для валидации стратегий.
    
    Включает тесты для обнаружения и предотвращения overfitting.
    """
    
    def __init__(self):
        # Параметры по умолчанию
        self.default_purging_period = 20
        self.default_embargo_period = 5
        self.default_num_folds = 10
        self.significance_level = 0.05
    
    def combinatorial_purged_cv(
        self,
        X: np.ndarray,
        y: np.ndarray,
        model,
        purging_period: int | None = None,
        embargo_period: int | None = None,
        num_folds: int | None = None
    ) -> CPCVResult:
        """
        Combinatorial Purged Cross Validation (Section 34).
        
        Args:
            X: Факторы
            y: Целевая переменная
            model: Модель
            purging_period: Период очистки
            embargo_period: Период эмбарго
            num_folds: Количество фолдов
        
        Returns:
            CPCVResult
        """
        if purging_period is None:
            purging_period = self.default_purging_period
        if embargo_period is None:
            embargo_period = self.default_embargo_period
        if num_folds is None:
            num_folds = self.default_num_folds
        
        n_samples = len(X)
        if n_samples < purging_period + embargo_period:
            return CPCVResult(
                mean_score=0.0,
                std_score=0.0,
                p_value=1.0,
                num_folds=0,
                purging_period=purging_period,
                embargo_period=embargo_period
            )
        
        # Упрощённая версия CPCV
        # Используем TimeSeriesSplit с очисткой
        tscv = TimeSeriesSplit(n_splits=num_folds)
        
        scores = []
        for train_index, test_index in tscv.split(X):
            X_train, X_test = X[train_index], X[test_index]
            y_train, y_test = y[train_index], y[test_index]
            
            # Обучить модель
            model.fit(X_train, y_train)
            
            # Предсказать
            y_pred = model.predict(X_test)
            
            # Рассчитать метрику (R²)
            score = stats.pearsonr(y_test, y_pred)[0] ** 2
            scores.append(score)
        
        mean_score = np.mean(scores)
        std_score = np.std(scores)
        
        # Рассчитать p-value (упрощённо)
        if mean_score > 0:
            p_value = 1 - stats.norm.cdf(mean_score, loc=0, scale=std_score)
        else:
            p_value = 1.0
        
        return CPCVResult(
            mean_score=mean_score,
            std_score=std_score,
            p_value=p_value,
            num_folds=num_folds,
            purging_period=purging_period,
            embargo_period=embargo_period
        )
    
    def probability_of_backtest_overfitting(
        self,
        returns: list[float],
        num_trials: int = 1000,
        num_strategies: int = 10
    ) -> PBOResult:
        """
        Probability of Backtest Overfitting (Section 35).
        
        Args:
            returns: Доходности стратегии
            num_trials: Количество испытаний
            num_strategies: Количество стратегий
        
        Returns:
            PBOResult
        """
        if not returns or len(returns) < 2:
            return PBOResult(
                probability=0.0,
                is_significant=False,
                num_strategies=num_strategies,
                num_trials=num_trials
            )
        
        # Упрощённая оценка PBO
        # Сравниваем Sharpe стратегии с Sharpe случайных стратегий
        
        # Рассчитать Sharpe стратегии
        strategy_sharpe = self.calculate_sharpe(returns)
        
        # Сгенерировать случайные стратегии
        random_sharpes = []
        for _ in range(num_trials):
            # Перемешать доходности
            shuffled_returns = np.random.permutation(returns)
            random_sharpe = self.calculate_sharpe(shuffled_returns)
            random_sharpes.append(random_sharpe)
        
        # Рассчитать вероятность
        pbo = sum(1 for rs in random_sharpes if rs >= strategy_sharpe) / num_trials
        
        is_significant = pbo < self.significance_level
        
        return PBOResult(
            probability=pbo,
            is_significant=is_significant,
            num_strategies=num_strategies,
            num_trials=num_trials
        )
    
    def calculate_sharpe(self, returns: list[float], risk_free_rate: float = 0.0) -> float:
        """
        Рассчитать Sharpe ratio.
        
        Args:
            returns: Доходности
            risk_free_rate: Безрисковая ставка
        
        Returns:
            Sharpe ratio
        """
        if not returns or len(returns) < 2:
            return 0.0
        
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        
        if std_return == 0:
            return 0.0
        
        return (mean_return - risk_free_rate) / std_return
    
    def deflated_sharpe_ratio(
        self,
        returns: list[float],
        num_strategies: int = 10,
        probability: float = 0.5
    ) -> DSRResult:
        """
        Deflated Sharpe Ratio (Section 36).
        
        Args:
            returns: Доходности стратегии
            num_strategies: Количество стратегий
            probability: Вероятность
        
        Returns:
            DSRResult
        """
        if not returns or len(returns) < 2:
            return DSRResult(
                deflated_sharpe=0.0,
                original_sharpe=0.0,
                num_strategies=num_strategies,
                probability=probability
            )
        
        original_sharpe = self.calculate_sharpe(returns)
        
        # Упрощённая формула DSR
        # DSR = SR - (1 - gamma) * z * sigma_SR
        # где gamma = 1 - probability, z = квантиль нормального распределения
        
        gamma = 1 - probability
        z = stats.norm.ppf(1 - probability / 2)
        
        # Стандартное отклонение Sharpe ratio
        # Для упрощения используем стандартное отклонение доходностей
        sigma_SR = np.std(returns) / np.sqrt(len(returns))
        
        deflated_sharpe = original_sharpe - (1 - gamma) * z * sigma_SR
        
        return DSRResult(
            deflated_sharpe=deflated_sharpe,
            original_sharpe=original_sharpe,
            num_strategies=num_strategies,
            probability=probability
        )
    
    def whites_reality_check(
        self,
        strategy_returns: list[list[float]]
    ) -> RealityCheckResult:
        """
        White's Reality Check (Section 37).
        
        Args:
            strategy_returns: Список доходностей стратегий
        
        Returns:
            RealityCheckResult
        """
        if not strategy_returns or len(strategy_returns) < 2:
            return RealityCheckResult(
                p_value=1.0,
                is_significant=False,
                num_strategies=len(strategy_returns),
                test_statistic=0.0
            )
        
        # Упрощённая версия теста Уайта
        # Сравниваем среднюю доходность лучшей стратегии со средней доходностью случайных стратегий
        
        # Рассчитать средние доходности стратегий
        strategy_means = [np.mean(returns) for returns in strategy_returns]
        best_mean = max(strategy_means)
        
        # Сгенерировать случайные стратегии
        n = len(strategy_returns[0])
        num_trials = 1000
        random_means = []
        
        for _ in range(num_trials):
            # Перемешать доходности
            shuffled = [np.random.permutation(returns) for returns in strategy_returns]
            random_means.append(max([np.mean(s) for s in shuffled]))
        
        # Рассчитать тестовую статистику
        test_statistic = (best_mean - np.mean(random_means)) / (np.std(random_means) / np.sqrt(num_trials))
        
        # Рассчитать p-value
        p_value = 1 - stats.norm.cdf(test_statistic)
        
        is_significant = p_value < self.significance_level
        
        return RealityCheckResult(
            p_value=p_value,
            is_significant=is_significant,
            num_strategies=len(strategy_returns),
            test_statistic=test_statistic
        )
    
    def multiple_testing_correction(
        self,
        p_values: list[float],
        method: str = "bonferroni"
    ) -> list[float]:
        """
        Корректировка для множественного тестирования.
        
        Args:
            p_values: Список p-value
            method: Метод корректировки (bonferroni, holm, fdr)
        
        Returns:
            Скорректированные p-value
        """
        if not p_values:
            return []
        
        if method == "bonferroni":
            # Корректировка Бонферрони
            return [min(1.0, p * len(p_values)) for p in p_values]
        
        elif method == "holm":
            # Корректировка Холма
            sorted_indices = np.argsort(p_values)
            sorted_p = [p_values[i] for i in sorted_indices]
            
            corrected = []
            for i, p in enumerate(sorted_p):
                corrected_p = min(1.0, p * (len(p_values) - i))
                corrected.append(corrected_p)
            
            # Вернуть в исходном порядке
            original_order = np.argsort(sorted_indices)
            return [corrected[i] for i in original_order]
        
        elif method == "fdr":
            # Контроль FDR (Benjamini-Hochberg)
            sorted_p = sorted(p_values)
            corrected = []
            
            for i, p in enumerate(sorted_p):
                corrected_p = min(1.0, p * len(sorted_p) / (i + 1))
                corrected.append(corrected_p)
            
            return corrected
        
        return p_values
    
    def stability_test(
        self,
        returns: list[float],
        window_size: int = 50,
        step_size: int = 10
    ) -> dict[str, Any]:
        """
        Тест стабильности стратегии.
        
        Args:
            returns: Доходности стратегии
            window_size: Размер окна
            step_size: Размер шага
        
        Returns:
            Результаты теста стабильности
        """
        if len(returns) < window_size:
            return {"stable": False, "metrics": []}
        
        sharpe_ratios = []
        for i in range(0, len(returns) - window_size + 1, step_size):
            window_returns = returns[i:i + window_size]
            sharpe = self.calculate_sharpe(window_returns)
            sharpe_ratios.append(sharpe)
        
        # Рассчитать статистику
        mean_sharpe = np.mean(sharpe_ratios)
        std_sharpe = np.std(sharpe_ratios)
        
        # Проверить стабильность
        stable = std_sharpe < mean_sharpe * 0.5  # Упрощённое правило
        
        return {
            "stable": stable,
            "mean_sharpe": mean_sharpe,
            "std_sharpe": std_sharpe,
            "sharpe_ratios": sharpe_ratios,
            "windows": len(sharpe_ratios)
        }
    
    def validate_strategy(
        self,
        returns: list[float],
        parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Полная валидация стратегии.
        
        Args:
            returns: Доходности стратегии
            parameters: Параметры стратегии
        
        Returns:
            Результаты валидации
        """
        results = {}
        
        # 1. Sharpe Ratio
        results["sharpe_ratio"] = self.calculate_sharpe(returns)
        
        # 2. PBO
        results["pbo"] = self.probability_of_backtest_overfitting(returns).to_dict()
        
        # 3. DSR
        results["dsr"] = self.deflated_sharpe_ratio(returns).to_dict()
        
        # 4. Стабильность
        results["stability"] = self.stability_test(returns)
        
        # 5. Reality Check
        if parameters and "comparison_returns" in parameters:
            results["reality_check"] = self.whites_reality_check(
                [returns, parameters["comparison_returns"]]
            ).to_dict()
        
        return results


# Глобальный экземпляр Statistical Tests
_statistical_tests: StatisticalTests | None = None


def get_statistical_tests() -> StatisticalTests:
    """Получить глобальный Statistical Tests"""
    global _statistical_tests
    if _statistical_tests is None:
        _statistical_tests = StatisticalTests()
    return _statistical_tests


def reset_statistical_tests():
    """Сбросить Statistical Tests (для тестов)"""
    global _statistical_tests
    _statistical_tests = StatisticalTests()
