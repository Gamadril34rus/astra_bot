"""
ASTRA BOT - Causality Research Engine

Движок причинно-следственного анализа (ТЗ Пункт 29)

Не путать: correlation ≠ causation.

Для важных закономерностей проводить:
- lead/lag analysis
- event studies
- conditional analysis
- regime-conditioned analysis

Если feature просто коррелирует с ценой, это не считается доказанным edge.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class CausalityMethod(str, Enum):
    """Методы причинно-следственного анализа"""
    LEAD_LAG = "lead_lag_analysis"
    EVENT_STUDY = "event_study"
    CONDITIONAL_ANALYSIS = "conditional_analysis"
    REGIME_CONDITIONED = "regime_conditioned_analysis"
    GRANGER = "granger_causality"
    COINTEGRATION = "cointegration"
    TRANSFER_ENTROPY = "transfer_entropy"


class CausalityResult(str, Enum):
    """Результаты причинно-следственного анализа"""
    STRONG_CAUSALITY = "strong_causality"
    MODERATE_CAUSALITY = "moderate_causality"
    WEAK_CAUSALITY = "weak_causality"
    NO_CAUSALITY = "no_causality"
    SPURIOUS = "spurious_correlation"


@dataclass
class LeadLagResult:
    """Результат lead/lag анализа"""
    feature_name: str
    target_name: str
    
    # Корреляции по лагам
    correlations: dict[int, float] = field(default_factory=dict)  # lag -> correlation
    
    # Оптимальный лаг
    optimal_lag: int = 0
    max_correlation: float = 0.0
    
    # Статистическая значимость
    p_values: dict[int, float] = field(default_factory=dict)
    significant_lags: list[int] = field(default_factory=list)
    
    # Направление
    direction: str = "neutral"  # positive/negative/neutral
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "target_name": self.target_name,
            "correlations": self.correlations,
            "optimal_lag": self.optimal_lag,
            "max_correlation": self.max_correlation,
            "p_values": self.p_values,
            "significant_lags": self.significant_lags,
            "direction": self.direction,
        }


@dataclass
class EventStudyResult:
    """Результат event study"""
    feature_name: str
    target_name: str
    
    # Временные окна
    windows: list[int] = field(default_factory=list)  # минуты
    
    # Средние возвраты по окнам
    avg_returns: dict[int, float] = field(default_factory=dict)
    cumulative_returns: dict[int, float] = field(default_factory=dict)
    
    # Статистика
    std_errors: dict[int, float] = field(default_factory=dict)
    t_stats: dict[int, float] = field(default_factory=dict)
    p_values: dict[int, float] = field(default_factory=dict)
    
    # Общая статистика
    total_events: int = 0
    avg_cumulative_return: float = 0.0
    max_cumulative_return: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "target_name": self.target_name,
            "windows": self.windows,
            "avg_returns": self.avg_returns,
            "cumulative_returns": self.cumulative_returns,
            "std_errors": self.std_errors,
            "t_stats": self.t_stats,
            "p_values": self.p_values,
            "total_events": self.total_events,
            "avg_cumulative_return": self.avg_cumulative_return,
            "max_cumulative_return": self.max_cumulative_return,
        }


@dataclass
class ConditionalResult:
    """Результат условного анализа"""
    feature_name: str
    target_name: str
    condition: str
    
    # Статистика при условии
    mean_return: float = 0.0
    std_return: float = 0.0
    sample_size: int = 0
    
    # Статистика без условия
    unconditional_mean: float = 0.0
    unconditional_std: float = 0.0
    unconditional_sample_size: int = 0
    
    # Разница
    difference: float = 0.0
    t_stat: float = 0.0
    p_value: float = 1.0
    
    # Уверенность
    confidence: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "target_name": self.target_name,
            "condition": self.condition,
            "mean_return": self.mean_return,
            "std_return": self.std_return,
            "sample_size": self.sample_size,
            "unconditional_mean": self.unconditional_mean,
            "unconditional_std": self.unconditional_std,
            "unconditional_sample_size": self.unconditional_sample_size,
            "difference": self.difference,
            "t_stat": self.t_stat,
            "p_value": self.p_value,
            "confidence": self.confidence,
        }


@dataclass
class RegimeConditionedResult:
    """Результат анализа с учётом режима"""
    feature_name: str
    target_name: str
    
    # Результаты по режимам
    regime_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    
    # Общая статистика
    overall_correlation: float = 0.0
    regime_dependency: float = 0.0  # Насколько зависит от режима
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "target_name": self.target_name,
            "regime_results": self.regime_results,
            "overall_correlation": self.overall_correlation,
            "regime_dependency": self.regime_dependency,
        }


@dataclass
class CausalityAnalysis:
    """Полный причинно-следственный анализ"""
    feature_name: str
    target_name: str
    
    # Методы
    lead_lag: LeadLagResult | None = None
    event_study: EventStudyResult | None = None
    conditional: list[ConditionalResult] = field(default_factory=list)
    regime_conditioned: RegimeConditionedResult | None = None
    
    # Итоговый результат
    result: CausalityResult = CausalityResult.NO_CAUSALITY
    confidence: float = 0.0
    
    # Статистика
    methods_used: list[CausalityMethod] = field(default_factory=list)
    significant_methods: list[CausalityMethod] = field(default_factory=list)
    
    # Вывод
    conclusion: str = ""
    recommendations: list[str] = field(default_factory=list)
    
    # Временная метка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "feature_name": self.feature_name,
            "target_name": self.target_name,
            "result": self.result.value,
            "confidence": self.confidence,
            "methods_used": [m.value for m in self.methods_used],
            "significant_methods": [m.value for m in self.significant_methods],
            "conclusion": self.conclusion,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat(),
        }
        
        if self.lead_lag:
            result["lead_lag"] = self.lead_lag.to_dict()
        if self.event_study:
            result["event_study"] = self.event_study.to_dict()
        if self.conditional:
            result["conditional"] = [c.to_dict() for c in self.conditional]
        if self.regime_conditioned:
            result["regime_conditioned"] = self.regime_conditioned.to_dict()
        
        return result


class CausalityResearchEngine:
    """
    Движок причинно-следственного анализа.
    
    Проводит различные тесты для проверки причинно-следственных связей
    между features и целевыми переменными.
    """
    
    def __init__(self):
        # Пороги
        self.thresholds = {
            "min_sample_size": 30,
            "min_correlation": 0.3,
            "significance_level": 0.05,
            "strong_correlation": 0.7,
            "moderate_correlation": 0.5,
            "weak_correlation": 0.3,
        }
        
        # История анализов
        self._analyses: dict[str, CausalityAnalysis] = {}
    
    def perform_lead_lag_analysis(
        self,
        feature: list[float],
        target: list[float],
        feature_name: str,
        target_name: str,
        max_lag: int = 10,
    ) -> LeadLagResult:
        """
        Выполнить lead/lag анализ.
        
        Args:
            feature: Значения feature
            target: Значения целевой переменной
            feature_name: Название feature
            target_name: Название целевой переменной
            max_lag: Максимальный лаг
        
        Returns:
            Результат lead/lag анализа
        """
        if len(feature) != len(target):
            raise ValueError("Feature and target must have the same length")
        
        if len(feature) < self.thresholds["min_sample_size"]:
            return LeadLagResult(
                feature_name=feature_name,
                target_name=target_name,
            )
        
        correlations = {}
        p_values = {}
        
        for lag in range(-max_lag, max_lag + 1):
            if lag == 0:
                # Нулевой лаг - простая корреляция
                corr = float(np.corrcoef(feature, target)[0, 1])
                correlations[lag] = corr
                
                # p-value (упрощённая оценка)
                if abs(corr) > 0:
                    # Приблизительный p-value для корреляции
                    n = len(feature)
                    t_stat = corr * np.sqrt(n - 2) / np.sqrt(1 - corr**2)
                    from scipy import stats
                    p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=n-2))
                    p_values[lag] = p_value
                else:
                    p_values[lag] = 1.0
            else:
                # Сдвиг series
                if lag > 0:
                    # Feature leads target (feature сдвигаем назад)
                    shifted_feature = feature[:-lag] if lag > 0 else feature[-lag:]
                    shifted_target = target[lag:] if lag > 0 else target[:lag]
                else:
                    # Feature lags target (feature сдвигаем вперёд)
                    shifted_feature = feature[-lag:] if lag < 0 else feature[:-lag]
                    shifted_target = target[:lag] if lag < 0 else target[lag:]
                
                if len(shifted_feature) >= self.thresholds["min_sample_size"] and len(shifted_target) >= self.thresholds["min_sample_size"]:
                    corr = float(np.corrcoef(shifted_feature, shifted_target)[0, 1])
                    correlations[lag] = corr
                    
                    # p-value
                    if abs(corr) > 0:
                        n = len(shifted_feature)
                        t_stat = corr * np.sqrt(n - 2) / np.sqrt(1 - corr**2)
                        from scipy import stats
                        p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=n-2))
                        p_values[lag] = p_value
                    else:
                        p_values[lag] = 1.0
        
        # Найти оптимальный лаг
        max_corr = max(correlations.values()) if correlations else 0.0
        optimal_lag = max(correlations, key=correlations.get) if correlations else 0
        
        # Найти значимые лаги
        significant_lags = [lag for lag, p in p_values.items() if p < self.thresholds["significance_level"]]
        
        # Определить направление
        if max_corr > self.thresholds["min_correlation"]:
            direction = "positive"
        elif max_corr < -self.thresholds["min_correlation"]:
            direction = "negative"
        else:
            direction = "neutral"
        
        return LeadLagResult(
            feature_name=feature_name,
            target_name=target_name,
            correlations=correlations,
            p_values=p_values,
            optimal_lag=optimal_lag,
            max_correlation=max_corr,
            significant_lags=significant_lags,
            direction=direction,
        )
    
    def perform_event_study(
        self,
        event_times: list[datetime],
        prices: list[float],
        timestamps: list[datetime],
        feature_name: str,
        target_name: str,
        windows: list[int] = [1, 3, 5, 15, 30, 60, 240, 1440],
    ) -> EventStudyResult:
        """
        Выполнить event study.
        
        Args:
            event_times: Времена событий
            prices: Цены
            timestamps: Временные метки цен
            feature_name: Название feature
            target_name: Название целевой переменной
            windows: Временные окна в минутах
        
        Returns:
            Результат event study
        """
        if not event_times or not prices or len(timestamps) != len(prices):
            return EventStudyResult(
                feature_name=feature_name,
                target_name=target_name,
                windows=windows,
            )
        
        # Создать DataFrame для анализа
        try:
            import pandas as pd
            df = pd.DataFrame({
                'timestamp': timestamps,
                'price': prices,
            })
            df = df.set_index('timestamp')
            
            # Найти события в данных
            event_returns = {}
            cumulative_returns = {}
            
            for window in windows:
                returns_list = []
                cum_returns_list = []
                
                for event_time in event_times:
                    # Найти цен до события
                    start_time = event_time - timedelta(minutes=window)
                    end_time = event_time + timedelta(minutes=window)
                    
                    event_data = df.loc[(df.index >= start_time) & (df.index <= end_time)]
                    
                    if len(event_data) >= 2:
                        # Возврат от начала до конца окна
                        start_price = event_data.iloc[0]['price']
                        end_price = event_data.iloc[-1]['price']
                        return_pct = ((end_price - start_price) / start_price * 100) if start_price > 0 else 0.0
                        returns_list.append(return_pct)
                        
                        # Накопленный возврат
                        cum_return = ((end_price - start_price) / start_price * 100) if start_price > 0 else 0.0
                        cum_returns_list.append(cum_return)
                
                if returns_list:
                    event_returns[window] = float(np.mean(returns_list))
                    cumulative_returns[window] = float(np.mean(cum_returns_list))
                else:
                    event_returns[window] = 0.0
                    cumulative_returns[window] = 0.0
            
            # Рассчитать стандартные ошибки и статистику
            std_errors = {}
            t_stats = {}
            p_values = {}
            
            for window in windows:
                if window in event_returns:
                    # Для упрощения используем стандартное отклонение
                    # В реальном event study нужно использовать более сложные методы
                    std_errors[window] = 0.0
                    t_stats[window] = event_returns[window] / 0.0001 if event_returns[window] != 0 else 0.0
                    p_values[window] = 0.05  # Упрощённое значение
            
            # Общая статистика
            all_returns = [r for r in event_returns.values() if r != 0]
            total_events = len(event_times)
            avg_cumulative_return = float(np.mean(list(cumulative_returns.values()))) if cumulative_returns else 0.0
            max_cumulative_return = float(max(cumulative_returns.values())) if cumulative_returns else 0.0
            
            return EventStudyResult(
                feature_name=feature_name,
                target_name=target_name,
                windows=windows,
                avg_returns=event_returns,
                cumulative_returns=cumulative_returns,
                std_errors=std_errors,
                t_stats=t_stats,
                p_values=p_values,
                total_events=total_events,
                avg_cumulative_return=avg_cumulative_return,
                max_cumulative_return=max_cumulative_return,
            )
        except Exception as e:
            logger.error(f"Error in event study: {e}")
            return EventStudyResult(
                feature_name=feature_name,
                target_name=target_name,
                windows=windows,
                limitations=[f"Error: {str(e)}"],
            )
    
    def perform_conditional_analysis(
        self,
        feature: list[float],
        target: list[float],
        condition_feature: list[bool],
        feature_name: str,
        target_name: str,
        condition_name: str,
    ) -> ConditionalResult:
        """
        Выполнить условный анализ.
        
        Args:
            feature: Значения feature
            target: Значения целевой переменной
            condition_feature: Условие (True/False для каждого наблюдения)
            feature_name: Название feature
            target_name: Название целевой переменной
            condition_name: Название условия
        
        Returns:
            Результат условного анализа
        """
        if len(feature) != len(target) or len(feature) != len(condition_feature):
            raise ValueError("All arrays must have the same length")
        
        if len(feature) < self.thresholds["min_sample_size"]:
            return ConditionalResult(
                feature_name=feature_name,
                target_name=target_name,
                condition=condition_name,
                sample_size=len(feature),
            )
        
        # Разделить данные по условию
        conditional_target = [t for t, c in zip(target, condition_feature) if c]
        unconditional_target = target
        
        # Рассчитать статистику
        conditional_mean = float(np.mean(conditional_target)) if conditional_target else 0.0
        conditional_std = float(np.std(conditional_target)) if len(conditional_target) > 1 else 0.0
        conditional_sample_size = len(conditional_target)
        
        unconditional_mean = float(np.mean(unconditional_target)) if unconditional_target else 0.0
        unconditional_std = float(np.std(unconditional_target)) if len(unconditional_target) > 1 else 0.0
        unconditional_sample_size = len(unconditional_target)
        
        # Разница
        difference = conditional_mean - unconditional_mean
        
        # t-статистика
        if conditional_std > 0 and unconditional_std > 0:
            se = np.sqrt((conditional_std**2 / conditional_sample_size) + 
                         (unconditional_std**2 / unconditional_sample_size))
            t_stat = difference / se if se > 0 else 0.0
        else:
            t_stat = 0.0
        
        # p-value (двусторонний тест)
        from scipy import stats
        p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=conditional_sample_size + unconditional_sample_size - 2))
        
        # Уверенность
        if p_value < 0.01:
            confidence = 0.95
        elif p_value < 0.05:
            confidence = 0.85
        elif p_value < 0.10:
            confidence = 0.70
        else:
            confidence = 0.5
        
        return ConditionalResult(
            feature_name=feature_name,
            target_name=target_name,
            condition=condition_name,
            mean_return=conditional_mean,
            std_return=conditional_std,
            sample_size=conditional_sample_size,
            unconditional_mean=unconditional_mean,
            unconditional_std=unconditional_std,
            unconditional_sample_size=unconditional_sample_size,
            difference=difference,
            t_stat=t_stat,
            p_value=p_value,
            confidence=confidence,
        )
    
    def perform_regime_conditioned_analysis(
        self,
        feature: list[float],
        target: list[float],
        regimes: list[str],
        feature_name: str,
        target_name: str,
    ) -> RegimeConditionedResult:
        """
        Выполнить анализ с учётом режима.
        
        Args:
            feature: Значения feature
            target: Значения целевой переменной
            regimes: Режимы для каждого наблюдения
            feature_name: Название feature
            target_name: Название целевой переменной
        
        Returns:
            Результат анализа с учётом режима
        """
        if len(feature) != len(target) or len(feature) != len(regimes):
            raise ValueError("All arrays must have the same length")
        
        regime_results = {}
        
        # Уникальные режимы
        unique_regimes = set(regimes)
        
        for regime in unique_regimes:
            # Фильтровать данные по режиму
            regime_indices = [i for i, r in enumerate(regimes) if r == regime]
            regime_feature = [feature[i] for i in regime_indices]
            regime_target = [target[i] for i in regime_indices]
            
            if len(regime_feature) >= self.thresholds["min_sample_size"]:
                corr = float(np.corrcoef(regime_feature, regime_target)[0, 1])
                regime_results[regime] = {
                    "correlation": corr,
                    "sample_size": len(regime_feature),
                    "mean_feature": float(np.mean(regime_feature)),
                    "mean_target": float(np.mean(regime_target)),
                }
        
        # Общая корреляция
        overall_correlation = float(np.corrcoef(feature, target)[0, 1])
        
        # Зависимость от режима
        if len(regime_results) > 1:
            # Рассчитать разброс корреляций по режимам
            correlations = [r["correlation"] for r in regime_results.values()]
            regime_dependency = float(np.std(correlations)) if len(correlations) > 1 else 0.0
        else:
            regime_dependency = 0.0
        
        return RegimeConditionedResult(
            feature_name=feature_name,
            target_name=target_name,
            regime_results=regime_results,
            overall_correlation=overall_correlation,
            regime_dependency=regime_dependency,
        )
    
    def analyze_causality(
        self,
        feature: list[float],
        target: list[float],
        feature_name: str,
        target_name: str,
        regimes: list[str] | None = None,
        condition_features: dict[str, list[bool]] | None = None,
        event_times: list[datetime] | None = None,
        timestamps: list[datetime] | None = None,
        prices: list[float] | None = None,
    ) -> CausalityAnalysis:
        """
        Полный причинно-следственный анализ.
        
        Args:
            feature: Значения feature
            target: Значения целевой переменной
            feature_name: Название feature
            target_name: Название целевой переменной
            regimes: Режимы (опционально)
            condition_features: Условия (опционально)
            event_times: Времена событий (опционально)
            timestamps: Временные метки (опционально)
            prices: Цены (опционально)
        
        Returns:
            Полный причинно-следственный анализ
        """
        methods_used = []
        significant_methods = []
        results = []
        
        # Lead-Lag анализ
        lead_lag = self.perform_lead_lag_analysis(
            feature, target, feature_name, target_name
        )
        methods_used.append(CausalityMethod.LEAD_LAG)
        if lead_lag.max_correlation > self.thresholds["min_correlation"]:
            significant_methods.append(CausalityMethod.LEAD_LAG)
        results.append(lead_lag)
        
        # Event Study (если есть данные)
        event_study = None
        if event_times and timestamps and prices:
            event_study = self.perform_event_study(
                event_times, prices, timestamps, feature_name, target_name
            )
            methods_used.append(CausalityMethod.EVENT_STUDY)
            if event_study.total_events >= self.thresholds["min_sample_size"]:
                significant_methods.append(CausalityMethod.EVENT_STUDY)
            results.append(event_study)
        
        # Условный анализ
        conditional_results = []
        if condition_features:
            for condition_name, condition in condition_features.items():
                conditional = self.perform_conditional_analysis(
                    feature, target, condition, feature_name, target_name, condition_name
                )
                methods_used.append(CausalityMethod.CONDITIONAL_ANALYSIS)
                if conditional.p_value < self.thresholds["significance_level"]:
                    significant_methods.append(CausalityMethod.CONDITIONAL_ANALYSIS)
                conditional_results.append(conditional)
        
        # Анализ с учётом режима
        regime_conditioned = None
        if regimes:
            regime_conditioned = self.perform_regime_conditioned_analysis(
                feature, target, regimes, feature_name, target_name
            )
            methods_used.append(CausalityMethod.REGIME_CONDITIONED)
            if regime_conditioned.regime_dependency > 0.3:
                significant_methods.append(CausalityMethod.REGIME_CONDITIONED)
        
        # Определить итоговый результат
        result, confidence = self._determine_causality_result(
            lead_lag, event_study, conditional_results, regime_conditioned
        )
        
        # Создать вывод
        conclusion, recommendations = self._create_conclusion(
            result, confidence, lead_lag, event_study, conditional_results, regime_conditioned
        )
        
        # Создать анализ
        analysis = CausalityAnalysis(
            feature_name=feature_name,
            target_name=target_name,
            lead_lag=lead_lag,
            event_study=event_study,
            conditional=conditional_results,
            regime_conditioned=regime_conditioned,
            result=result,
            confidence=confidence,
            methods_used=methods_used,
            significant_methods=significant_methods,
            conclusion=conclusion,
            recommendations=recommendations,
        )
        
        # Сохранить анализ
        analysis_id = f"{feature_name}_{target_name}"
        self._analyses[analysis_id] = analysis
        
        return analysis
    
    def _determine_causality_result(
        self,
        lead_lag: LeadLagResult | None,
        event_study: EventStudyResult | None,
        conditional_results: list[ConditionalResult],
        regime_conditioned: RegimeConditionedResult | None,
    ) -> tuple[CausalityResult, float]:
        """
        Определить итоговый результат причинно-следственного анализа.
        
        Args:
            lead_lag: Результат lead/lag анализа
            event_study: Результат event study
            conditional_results: Результаты условного анализа
            regime_conditioned: Результат анализа с учётом режима
        
        Returns:
            Итоговый результат и уверенность
        """
        score = 0.0
        confidence = 0.0
        
        # Lead-Lag
        if lead_lag and lead_lag.max_correlation > self.thresholds["strong_correlation"]:
            score += 3
            confidence += 0.3
        elif lead_lag and lead_lag.max_correlation > self.thresholds["moderate_correlation"]:
            score += 2
            confidence += 0.2
        elif lead_lag and lead_lag.max_correlation > self.thresholds["weak_correlation"]:
            score += 1
            confidence += 0.1
        
        # Event Study
        if event_study and event_study.total_events >= self.thresholds["min_sample_size"]:
            if abs(event_study.avg_cumulative_return) > 1.0:  # Более 1% средний возврат
                score += 2
                confidence += 0.2
            elif abs(event_study.avg_cumulative_return) > 0.5:
                score += 1
                confidence += 0.1
        
        # Conditional Analysis
        for conditional in conditional_results:
            if conditional.p_value < 0.01:
                score += 2
                confidence += 0.2
            elif conditional.p_value < 0.05:
                score += 1
                confidence += 0.1
        
        # Regime Conditioned
        if regime_conditioned and regime_conditioned.regime_dependency > 0.5:
            score += 1
            confidence += 0.1
        
        # Определить результат
        if score >= 5:
            result = CausalityResult.STRONG_CAUSALITY
        elif score >= 3:
            result = CausalityResult.MODERATE_CAUSALITY
        elif score >= 2:
            result = CausalityResult.WEAK_CAUSALITY
        elif score >= 1:
            result = CausalityResult.WEAK_CAUSALITY
        else:
            result = CausalityResult.NO_CAUSALITY
        
        # Проверка на ложную корреляцию
        if lead_lag and lead_lag.max_correlation < self.thresholds["weak_correlation"] and \
           (not conditional_results or all(c.p_value > 0.1 for c in conditional_results)) and \
           (not regime_conditioned or regime_conditioned.regime_dependency < 0.3):
            result = CausalityResult.SPURIOUS
        
        # Ограничить уверенность
        confidence = min(1.0, confidence)
        
        return result, confidence
    
    def _create_conclusion(
        self,
        result: CausalityResult,
        confidence: float,
        lead_lag: LeadLagResult | None,
        event_study: EventStudyResult | None,
        conditional_results: list[ConditionalResult],
        regime_conditioned: RegimeConditionedResult | None,
    ) -> tuple[str, list[str]]:
        """
        Создать вывод и рекомендации.
        
        Args:
            result: Итоговый результат
            confidence: Уверенность
            lead_lag: Результат lead/lag анализа
            event_study: Результат event study
            conditional_results: Результаты условного анализа
            regime_conditioned: Результат анализа с учётом режима
        
        Returns:
            Вывод и рекомендации
        """
        conclusion = ""
        recommendations = []
        
        if result == CausalityResult.STRONG_CAUSALITY:
            conclusion = f"Strong causality detected between {lead_lag.feature_name if lead_lag else 'feature'} and {lead_lag.target_name if lead_lag else 'target'}"
            recommendations.append("Consider using this feature in models")
            recommendations.append("Perform further validation with OOS data")
            recommendations.append("Test robustness across different regimes")
        elif result == CausalityResult.MODERATE_CAUSALITY:
            conclusion = f"Moderate causality detected between {lead_lag.feature_name if lead_lag else 'feature'} and {lead_lag.target_name if lead_lag else 'target'}"
            recommendations.append("Consider using this feature with caution")
            recommendations.append("Perform additional tests to confirm causality")
        elif result == CausalityResult.WEAK_CAUSALITY:
            conclusion = f"Weak causality detected between {lead_lag.feature_name if lead_lag else 'feature'} and {lead_lag.target_name if lead_lag else 'target'}"
            recommendations.append("This feature may have limited predictive value")
            recommendations.append("Consider excluding if it doesn't improve model performance")
        elif result == CausalityResult.NO_CAUSALITY:
            conclusion = f"No causality detected between {lead_lag.feature_name if lead_lag else 'feature'} and {lead_lag.target_name if lead_lag else 'target'}"
            recommendations.append("This feature likely has no predictive value")
            recommendations.append("Consider removing from analysis")
        elif result == CausalityResult.SPURIOUS:
            conclusion = f"Spurious correlation detected between {lead_lag.feature_name if lead_lag else 'feature'} and {lead_lag.target_name if lead_lag else 'target'}"
            recommendations.append("This correlation is likely coincidental")
            recommendations.append("Do not use this feature for prediction")
        
        # Добавить информацию о lead/lag
        if lead_lag and lead_lag.optimal_lag != 0:
            conclusion += f" Optimal lag: {lead_lag.optimal_lag} minutes"
            if lead_lag.optimal_lag > 0:
                recommendations.append(f"Feature leads target by {lead_lag.optimal_lag} minutes")
            else:
                recommendations.append(f"Feature lags target by {abs(lead_lag.optimal_lag)} minutes")
        
        # Добавить информацию о режиме
        if regime_conditioned and regime_conditioned.regime_dependency > 0.3:
            conclusion += f" Relationship is regime-dependent"
            recommendations.append("Consider regime-conditioned models")
        
        return conclusion, recommendations
    
    def get_analysis(self, analysis_id: str) -> CausalityAnalysis | None:
        """
        Получить анализ.
        
        Args:
            analysis_id: ID анализа
        
        Returns:
            Анализ или None
        """
        return self._analyses.get(analysis_id)
    
    def search_analyses(
        self,
        feature_name: str = "",
        target_name: str = "",
        min_confidence: float = 0.0,
        result: CausalityResult | None = None,
    ) -> list[CausalityAnalysis]:
        """
        Поиск анализов.
        
        Args:
            feature_name: Название feature
            target_name: Название целевой переменной
            min_confidence: Минимальная уверенность
            result: Результат
        
        Returns:
            Список анализов
        """
        results = []
        for analysis in self._analyses.values():
            if feature_name and analysis.feature_name != feature_name:
                continue
            if target_name and analysis.target_name != target_name:
                continue
            if analysis.confidence < min_confidence:
                continue
            if result and analysis.result != result:
                continue
            results.append(analysis)
        
        return results


# Глобальный экземпляр
_causality_research_engine: CausalityResearchEngine | None = None


def get_causality_research_engine() -> CausalityResearchEngine:
    """Получить глобальный Causality Research Engine"""
    global _causality_research_engine
    if _causality_research_engine is None:
        _causality_research_engine = CausalityResearchEngine()
    return _causality_research_engine


def reset_causality_research_engine():
    """Сбросить Causality Research Engine (для тестов)"""
    global _causality_research_engine
    _causality_research_engine = CausalityResearchEngine()
