"""
ASTRA BOT — Signal Correlation Engine

Движок анализа корреляции сигналов (Master Specification v2, Section 23)

Исследует корреляцию:
- strategy vs strategy
- signal vs signal
- feature vs feature
- position vs position

Если три стратегии фактически используют один фактор:
- не считать их тремя независимыми подтверждениями

Определяет factor_group для реальной независимости alpha sources.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)


@dataclass
class CorrelationMatrix:
    """Матрица корреляции"""
    matrix: pd.DataFrame
    timestamp: datetime
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "matrix": self.matrix.to_dict(),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class FactorGroup:
    """Группа факторов"""
    group_id: str
    signals: list[str]  # Имена сигналов в группе
    features: list[str]  # Факторы в группе
    correlation_score: float  # Средняя корреляция внутри группы
    is_independent: bool  # Является ли группа независимой
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "signals": self.signals,
            "features": self.features,
            "correlation_score": self.correlation_score,
            "is_independent": self.is_independent,
        }


@dataclass
class SignalCorrelationResult:
    """Результат анализа корреляции сигналов"""
    correlation_matrix: CorrelationMatrix
    factor_groups: list[FactorGroup]
    independent_signals: list[str]
    correlated_pairs: list[tuple[str, str, float]]  # (signal1, signal2, correlation)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_matrix": self.correlation_matrix.to_dict(),
            "factor_groups": [g.to_dict() for g in self.factor_groups],
            "independent_signals": self.independent_signals,
            "correlated_pairs": [
                {"signal1": p[0], "signal2": p[1], "correlation": p[2]}
                for p in self.correlated_pairs
            ],
        }


@dataclass
class SignalFeatures:
    """Факторы сигнала"""
    signal_name: str
    features: dict[str, float]  # Имя фактора -> значение
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_name": self.signal_name,
            "features": self.features,
        }


class SignalCorrelationEngine:
    """
    Движок анализа корреляции сигналов.
    
    Определяет группы коррелированных сигналов и факторов,
    чтобы избежать двойного счета одного и того же alpha source.
    """
    
    def __init__(self):
        # Пороги корреляции
        self.correlation_thresholds = {
            "high": 0.8,
            "medium": 0.5,
            "low": 0.3,
        }
        
        # Параметры кластеризации
        self.cluster_eps = 0.3
        self.cluster_min_samples = 2
    
    def calculate_correlation_matrix(
        self,
        signals: list[SignalFeatures]
    ) -> CorrelationMatrix:
        """
        Рассчитать матрицу корреляции между сигналами.
        
        Args:
            signals: Список сигналов с их факторами
        
        Returns:
            CorrelationMatrix
        """
        if not signals or len(signals) < 2:
            return CorrelationMatrix(
                matrix=pd.DataFrame(),
                timestamp=datetime.now()
            )
        
        # Создать матрицу факторов
        signal_names = [s.signal_name for s in signals]
        feature_names = sorted(set(
            feature for s in signals for feature in s.features.keys()
        ))
        
        # Создать DataFrame
        data = []
        for signal in signals:
            row = [signal.features.get(feature, 0.0) for feature in feature_names]
            data.append(row)
        
        df = pd.DataFrame(data, index=signal_names, columns=feature_names)
        
        # Рассчитать корреляционную матрицу
        corr_matrix = df.corr()
        
        return CorrelationMatrix(
            matrix=corr_matrix,
            timestamp=datetime.now()
        )
    
    def find_correlated_pairs(
        self,
        correlation_matrix: CorrelationMatrix,
        threshold: float = 0.7
    ) -> list[tuple[str, str, float]]:
        """
        Найти пары сильно коррелированных сигналов.
        
        Args:
            correlation_matrix: Матрица корреляции
            threshold: Порог корреляции
        
        Returns:
            Список коррелированных пар
        """
        if correlation_matrix.matrix.empty:
            return []
        
        corr_matrix = correlation_matrix.matrix
        correlated_pairs = []
        
        # Пройти по верхнему треугольнику матрицы
        for i in range(len(corr_matrix)):
            for j in range(i + 1, len(corr_matrix)):
                corr_value = corr_matrix.iloc[i, j]
                if abs(corr_value) >= threshold:
                    correlated_pairs.append((
                        corr_matrix.index[i],
                        corr_matrix.index[j],
                        corr_value
                    ))
        
        # Сортировать по абсолютному значению корреляции
        correlated_pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        
        return correlated_pairs
    
    def cluster_signals(
        self,
        signals: list[SignalFeatures]
    ) -> list[FactorGroup]:
        """
        Кластеризовать сигналы на основе их факторов.
        
        Args:
            signals: Список сигналов с их факторами
        
        Returns:
            Список групп факторов
        """
        if not signals or len(signals) < 2:
            return [
                FactorGroup(
                    group_id="single",
                    signals=[s.signal_name for s in signals],
                    features=list(signals[0].features.keys()) if signals else [],
                    correlation_score=0.0,
                    is_independent=True
                )
            ]
        
        # Создать матрицу факторов
        feature_names = sorted(set(
            feature for s in signals for feature in s.features.keys()
        ))
        
        # Создать векторы для кластеризации
        vectors = []
        for signal in signals:
            vector = [signal.features.get(feature, 0.0) for feature in feature_names]
            vectors.append(vector)
        
        X = np.array(vectors)
        
        # Стандартизировать данные
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        # Кластеризация DBSCAN
        clustering = DBSCAN(
            eps=self.cluster_eps,
            min_samples=self.cluster_min_samples,
            metric='cosine'
        )
        labels = clustering.fit_predict(X_scaled)
        
        # Создать группы
        groups = {}
        for i, label in enumerate(labels):
            if label not in groups:
                groups[label] = {
                    "signals": [],
                    "features": set(),
                    "indices": []
                }
            groups[label]["signals"].append(signals[i].signal_name)
            groups[label]["features"].update(signals[i].features.keys())
            groups[label]["indices"].append(i)
        
        # Преобразовать в FactorGroup
        factor_groups = []
        for label, group_data in groups.items():
            # Рассчитать среднюю корреляцию внутри группы
            if len(group_data["indices"]) < 2:
                correlation_score = 0.0
            else:
                # Рассчитать корреляцию между сигналами в группе
                group_vectors = [vectors[i] for i in group_data["indices"]]
                group_corrs = []
                for i in range(len(group_vectors)):
                    for j in range(i + 1, len(group_vectors)):
                        corr = np.corrcoef(group_vectors[i], group_vectors[j])[0, 1]
                        group_corrs.append(abs(corr))
                correlation_score = np.mean(group_corrs) if group_corrs else 0.0
            
            factor_groups.append(FactorGroup(
                group_id=f"group_{label}",
                signals=group_data["signals"],
                features=list(group_data["features"]),
                correlation_score=correlation_score,
                is_independent=correlation_score < self.correlation_thresholds["medium"]
            ))
        
        return factor_groups
    
    def identify_independent_signals(
        self,
        factor_groups: list[FactorGroup]
    ) -> list[str]:
        """
        Определить независимые сигналы.
        
        Args:
            factor_groups: Группы факторов
        
        Returns:
            Список независимых сигналов
        """
        independent_signals = []
        
        for group in factor_groups:
            if group.is_independent:
                independent_signals.extend(group.signals)
            else:
                # В не независимой группе выбираем сигнал с лучшей производительностью
                # (в реальной системе это будет на основе статистики)
                # Пока просто берём первый
                independent_signals.append(group.signals[0])
        
        return independent_signals
    
    def analyze_signal_correlation(
        self,
        signals: list[SignalFeatures]
    ) -> SignalCorrelationResult:
        """
        Полный анализ корреляции сигналов.
        
        Args:
            signals: Список сигналов с их факторами
        
        Returns:
            SignalCorrelationResult
        """
        # Рассчитать матрицу корреляции
        correlation_matrix = self.calculate_correlation_matrix(signals)
        
        # Найти коррелированные пары
        correlated_pairs = self.find_correlated_pairs(
            correlation_matrix,
            threshold=self.correlation_thresholds["high"]
        )
        
        # Кластеризовать сигналы
        factor_groups = self.cluster_signals(signals)
        
        # Определить независимые сигналы
        independent_signals = self.identify_independent_signals(factor_groups)
        
        return SignalCorrelationResult(
            correlation_matrix=correlation_matrix,
            factor_groups=factor_groups,
            independent_signals=independent_signals,
            correlated_pairs=correlated_pairs
        )
    
    def get_signal_factor_group(
        self,
        signal_name: str,
        factor_groups: list[FactorGroup]
    ) -> str | None:
        """
        Получить идентификатор группы факторов для сигнала.
        
        Args:
            signal_name: Имя сигнала
            factor_groups: Группы факторов
        
        Returns:
            Идентификатор группы или None
        """
        for group in factor_groups:
            if signal_name in group.signals:
                return group.group_id
        return None
    
    def are_signals_independent(
        self,
        signal1: str,
        signal2: str,
        correlation_matrix: CorrelationMatrix,
        threshold: float = 0.5
    ) -> bool:
        """
        Проверить, являются ли два сигнала независимыми.
        
        Args:
            signal1: Имя первого сигнала
            signal2: Имя второго сигнала
            correlation_matrix: Матрица корреляции
            threshold: Порог корреляции
        
        Returns:
            True если сигналы независимы
        """
        if correlation_matrix.matrix.empty:
            return True
        
        corr_matrix = correlation_matrix.matrix
        
        if signal1 not in corr_matrix.index or signal2 not in corr_matrix.index:
            return True
        
        correlation = abs(corr_matrix.loc[signal1, signal2])
        return correlation < threshold
    
    def calculate_feature_correlation(
        self,
        feature_data: dict[str, list[float]]  # feature_name -> values
    ) -> pd.DataFrame:
        """
        Рассчитать корреляцию между факторами.
        
        Args:
            feature_data: Данные факторов
        
        Returns:
            Матрица корреляции факторов
        """
        df = pd.DataFrame(feature_data)
        return df.corr()
    
    def detect_redundant_features(
        self,
        feature_correlation: pd.DataFrame,
        threshold: float = 0.9
    ) -> list[tuple[str, str, float]]:
        """
        Обнаружить избыточные факторы.
        
        Args:
            feature_correlation: Матрица корреляции факторов
            threshold: Порог корреляции
        
        Returns:
            Список избыточных пар
        """
        redundant_pairs = []
        
        for i in range(len(feature_correlation)):
            for j in range(i + 1, len(feature_correlation)):
                corr_value = feature_correlation.iloc[i, j]
                if abs(corr_value) >= threshold:
                    redundant_pairs.append((
                        feature_correlation.index[i],
                        feature_correlation.index[j],
                        corr_value
                    ))
        
        return redundant_pairs
    
    def optimize_signal_set(
        self,
        signals: list[SignalFeatures],
        max_signals: int = 3
    ) -> list[str]:
        """
        Оптимизировать набор сигналов для уменьшения корреляции.
        
        Args:
            signals: Список сигналов
            max_signals: Максимальное количество сигналов
        
        Returns:
            Оптимизированный набор сигналов
        """
        # Рассчитать корреляционную матрицу
        corr_matrix = self.calculate_correlation_matrix(signals)
        
        if corr_matrix.matrix.empty:
            return [s.signal_name for s in signals[:max_signals]]
        
        # Жадный алгоритм выбора сигналов
        selected_signals = []
        signal_names = [s.signal_name for s in signals]
        
        # Начинаем с сигнала с наименьшей средней корреляцией
        avg_correlations = []
        for signal in signal_names:
            signal_corrs = [
                abs(corr_matrix.matrix.loc[signal, other])
                for other in signal_names
                if other != signal
            ]
            avg_corr = np.mean(signal_corrs) if signal_corrs else 0
            avg_correlations.append((signal, avg_corr))
        
        # Сортировать по средней корреляции
        avg_correlations.sort(key=lambda x: x[1])
        
        # Выбрать сигналы с наименьшей корреляцией
        for signal, corr in avg_correlations:
            if len(selected_signals) >= max_signals:
                break
            
            # Проверить корреляцию с уже выбранными сигналами
            is_independent = True
            for selected in selected_signals:
                if signal in corr_matrix.matrix.index and selected in corr_matrix.matrix.columns:
                    corr_value = abs(corr_matrix.matrix.loc[signal, selected])
                    if corr_value >= self.correlation_thresholds["medium"]:
                        is_independent = False
                        break
            
            if is_independent:
                selected_signals.append(signal)
        
        return selected_signals


# Глобальный экземпляр Signal Correlation Engine
_signal_correlation_engine: SignalCorrelationEngine | None = None


def get_signal_correlation_engine() -> SignalCorrelationEngine:
    """Получить глобальный Signal Correlation Engine"""
    global _signal_correlation_engine
    if _signal_correlation_engine is None:
        _signal_correlation_engine = SignalCorrelationEngine()
    return _signal_correlation_engine


def reset_signal_correlation_engine():
    """Сбросить Signal Correlation Engine (для тестов)"""
    global _signal_correlation_engine
    _signal_correlation_engine = SignalCorrelationEngine()
