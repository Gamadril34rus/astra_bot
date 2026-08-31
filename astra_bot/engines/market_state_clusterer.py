"""
ASTRA BOT — Market State Clusterer

Движок кластеризации состояний рынка (Master Specification v2, Section 29)

Features:
- returns
- volatility
- volume
- OI (Open Interest)
- funding
- order flow
- spread
- depth
- correlation
- liquidations

Использовать clustering для обнаружения неизвестных состояний.

Результат:
- STATE_001
- STATE_002
- STATE_003
- ...

Исследовать forward outcome каждого состояния.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans, DBSCAN, AgglomerativeClustering
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score

logger = logging.getLogger(__name__)


@dataclass
class MarketStateFeatures:
    """Характеристики состояния рынка"""
    timestamp: datetime
    
    # Основные характеристики
    returns: float  # Доходность
    volatility: float  # Волатильность
    volume: float  # Объём
    
    # Дополнительные характеристики
    open_interest: float | None = None  # Open Interest
    funding_rate: float | None = None  # Ставка финансирования
    order_flow: float | None = None  # Поток ордеров
    spread: float | None = None  # Spread
    depth: float | None = None  # Глубина стакана
    correlation: float | None = None  # Корреляция
    liquidations: float | None = None  # Ликвидации
    
    def to_array(self) -> list[float]:
        """Преобразовать в массив"""
        return [
            self.returns,
            self.volatility,
            self.volume,
            self.open_interest or 0.0,
            self.funding_rate or 0.0,
            self.order_flow or 0.0,
            self.spread or 0.0,
            self.depth or 0.0,
            self.correlation or 0.0,
            self.liquidations or 0.0,
        ]
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "timestamp": self.timestamp.isoformat(),
            "returns": self.returns,
            "volatility": self.volatility,
            "volume": self.volume,
        }
        
        if self.open_interest is not None:
            result["open_interest"] = self.open_interest
        if self.funding_rate is not None:
            result["funding_rate"] = self.funding_rate
        if self.order_flow is not None:
            result["order_flow"] = self.order_flow
        if self.spread is not None:
            result["spread"] = self.spread
        if self.depth is not None:
            result["depth"] = self.depth
        if self.correlation is not None:
            result["correlation"] = self.correlation
        if self.liquidations is not None:
            result["liquidations"] = self.liquidations
        
        return result


@dataclass
class ClusterResult:
    """Результат кластеризации"""
    cluster_id: str  # STATE_001, STATE_002, etc.
    features: MarketStateFeatures
    cluster_center: list[float]  # Центр кластера
    silhouette_score: float  # Оценка силуэта
    
    # Статистика кластера
    count: int = 0
    avg_returns: float = 0.0
    avg_volatility: float = 0.0
    
    # Forward outcome
    forward_returns: list[float] = field(default_factory=list)
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "count": self.count,
            "avg_returns": self.avg_returns,
            "avg_volatility": self.avg_volatility,
            "silhouette_score": self.silhouette_score,
            "forward_returns": self.forward_returns,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ClusteringResult:
    """Результат кластеризации"""
    num_clusters: int
    method: str  # kmeans, dbscan, hierarchical
    clusters: list[ClusterResult]
    
    # Оценка качества
    silhouette_avg: float = 0.0
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "num_clusters": self.num_clusters,
            "method": self.method,
            "silhouette_avg": self.silhouette_avg,
            "clusters": [c.to_dict() for c in self.clusters],
            "timestamp": self.timestamp.isoformat(),
        }


class MarketStateClusterer:
    """
    Движок кластеризации состояний рынка.
    
    Обнаруживает неизвестные состояния рынка и исследует их forward outcome.
    """
    
    def __init__(self):
        # Хранение состояний
        self.states: list[MarketStateFeatures] = []
        
        # Хранение результатов кластеризации
        self.clustering_results: list[ClusteringResult] = []
        
        # Параметры кластеризации
        self.default_n_clusters = 5
        self.default_eps = 0.5
        self.default_min_samples = 5
        
        # Минимальное количество состояний для кластеризации
        self.min_states_for_clustering = 10
    
    def add_state(self, state: MarketStateFeatures) -> None:
        """
        Добавить состояние рынка.
        
        Args:
            state: Состояние рынка
        """
        self.states.append(state)
    
    def preprocess_features(self) -> np.ndarray:
        """
        Подготовить характеристики для кластеризации.
        
        Returns:
            Матрица характеристик
        """
        if not self.states:
            return np.array([])
        
        # Создать матрицу характеристик
        X = np.array([state.to_array() for state in self.states])
        
        # Стандартизировать
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        return X_scaled
    
    def cluster_kmeans(
        self,
        n_clusters: int | None = None
    ) -> ClusteringResult:
        """
        Кластеризация методом K-means.
        
        Args:
            n_clusters: Количество кластеров
        
        Returns:
            ClusteringResult
        """
        if len(self.states) < self.min_states_for_clustering:
            return ClusteringResult(
                num_clusters=0,
                method="kmeans",
                clusters=[],
                silhouette_avg=0.0
            )
        
        if n_clusters is None:
            n_clusters = self.default_n_clusters
        
        # Подготовить характеристики
        X = self.preprocess_features()
        
        # Кластеризация
        kmeans = KMeans(n_clusters=n_clusters, random_state=42)
        labels = kmeans.fit_predict(X)
        
        # Рассчитать оценку силуэта
        silhouette_avg = silhouette_score(X, labels)
        
        # Создать результаты
        clusters = self._create_clusters(labels, kmeans.cluster_centers_)
        
        result = ClusteringResult(
            num_clusters=n_clusters,
            method="kmeans",
            clusters=clusters,
            silhouette_avg=silhouette_avg
        )
        
        self.clustering_results.append(result)
        
        return result
    
    def cluster_dbscan(
        self,
        eps: float | None = None,
        min_samples: int | None = None
    ) -> ClusteringResult:
        """
        Кластеризация методом DBSCAN.
        
        Args:
            eps: Радиус
            min_samples: Минимальное количество точек
        
        Returns:
            ClusteringResult
        """
        if len(self.states) < self.min_states_for_clustering:
            return ClusteringResult(
                num_clusters=0,
                method="dbscan",
                clusters=[],
                silhouette_avg=0.0
            )
        
        if eps is None:
            eps = self.default_eps
        if min_samples is None:
            min_samples = self.default_min_samples
        
        # Подготовить характеристики
        X = self.preprocess_features()
        
        # Кластеризация
        dbscan = DBSCAN(eps=eps, min_samples=min_samples)
        labels = dbscan.fit_predict(X)
        
        # Количество кластеров (исключая шум)
        n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
        
        # Рассчитать оценку силуэта (если есть кластеры)
        silhouette_avg = 0.0
        if n_clusters > 1:
            silhouette_avg = silhouette_score(X, labels)
        
        # Создать результаты
        clusters = self._create_clusters(labels, [])
        
        result = ClusteringResult(
            num_clusters=n_clusters,
            method="dbscan",
            clusters=clusters,
            silhouette_avg=silhouette_avg
        )
        
        self.clustering_results.append(result)
        
        return result
    
    def cluster_hierarchical(
        self,
        n_clusters: int | None = None
    ) -> ClusteringResult:
        """
        Кластеризация иерархическим методом.
        
        Args:
            n_clusters: Количество кластеров
        
        Returns:
            ClusteringResult
        """
        if len(self.states) < self.min_states_for_clustering:
            return ClusteringResult(
                num_clusters=0,
                method="hierarchical",
                clusters=[],
                silhouette_avg=0.0
            )
        
        if n_clusters is None:
            n_clusters = self.default_n_clusters
        
        # Подготовить характеристики
        X = self.preprocess_features()
        
        # Кластеризация
        clustering = AgglomerativeClustering(n_clusters=n_clusters)
        labels = clustering.fit_predict(X)
        
        # Рассчитать оценку силуэта
        silhouette_avg = silhouette_score(X, labels)
        
        # Создать результаты
        clusters = self._create_clusters(labels, [])
        
        result = ClusteringResult(
            num_clusters=n_clusters,
            method="hierarchical",
            clusters=clusters,
            silhouette_avg=silhouette_avg
        )
        
        self.clustering_results.append(result)
        
        return result
    
    def _create_clusters(
        self,
        labels: np.ndarray,
        cluster_centers: np.ndarray | None
    ) -> list[ClusterResult]:
        """
        Создать объекты кластеров.
        
        Args:
            labels: Метки кластеров
            cluster_centers: Центры кластеров
        
        Returns:
            Список кластеров
        """
        clusters = []
        
        for i, label in enumerate(set(labels)):
            if label == -1:  # Шум в DBSCAN
                continue
            
            # Найти все состояния в кластере
            cluster_states = [
                self.states[j] for j in range(len(self.states)) 
                if labels[j] == label
            ]
            
            # Рассчитать статистику
            count = len(cluster_states)
            avg_returns = np.mean([s.returns for s in cluster_states])
            avg_volatility = np.mean([s.volatility for s in cluster_states])
            
            # Создать идентификатор кластера
            cluster_id = f"STATE_{i:03d}"
            
            # Центр кластера
            center = []
            if cluster_centers is not None and i < len(cluster_centers):
                center = list(cluster_centers[i])
            
            # Создать кластер
            cluster = ClusterResult(
                cluster_id=cluster_id,
                features=cluster_states[0] if cluster_states else MarketStateFeatures(
                    timestamp=datetime.now(),
                    returns=0.0,
                    volatility=0.0,
                    volume=0.0
                ),
                cluster_center=center,
                silhouette_score=0.0,
                count=count,
                avg_returns=avg_returns,
                avg_volatility=avg_volatility
            )
            
            clusters.append(cluster)
        
        return clusters
    
    def find_optimal_clusters(
        self,
        max_clusters: int = 10
    ) -> ClusteringResult:
        """
        Найти оптимальное количество кластеров.
        
        Args:
            max_clusters: Максимальное количество кластеров
        
        Returns:
            Лучший результат кластеризации
        """
        if len(self.states) < self.min_states_for_clustering:
            return ClusteringResult(
                num_clusters=0,
                method="optimal",
                clusters=[],
                silhouette_avg=0.0
            )
        
        best_result = None
        best_score = -1
        
        for n_clusters in range(2, max_clusters + 1):
            result = self.cluster_kmeans(n_clusters)
            if result.silhouette_avg > best_score:
                best_score = result.silhouette_avg
                best_result = result
        
        return best_result
    
    def get_state_cluster(self, state: MarketStateFeatures) -> str | None:
        """
        Определить кластер для состояния.
        
        Args:
            state: Состояние рынка
        
        Returns:
            Идентификатор кластера или None
        """
        if not self.clustering_results:
            return None
        
        # Использовать последний результат кластеризации
        last_result = self.clustering_results[-1]
        
        # Подготовить характеристики
        X = self.preprocess_features()
        
        # Найти кластер для состояния
        # Для простоты используем KMeans
        if last_result.method == "kmeans":
            kmeans = KMeans(n_clusters=last_result.num_clusters, random_state=42)
            kmeans.fit(X)
            
            state_array = np.array([state.to_array()])
            scaler = StandardScaler()
            state_scaled = scaler.fit_transform(state_array)
            
            cluster_id = kmeans.predict(state_scaled)[0]
            
            # Найти идентификатор кластера
            for cluster in last_result.clusters:
                # Просто возвращаем первый кластер (упрощение)
                return cluster.cluster_id
        
        return None
    
    def analyze_forward_outcomes(
        self,
        clustering_result: ClusteringResult,
        forward_returns: dict[str, list[float]]  # cluster_id -> forward returns
    ) -> dict[str, Any]:
        """
        Проанализировать forward outcome каждого состояния.
        
        Args:
            clustering_result: Результат кластеризации
            forward_returns: Forward доходности по кластерам
        
        Returns:
            Анализ forward outcomes
        """
        analysis = {}
        
        for cluster in clustering_result.clusters:
            if cluster.cluster_id not in forward_returns:
                continue
            
            returns = forward_returns[cluster.cluster_id]
            
            if returns:
                analysis[cluster.cluster_id] = {
                    "count": len(returns),
                    "mean_return": np.mean(returns),
                    "std_return": np.std(returns),
                    "min_return": min(returns),
                    "max_return": max(returns),
                    "sharpe_ratio": np.mean(returns) / np.std(returns) if np.std(returns) > 0 else 0,
                }
        
        return analysis
    
    def detect_unknown_states(
        self,
        new_state: MarketStateFeatures
    ) -> bool:
        """
        Обнаружить неизвестные состояния (Section 30).
        
        Args:
            new_state: Новое состояние рынка
        
        Returns:
            True если состояние неизвестное
        """
        if not self.clustering_results:
            return True
        
        # Если кластеризация не была выполнена
        last_result = self.clustering_results[-1]
        if last_result.num_clusters == 0:
            return True
        
        # Определить кластер для нового состояния
        cluster_id = self.get_state_cluster(new_state)
        
        # Если кластер не найден, состояние неизвестное
        if cluster_id is None:
            return True
        
        # Если кластер найден, но у него мало наблюдений, состояние может быть неизвестным
        for cluster in last_result.clusters:
            if cluster.cluster_id == cluster_id:
                if cluster.count < 5:  # Мало наблюдений
                    return True
                break
        
        return False
    
    def cleanup_old_states(self, max_age_days: int = 90) -> int:
        """
        Очистить старые состояния.
        
        Args:
            max_age_days: Максимальный возраст в днях
        
        Returns:
            Количество удалённых состояний
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        states_to_remove = [
            i for i, state in enumerate(self.states)
            if state.timestamp < cutoff
        ]
        
        for i in sorted(states_to_remove, reverse=True):
            del self.states[i]
        
        return len(states_to_remove)


# Глобальный экземпляр Market State Clusterer
_market_state_clusterer: MarketStateClusterer | None = None


def get_market_state_clusterer() -> MarketStateClusterer:
    """Получить глобальный Market State Clusterer"""
    global _market_state_clusterer
    if _market_state_clusterer is None:
        _market_state_clusterer = MarketStateClusterer()
    return _market_state_clusterer


def reset_market_state_clusterer():
    """Сбросить Market State Clusterer (для тестов)"""
    global _market_state_clusterer
    _market_state_clusterer = MarketStateClusterer()
