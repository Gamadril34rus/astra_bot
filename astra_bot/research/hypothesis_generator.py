"""
ASTRA BOT — Hypothesis Generator

Генератор гипотез (Master Specification v2, Section 49-51)

Research Agent должен анализировать:
- current knowledge
- unknowns
- failed hypotheses
- degrading features
- strategy degradation
- market regime changes
- execution losses

И самостоятельно формировать следующие исследования.

Каждое исследование получает:
- expected_value_of_information
- data_availability
- sample_size
- uncertainty
- computational_cost
- potential_trading_impact

В первую очередь исследуются вопросы с максимальной потенциальной пользой.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class HypothesisType(str, Enum):
    """Типы гипотез"""
    FEATURE_DECAY = "feature_decay"
    STRATEGY_DEGRADATION = "strategy_degradation"
    REGIME_CHANGE = "regime_change"
    EXECUTION_LOSS = "execution_loss"
    CORRELATION_CHANGE = "correlation_change"
    NEW_SIGNAL = "new_signal"
    MODEL_IMPROVEMENT = "model_improvement"
    RISK_OPTIMIZATION = "risk_optimization"


@dataclass
class KnowledgeGap:
    """Пробел в знаниях"""
    area: str
    description: str
    uncertainty: float  # 0-1
    potential_impact: float  # Потенциальное влияние
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "description": self.description,
            "uncertainty": self.uncertainty,
            "potential_impact": self.potential_impact,
        }


@dataclass
class Hypothesis:
    """Гипотеза"""
    hypothesis_id: str
    title: str
    description: str
    hypothesis_type: HypothesisType
    
    # Приоритет
    expected_value_of_information: float
    data_availability: float  # 0-1
    sample_size: int
    uncertainty: float  # 0-1
    computational_cost: float  # 0-1
    potential_trading_impact: float  # 0-1
    
    # Статус
    status: str = "PENDING"  # PENDING, TESTING, VALIDATED, INVALIDATED
    priority_score: float = 0.0
    
    # Связи
    related_experiments: list[str] = field(default_factory=list)
    related_features: list[str] = field(default_factory=list)
    related_strategies: list[str] = field(default_factory=list)
    
    # Временные метки
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "description": self.description,
            "hypothesis_type": self.hypothesis_type.value,
            "expected_value_of_information": self.expected_value_of_information,
            "data_availability": self.data_availability,
            "sample_size": self.sample_size,
            "uncertainty": self.uncertainty,
            "computational_cost": self.computational_cost,
            "potential_trading_impact": self.potential_trading_impact,
            "status": self.status,
            "priority_score": self.priority_score,
            "related_experiments": self.related_experiments,
            "related_features": self.related_features,
            "related_strategies": self.related_strategies,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        return result


@dataclass
class ResearchPriority:
    """Приоритет исследования"""
    hypothesis: Hypothesis
    score: float
    rank: int
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis.to_dict(),
            "score": self.score,
            "rank": self.rank,
        }


class HypothesisGenerator:
    """
    Генератор гипотез.
    
    Анализирует текущее состояние системы и генерирует новые гипотезы
    для исследования.
    """
    
    def __init__(self):
        # Хранение гипотез
        self.hypotheses: dict[str, Hypothesis] = {}
        
        # Счётчик гипотез
        self.hypothesis_counter = 0
        
        # Веса для расчёта приоритета
        self.priority_weights = {
            "expected_value_of_information": 0.4,
            "potential_trading_impact": 0.3,
            "uncertainty": 0.2,
            "data_availability": -0.1,  # Отрицательный вес (высокая доступность = низкий приоритет)
        }
    
    def generate_hypothesis_id(self) -> str:
        """Сгенерировать идентификатор гипотезы"""
        self.hypothesis_counter += 1
        return f"HYP_{self.hypothesis_counter:05d}"
    
    def identify_knowledge_gaps(
        self,
        current_knowledge: dict[str, Any],
        system_state: dict[str, Any]
    ) -> list[KnowledgeGap]:
        """
        Определить пробелы в знаниях.
        
        Args:
            current_knowledge: Текущие знания системы
            system_state: Состояние системы
        
        Returns:
            Список пробелов в знаниях
        """
        gaps = []
        
        # 1. Пробелы в данных
        if "data_coverage" in system_state:
            coverage = system_state["data_coverage"]
            if coverage < 1.0:
                gaps.append(KnowledgeGap(
                    area="data_coverage",
                    description=f"Low data coverage: {coverage:.1%}",
                    uncertainty=1.0 - coverage,
                    potential_impact=0.5
                ))
        
        # 2. Деградирующие факторы
        if "degrading_features" in current_knowledge:
            for feature, decay_rate in current_knowledge["degrading_features"].items():
                if decay_rate > 0.1:
                    gaps.append(KnowledgeGap(
                        area="feature_decay",
                        description=f"Feature {feature} is degrading at {decay_rate:.1%}",
                        uncertainty=decay_rate,
                        potential_impact=0.7
                    ))
        
        # 3. Деградирующие стратегии
        if "degrading_strategies" in current_knowledge:
            for strategy, decay_rate in current_knowledge["degrading_strategies"].items():
                if decay_rate > 0.1:
                    gaps.append(KnowledgeGap(
                        area="strategy_degradation",
                        description=f"Strategy {strategy} is degrading at {decay_rate:.1%}",
                        uncertainty=decay_rate,
                        potential_impact=0.8
                    ))
        
        # 4. Изменения режимов рынка
        if "regime_changes" in system_state:
            regime_changes = system_state["regime_changes"]
            if regime_changes > 0:
                gaps.append(KnowledgeGap(
                    area="regime_change",
                    description=f"{regime_changes} recent regime changes detected",
                    uncertainty=0.5,
                    potential_impact=0.9
                ))
        
        # 5. Убытки от исполнения
        if "execution_losses" in system_state:
            losses = system_state["execution_losses"]
            if losses > 0:
                gaps.append(KnowledgeGap(
                    area="execution_loss",
                    description=f"{losses} execution losses in last period",
                    uncertainty=0.4,
                    potential_impact=0.6
                ))
        
        # 6. Неизвестные режимы
        if "unknown_regimes" in system_state:
            unknown_count = system_state["unknown_regimes"]
            if unknown_count > 0:
                gaps.append(KnowledgeGap(
                    area="unknown_regime",
                    description=f"{unknown_count} unknown regimes detected",
                    uncertainty=0.8,
                    potential_impact=0.9
                ))
        
        # 7. Низкая корреляция между сигналами
        if "signal_correlation" in current_knowledge:
            avg_correlation = current_knowledge["signal_correlation"]
            if avg_correlation > 0.7:
                gaps.append(KnowledgeGap(
                    area="signal_correlation",
                    description=f"High signal correlation: {avg_correlation:.2f}",
                    uncertainty=0.3,
                    potential_impact=0.5
                ))
        
        return gaps
    
    def generate_hypotheses(
        self,
        knowledge_gaps: list[KnowledgeGap],
        current_knowledge: dict[str, Any],
        system_state: dict[str, Any]
    ) -> list[Hypothesis]:
        """
        Сгенерировать гипотезы на основе пробелов в знаниях.
        
        Args:
            knowledge_gaps: Пробелы в знаниях
            current_knowledge: Текущие знания
            system_state: Состояние системы
        
        Returns:
            Список гипотез
        """
        hypotheses = []
        
        for gap in knowledge_gaps:
            # Сгенерировать гипотезу для каждого пробела
            hypothesis = self._generate_hypothesis_from_gap(
                gap, current_knowledge, system_state
            )
            if hypothesis:
                hypotheses.append(hypothesis)
        
        return hypotheses
    
    def _generate_hypothesis_from_gap(
        self,
        gap: KnowledgeGap,
        current_knowledge: dict[str, Any],
        system_state: dict[str, Any]
    ) -> Hypothesis | None:
        """
        Сгенерировать гипотезу из пробела в знаниях.
        
        Args:
            gap: Пробел в знаниях
            current_knowledge: Текущие знания
            system_state: Состояние системы
        
        Returns:
            Гипотеза или None
        """
        hypothesis_id = self.generate_hypothesis_id()
        
        # Определить тип гипотезы
        if gap.area == "feature_decay":
            hypothesis_type = HypothesisType.FEATURE_DECAY
            title = f"Investigate feature decay: {gap.description}"
            description = (
                f"Feature decay has been detected. Need to investigate the cause "
                f"and potential impact on strategies."
            )
        elif gap.area == "strategy_degradation":
            hypothesis_type = HypothesisType.STRATEGY_DEGRADATION
            title = f"Investigate strategy degradation: {gap.description}"
            description = (
                f"Strategy performance is degrading. Need to analyze the cause "
                f"and determine if the strategy should be retired."
            )
        elif gap.area == "regime_change":
            hypothesis_type = HypothesisType.REGIME_CHANGE
            title = f"Investigate regime change: {gap.description}"
            description = (
                f"Market regime has changed. Need to understand the new regime "
                f"and adapt strategies accordingly."
            )
        elif gap.area == "execution_loss":
            hypothesis_type = HypothesisType.EXECUTION_LOSS
            title = f"Investigate execution losses: {gap.description}"
            description = (
                f"Execution losses have been detected. Need to analyze the cause "
                f"and improve execution strategies."
            )
        elif gap.area == "unknown_regime":
            hypothesis_type = HypothesisType.REGIME_CHANGE
            title = f"Investigate unknown regime: {gap.description}"
            description = (
                f"Unknown market regime detected. Need to classify and understand "
                f"this regime to make better trading decisions."
            )
        elif gap.area == "signal_correlation":
            hypothesis_type = HypothesisType.CORRELATION_CHANGE
            title = f"Investigate signal correlation: {gap.description}"
            description = (
                f"High correlation between signals detected. Need to investigate "
                f"if signals are providing independent information."
            )
        elif gap.area == "data_coverage":
            hypothesis_type = HypothesisType.NEW_SIGNAL
            title = f"Improve data coverage: {gap.description}"
            description = (
                f"Data coverage is incomplete. Need to identify and add missing data "
                f"to improve prediction quality."
            )
        else:
            return None
        
        # Оценить параметры гипотезы
        expected_value_of_information = gap.potential_impact * (1 - gap.uncertainty)
        
        # Оценить доступность данных
        data_availability = system_state.get("data_availability", {}).get(gap.area, 0.5)
        
        # Оценить размер выборки
        sample_size = system_state.get("sample_sizes", {}).get(gap.area, 100)
        
        # Оценить вычислительную стоимость
        computational_cost = self._estimate_computational_cost(hypothesis_type)
        
        # Рассчитать приоритет
        priority_score = self.calculate_priority_score(
            expected_value_of_information=expected_value_of_information,
            potential_trading_impact=gap.potential_impact,
            uncertainty=gap.uncertainty,
            data_availability=data_availability,
            computational_cost=computational_cost
        )
        
        hypothesis = Hypothesis(
            hypothesis_id=hypothesis_id,
            title=title,
            description=description,
            hypothesis_type=hypothesis_type,
            expected_value_of_information=expected_value_of_information,
            data_availability=data_availability,
            sample_size=sample_size,
            uncertainty=gap.uncertainty,
            computational_cost=computational_cost,
            potential_trading_impact=gap.potential_impact,
            priority_score=priority_score,
            status="PENDING"
        )
        
        self.hypotheses[hypothesis_id] = hypothesis
        
        return hypothesis
    
    def _estimate_computational_cost(self, hypothesis_type: HypothesisType) -> float:
        """
        Оценить вычислительную стоимость гипотезы.
        
        Args:
            hypothesis_type: Тип гипотезы
        
        Returns:
            Вычислительная стоимость (0-1)
        """
        costs = {
            HypothesisType.FEATURE_DECAY: 0.3,
            HypothesisType.STRATEGY_DEGRADATION: 0.4,
            HypothesisType.REGIME_CHANGE: 0.5,
            HypothesisType.EXECUTION_LOSS: 0.2,
            HypothesisType.CORRELATION_CHANGE: 0.3,
            HypothesisType.NEW_SIGNAL: 0.6,
            HypothesisType.MODEL_IMPROVEMENT: 0.7,
            HypothesisType.RISK_OPTIMIZATION: 0.3,
        }
        
        return costs.get(hypothesis_type, 0.5)
    
    def calculate_priority_score(
        self,
        expected_value_of_information: float,
        potential_trading_impact: float,
        uncertainty: float,
        data_availability: float,
        computational_cost: float
    ) -> float:
        """
        Рассчитать оценку приоритета.
        
        Args:
            expected_value_of_information: Ожидаемая ценность информации
            potential_trading_impact: Потенциальное влияние на торговлю
            uncertainty: Неопределённость
            data_availability: Доступность данных
            computational_cost: Вычислительная стоимость
        
        Returns:
            Оценка приоритета
        """
        # Рассчитать оценку по весам
        score = (
            self.priority_weights["expected_value_of_information"] * expected_value_of_information +
            self.priority_weights["potential_trading_impact"] * potential_trading_impact +
            self.priority_weights["uncertainty"] * uncertainty +
            self.priority_weights["data_availability"] * data_availability
        )
        
        # Учесть вычислительную стоимость
        score = score * (1 - computational_cost * 0.5)
        
        return min(1.0, max(0.0, score))
    
    def prioritize_hypotheses(
        self,
        hypotheses: list[Hypothesis]
    ) -> list[ResearchPriority]:
        """
        Приоритизировать гипотезы.
        
        Args:
            hypotheses: Список гипотез
        
        Returns:
            Приоритизированный список
        """
        # Отсортировать по оценке приоритета
        sorted_hypotheses = sorted(
            hypotheses,
            key=lambda h: h.priority_score,
            reverse=True
        )
        
        # Создать список с рангами
        priorities = []
        for rank, hypothesis in enumerate(sorted_hypotheses, 1):
            priorities.append(ResearchPriority(
                hypothesis=hypothesis,
                score=hypothesis.priority_score,
                rank=rank
            ))
        
        return priorities
    
    def generate_research_plan(
        self,
        current_knowledge: dict[str, Any],
        system_state: dict[str, Any],
        max_hypotheses: int = 5
    ) -> list[ResearchPriority]:
        """
        Сгенерировать план исследований.
        
        Args:
            current_knowledge: Текущие знания системы
            system_state: Состояние системы
            max_hypotheses: Максимальное количество гипотез
        
        Returns:
            План исследований
        """
        # Определить пробелы в знаниях
        knowledge_gaps = self.identify_knowledge_gaps(current_knowledge, system_state)
        
        # Сгенерировать гипотезы
        hypotheses = self.generate_hypotheses(
            knowledge_gaps, current_knowledge, system_state
        )
        
        # Приоритизировать гипотезы
        priorities = self.prioritize_hypotheses(hypotheses)
        
        # Вернуть топ гипотезы
        return priorities[:max_hypotheses]
    
    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        """
        Получить гипотезу.
        
        Args:
            hypothesis_id: Идентификатор гипотезы
        
        Returns:
            Гипотеза или None
        """
        return self.hypotheses.get(hypothesis_id)
    
    def update_hypothesis(
        self,
        hypothesis_id: str,
        status: str,
        results: dict[str, Any] | None = None
    ) -> Hypothesis | None:
        """
        Обновить гипотезу.
        
        Args:
            hypothesis_id: Идентификатор гипотезы
            status: Новый статус
            results: Результаты
        
        Returns:
            Обновлённая гипотеза или None
        """
        if hypothesis_id not in self.hypotheses:
            return None
        
        hypothesis = self.hypotheses[hypothesis_id]
        hypothesis.status = status
        hypothesis.updated_at = datetime.now()
        
        # Сохранить результаты (если есть)
        if results:
            hypothesis.to_dict()["results"] = results
        
        return hypothesis
    
    def cleanup_old_hypotheses(self, max_age_days: int = 90) -> int:
        """
        Очистить старые гипотезы.
        
        Args:
            max_age_days: Максимальный возраст в днях
        
        Returns:
            Количество удалённых гипотез
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        hypotheses_to_remove = [
            hyp_id for hyp_id, hyp in self.hypotheses.items()
            if hyp.created_at < cutoff
        ]
        
        for hyp_id in hypotheses_to_remove:
            del self.hypotheses[hyp_id]
        
        return len(hypotheses_to_remove)


# Глобальный экземпляр Hypothesis Generator
_hypothesis_generator: HypothesisGenerator | None = None


def get_hypothesis_generator() -> HypothesisGenerator:
    """Получить глобальный Hypothesis Generator"""
    global _hypothesis_generator
    if _hypothesis_generator is None:
        _hypothesis_generator = HypothesisGenerator()
    return _hypothesis_generator


def reset_hypothesis_generator():
    """Сбросить Hypothesis Generator (для тестов)"""
    global _hypothesis_generator
    _hypothesis_generator = HypothesisGenerator()
