"""
ASTRA BOT — Regime Similarity Engine

Движок оценки схожести режимов (Master Specification v2, Section 9)

Для каждого текущего состояния оценивает:
- similarity_to_historical_states

Например:
- Current state -> 89% similarity -> 1834 historical observations
- Current state -> 31% similarity -> 47 observations

Второй случай должен автоматически получать повышенную неопределённость.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from scipy import stats
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)


@dataclass
class MarketState:
    """Состояние рынка"""
    timestamp: datetime
    features: dict[str, float]  # Характеристики состояния
    regime: str  # Режим рынка
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "features": self.features,
            "regime": self.regime,
        }


@dataclass
class SimilarityResult:
    """Результат оценки схожести"""
    current_state: MarketState
    similarity_score: float  # 0-1
    num_historical_observations: int
    top_matches: list[tuple[MarketState, float]] = field(default_factory=list)  # (state, similarity)
    
    # Статистика
    mean_similarity: float = 0.0
    std_similarity: float = 0.0
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "similarity_score": self.similarity_score,
            "num_historical_observations": self.num_historical_observations,
            "mean_similarity": self.mean_similarity,
            "std_similarity": self.std_similarity,
            "timestamp": self.timestamp.isoformat(),
            "top_matches": [
                {"timestamp": m[0].timestamp.isoformat(), "similarity": m[1]}
                for m in self.top_matches
            ] if self.top_matches else [],
        }


@dataclass
class RegimeSimilarityAssessment:
    """Оценка схожести режима"""
    current_regime: str
    similarity_score: float  # 0-1
    historical_coverage: int
    regime_stability: float  # Стабильность текущего режима
    transition_probability: float  # Вероятность перехода
    
    # Флаги
    is_unknown_regime: bool = False
    needs_increased_uncertainty: bool = False
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "current_regime": self.current_regime,
            "similarity_score": self.similarity_score,
            "historical_coverage": self.historical_coverage,
            "regime_stability": self.regime_stability,
            "transition_probability": self.transition_probability,
            "is_unknown_regime": self.is_unknown_regime,
            "needs_increased_uncertainty": self.needs_increased_uncertainty,
            "timestamp": self.timestamp.isoformat(),
        }


class RegimeSimilarityEngine:
    """
    Движок оценки схожести режимов.
    
    Оценивает, насколько текущее состояние рынка похоже на исторические
    состояния, и определяет уровень неопределённости.
    """
    
    def __init__(self):
        # Хранение исторических состояний
        self.historical_states: list[MarketState] = []
        
        # Пороги
        self.similarity_thresholds = {
            "high": 0.8,
            "medium": 0.6,
            "low": 0.4,
        }
        
        # Минимальное количество наблюдений
        self.min_observations = 10
        
        # Максимальное количество сравнений
        self.max_comparisons = 1000
    
    def add_historical_state(self, state: MarketState) -> None:
        """
        Добавить историческое состояние.
        
        Args:
            state: Состояние рынка
        """
        self.historical_states.append(state)
        
        # Ограничить количество хранимых состояний
        if len(self.historical_states) > self.max_comparisons * 2:
            self.historical_states = self.historical_states[-self.max_comparisons * 2:]
    
    def calculate_similarity(
        self,
        state1: MarketState,
        state2: MarketState
    ) -> float:
        """
        Рассчитать схожесть между двумя состояниями.
        
        Args:
            state1: Первое состояние
            state2: Второе состояние
        
        Returns:
            Схожесть (0-1)
        """
        # Получить общие характеристики
        common_features = set(state1.features.keys()) & set(state2.features.keys())
        
        if not common_features:
            return 0.0
        
        # Создать векторы
        features1 = [state1.features[f] for f in common_features]
        features2 = [state2.features[f] for f in common_features]
        
        # Стандартизировать
        scaler = StandardScaler()
        X = np.array([features1, features2])
        X_scaled = scaler.fit_transform(X)
        
        # Рассчитать косинусную схожесть
        similarity = cosine_similarity([X_scaled[0]], [X_scaled[1]])[0][0]
        
        return similarity
    
    def find_similar_states(
        self,
        current_state: MarketState,
        limit: int = 10
    ) -> SimilarityResult:
        """
        Найти похожие исторические состояния.
        
        Args:
            current_state: Текущее состояние
            limit: Максимальное количество результатов
        
        Returns:
            SimilarityResult
        """
        if not self.historical_states:
            return SimilarityResult(
                current_state=current_state,
                similarity_score=0.0,
                num_historical_observations=0
            )
        
        # Рассчитать схожесть со всеми историческими состояниями
        similarities = []
        for historical_state in self.historical_states:
            similarity = self.calculate_similarity(current_state, historical_state)
            similarities.append((historical_state, similarity))
        
        # Отсортировать по схожести
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        # Взять топ результаты
        top_matches = similarities[:limit]
        
        # Рассчитать статистику
        all_similarities = [s[1] for s in similarities]
        mean_similarity = np.mean(all_similarities)
        std_similarity = np.std(all_similarities)
        
        # Рассчитать итоговую оценку схожести
        # Используем среднюю схожесть топ результатов
        top_similarities = [s[1] for s in top_matches]
        similarity_score = np.mean(top_similarities) if top_similarities else 0.0
        
        return SimilarityResult(
            current_state=current_state,
            similarity_score=similarity_score,
            num_historical_observations=len(self.historical_states),
            top_matches=top_matches,
            mean_similarity=mean_similarity,
            std_similarity=std_similarity
        )
    
    def assess_regime_similarity(
        self,
        current_state: MarketState
    ) -> RegimeSimilarityAssessment:
        """
        Оценить схожесть режима.
        
        Args:
            current_state: Текущее состояние
        
        Returns:
            RegimeSimilarityAssessment
        """
        # Найти похожие состояния
        similarity_result = self.find_similar_states(current_state)
        
        # Рассчитать параметры
        similarity_score = similarity_result.similarity_score
        historical_coverage = similarity_result.num_historical_observations
        
        # Определить стабильность режима
        # Стабильность = доля исторических состояний с таким же режимом
        same_regime_count = 0
        for state in self.historical_states:
            if state.regime == current_state.regime:
                same_regime_count += 1
        
        regime_stability = same_regime_count / len(self.historical_states) if self.historical_states else 0.0
        
        # Определить вероятность перехода
        # Если стабильность низкая, вероятность перехода высокая
        transition_probability = 1.0 - regime_stability
        
        # Определить, является ли режим неизвестным
        is_unknown_regime = (current_state.regime == "UNKNOWN" or 
                            similarity_score < self.similarity_thresholds["low"])
        
        # Определить, нужна ли повышенная неопределённость
        needs_increased_uncertainty = (
            is_unknown_regime or
            similarity_score < self.similarity_thresholds["medium"] or
            historical_coverage < self.min_observations
        )
        
        return RegimeSimilarityAssessment(
            current_regime=current_state.regime,
            similarity_score=similarity_score,
            historical_coverage=historical_coverage,
            regime_stability=regime_stability,
            transition_probability=transition_probability,
            is_unknown_regime=is_unknown_regime,
            needs_increased_uncertainty=needs_increased_uncertainty
        )
    
    def should_trade_in_regime(
        self,
        assessment: RegimeSimilarityAssessment
    ) -> bool:
        """
        Определить, следует ли торговать в текущем режиме.
        
        Args:
            assessment: Оценка схожести режима
        
        Returns:
            True если можно торговать
        """
        # Не торговать в неизвестном режиме
        if assessment.is_unknown_regime:
            return False
        
        # Не торговать при низкой схожести и малом покрытии
        if (assessment.similarity_score < self.similarity_thresholds["low"] and
            assessment.historical_coverage < self.min_observations):
            return False
        
        return True
    
    def get_uncertainty_multiplier(
        self,
        assessment: RegimeSimilarityAssessment
    ) -> float:
        """
        Получить множитель неопределённости для режима.
        
        Args:
            assessment: Оценка схожести режима
        
        Returns:
            Множитель неопределённости
        """
        if assessment.is_unknown_regime:
            return 2.0  # Удвоенная неопределённость
        
        if assessment.needs_increased_uncertainty:
            # Линейная зависимость от схожести
            return 1.0 + (1.0 - assessment.similarity_score)
        
        return 1.0
    
    def find_regime_transitions(
        self,
        regime: str
    ) -> list[tuple[datetime, str, str]]:
        """
        Найти переходы режимов.
        
        Args:
            regime: Текущий режим
        
        Returns:
            Список переходов (время, из, в)
        """
        transitions = []
        
        for i in range(1, len(self.historical_states)):
            prev_state = self.historical_states[i - 1]
            curr_state = self.historical_states[i]
            
            if prev_state.regime != curr_state.regime:
                transitions.append((
                    curr_state.timestamp,
                    prev_state.regime,
                    curr_state.regime
                ))
        
        return transitions
    
    def get_regime_statistics(self, regime: str) -> dict[str, Any]:
        """
        Получить статистику по режиму.
        
        Args:
            regime: Режим
        
        Returns:
            Статистика режима
        """
        regime_states = [s for s in self.historical_states if s.regime == regime]
        
        if not regime_states:
            return {}
        
        # Рассчитать статистику
        durations = []
        for i in range(1, len(regime_states)):
            duration = (regime_states[i].timestamp - regime_states[i - 1].timestamp).total_seconds()
            durations.append(duration)
        
        avg_duration = np.mean(durations) if durations else 0
        
        return {
            "regime": regime,
            "count": len(regime_states),
            "avg_duration_seconds": avg_duration,
            "first_observation": regime_states[0].timestamp.isoformat(),
            "last_observation": regime_states[-1].timestamp.isoformat(),
        }
    
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
            i for i, state in enumerate(self.historical_states)
            if state.timestamp < cutoff
        ]
        
        for i in sorted(states_to_remove, reverse=True):
            del self.historical_states[i]
        
        return len(states_to_remove)


# Глобальный экземпляр Regime Similarity Engine
_regime_similarity_engine: RegimeSimilarityEngine | None = None


def get_regime_similarity_engine() -> RegimeSimilarityEngine:
    """Получить глобальный Regime Similarity Engine"""
    global _regime_similarity_engine
    if _regime_similarity_engine is None:
        _regime_similarity_engine = RegimeSimilarityEngine()
    return _regime_similarity_engine


def reset_regime_similarity_engine():
    """Сбросить Regime Similarity Engine (для тестов)"""
    global _regime_similarity_engine
    _regime_similarity_engine = RegimeSimilarityEngine()
