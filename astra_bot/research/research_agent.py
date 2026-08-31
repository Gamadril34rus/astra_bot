"""
ASTRA BOT — Research Agent 2.0

Автономный исследовательский агент (Master Specification v2, Section 49-51)

Research Agent должен анализировать:
- current knowledge
- unknowns
- failed hypotheses
- degrading features
- strategy degradation
- market regime changes
- execution losses

И самостоятельно формировать следующие исследования.

Цель: Постоянное улучшение системы через autonomous research.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .experiment_registry import ExperimentRegistry, get_experiment_registry
from .hypothesis_generator import HypothesisGenerator, get_hypothesis_generator
from .statistical_tests import StatisticalTests, get_statistical_tests

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeBase:
    """База знаний"""
    # Текущие знания
    active_features: list[str] = field(default_factory=list)
    active_strategies: list[str] = field(default_factory=list)
    active_models: list[str] = field(default_factory=list)
    
    # Деградирующие компоненты
    degrading_features: dict[str, float] = field(default_factory=dict)  # feature -> decay rate
    degrading_strategies: dict[str, float] = field(default_factory=dict)  # strategy -> decay rate
    degrading_models: dict[str, float] = field(default_factory=dict)  # model -> decay rate
    
    # Неудачные эксперименты
    failed_experiments: list[str] = field(default_factory=list)
    failed_hypotheses: list[str] = field(default_factory=list)
    
    # Успешные открытия
    successful_discoveries: list[str] = field(default_factory=list)
    
    # Статистика
    total_experiments: int = 0
    successful_experiments: int = 0
    
    # Временная метка
    last_updated: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "active_features": self.active_features,
            "active_strategies": self.active_strategies,
            "active_models": self.active_models,
            "degrading_features": self.degrading_features,
            "degrading_strategies": self.degrading_strategies,
            "degrading_models": self.degrading_models,
            "failed_experiments": self.failed_experiments,
            "failed_hypotheses": self.failed_hypotheses,
            "successful_discoveries": self.successful_discoveries,
            "total_experiments": self.total_experiments,
            "successful_experiments": self.successful_experiments,
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class ResearchPlan:
    """План исследований"""
    hypothesis_id: str
    experiment_id: str
    priority: int
    expected_impact: float
    estimated_cost: float
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id,
            "priority": self.priority,
            "expected_impact": self.expected_impact,
            "estimated_cost": self.estimated_cost,
        }


@dataclass
class ResearchResult:
    """Результат исследования"""
    experiment_id: str
    hypothesis_id: str
    success: bool
    findings: dict[str, Any]
    
    # Влияние на систему
    impact_on_features: dict[str, float] = field(default_factory=dict)
    impact_on_strategies: dict[str, float] = field(default_factory=dict)
    impact_on_models: dict[str, float] = field(default_factory=dict)
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "success": self.success,
            "findings": self.findings,
            "timestamp": self.timestamp.isoformat(),
        }
        
        if self.impact_on_features:
            result["impact_on_features"] = self.impact_on_features
        if self.impact_on_strategies:
            result["impact_on_strategies"] = self.impact_on_strategies
        if self.impact_on_models:
            result["impact_on_models"] = self.impact_on_models
        
        return result


@dataclass
class SystemState:
    """Состояние системы"""
    # Рыночные условия
    current_regime: str = ""
    regime_confidence: float = 0.0
    regime_changes: int = 0
    
    # Выполнение
    execution_losses: int = 0
    execution_quality: float = 0.0
    
    # Данные
    data_coverage: float = 0.0
    data_quality: float = 0.0
    data_availability: dict[str, float] = field(default_factory=dict)
    sample_sizes: dict[str, int] = field(default_factory=dict)
    
    # Неизвестные режимы
    unknown_regimes: int = 0
    
    # Корреляции
    signal_correlation: float = 0.0
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "current_regime": self.current_regime,
            "regime_confidence": self.regime_confidence,
            "regime_changes": self.regime_changes,
            "execution_losses": self.execution_losses,
            "execution_quality": self.execution_quality,
            "data_coverage": self.data_coverage,
            "data_quality": self.data_quality,
            "unknown_regimes": self.unknown_regimes,
            "signal_correlation": self.signal_correlation,
            "timestamp": self.timestamp.isoformat(),
        }
        
        if self.data_availability:
            result["data_availability"] = self.data_availability
        if self.sample_sizes:
            result["sample_sizes"] = self.sample_sizes
        
        return result


class ResearchAgent:
    """
    Автономный исследовательский агент.
    
    Анализирует текущее состояние системы и знания,
    генерирует гипотезы и планирует эксперименты.
    """
    
    def __init__(self):
        # Компоненты
        self.knowledge_base = KnowledgeBase()
        self.hypothesis_generator = get_hypothesis_generator()
        self.experiment_registry = get_experiment_registry()
        self.statistical_tests = get_statistical_tests()
        
        # История исследований
        self.research_history: list[ResearchResult] = []
        
        # Текущий план исследований
        self.current_plan: list[ResearchPlan] = []
        
        # Параметры
        self.max_active_experiments = 5
        self.max_research_cost = 0.5  # Максимальная стоимость исследований (0-1)
    
    def update_knowledge_base(
        self,
        new_knowledge: dict[str, Any]
    ) -> None:
        """
        Обновить базу знаний.
        
        Args:
            new_knowledge: Новые знания
        """
        # Обновить активные компоненты
        if "active_features" in new_knowledge:
            self.knowledge_base.active_features = new_knowledge["active_features"]
        
        if "active_strategies" in new_knowledge:
            self.knowledge_base.active_strategies = new_knowledge["active_strategies"]
        
        if "active_models" in new_knowledge:
            self.knowledge_base.active_models = new_knowledge["active_models"]
        
        # Обновить деградирующие компоненты
        if "degrading_features" in new_knowledge:
            self.knowledge_base.degrading_features.update(new_knowledge["degrading_features"])
        
        if "degrading_strategies" in new_knowledge:
            self.knowledge_base.degrading_strategies.update(new_knowledge["degrading_strategies"])
        
        if "degrading_models" in new_knowledge:
            self.knowledge_base.degrading_models.update(new_knowledge["degrading_models"])
        
        # Обновить статистику
        if "total_experiments" in new_knowledge:
            self.knowledge_base.total_experiments = new_knowledge["total_experiments"]
        
        if "successful_experiments" in new_knowledge:
            self.knowledge_base.successful_experiments = new_knowledge["successful_experiments"]
        
        self.knowledge_base.last_updated = datetime.now()
    
    def update_system_state(
        self,
        system_state: SystemState
    ) -> None:
        """
        Обновить состояние системы.
        
        Args:
            system_state: Состояние системы
        """
        self.system_state = system_state
    
    def analyze_current_state(self) -> dict[str, Any]:
        """
        Проанализировать текущее состояние.
        
        Returns:
            Анализ состояния
        """
        analysis = {
            "knowledge_base": self.knowledge_base.to_dict(),
            "system_state": self.system_state.to_dict() if hasattr(self, 'system_state') else {},
        }
        
        # Определить пробелы в знаниях
        knowledge_gaps = self.hypothesis_generator.identify_knowledge_gaps(
            self.knowledge_base.to_dict(),
            self.system_state.to_dict() if hasattr(self, 'system_state') else {}
        )
        
        analysis["knowledge_gaps"] = [gap.to_dict() for gap in knowledge_gaps]
        
        return analysis
    
    def generate_research_plan(self) -> list[ResearchPlan]:
        """
        Сгенерировать план исследований.
        
        Returns:
            План исследований
        """
        # Проанализировать текущее состояние
        analysis = self.analyze_current_state()
        
        # Сгенерировать приоритеты исследований
        priorities = self.hypothesis_generator.generate_research_plan(
            self.knowledge_base.to_dict(),
            self.system_state.to_dict() if hasattr(self, 'system_state') else {},
            max_hypotheses=10
        )
        
        # Создать план исследований
        research_plan = []
        total_cost = 0.0
        
        for priority in priorities:
            hypothesis = priority.hypothesis
            
            # Проверить ограничения
            if len(research_plan) >= self.max_active_experiments:
                break
            
            if total_cost + hypothesis.computational_cost > self.max_research_cost:
                continue
            
            # Зарегистрировать эксперимент
            experiment = self.experiment_registry.register_experiment(
                hypothesis=hypothesis.title,
                dataset=self._create_dataset_for_hypothesis(hypothesis),
                parameters=self._create_parameters_for_hypothesis(hypothesis),
                periods=self._create_periods_for_hypothesis(hypothesis),
                code_commit="",
                feature_version=""
            )
            
            # Добавить в план
            research_plan.append(ResearchPlan(
                hypothesis_id=hypothesis.hypothesis_id,
                experiment_id=experiment.experiment_id,
                priority=priority.rank,
                expected_impact=hypothesis.potential_trading_impact,
                estimated_cost=hypothesis.computational_cost
            ))
            
            total_cost += hypothesis.computational_cost
        
        self.current_plan = research_plan
        
        return research_plan
    
    def _create_dataset_for_hypothesis(
        self,
        hypothesis: Any
    ) -> Any:
        """
        Создать набор данных для гипотезы.
        
        Args:
            hypothesis: Гипотеза
        
        Returns:
            DatasetInfo
        """
        from ..engines.uncertainty_engine import MarketDataQuality
        
        # Упрощённая версия
        from .experiment_registry import DatasetInfo
        
        return DatasetInfo(
            name=f"dataset_{hypothesis.hypothesis_id}",
            description=f"Dataset for hypothesis: {hypothesis.title}",
            source="market_data",
            start_date=datetime.now() - timedelta(days=365),
            end_date=datetime.now(),
            features=["returns", "volatility", "volume"],
            target="returns",
            size=1000,
            hash=""
        )
    
    def _create_parameters_for_hypothesis(
        self,
        hypothesis: Any
    ) -> Any:
        """
        Создать параметры для гипотезы.
        
        Args:
            hypothesis: Гипотеза
        
        Returns:
            ExperimentParameters
        """
        from .experiment_registry import ExperimentParameters
        
        return ExperimentParameters(
            parameters={"hypothesis_type": hypothesis.hypothesis_type.value},
            model_type="research",
            model_version="1.0"
        )
    
    def _create_periods_for_hypothesis(
        self,
        hypothesis: Any
    ) -> Any:
        """
        Создать периоды для гипотезы.
        
        Args:
            hypothesis: Гипотеза
        
        Returns:
            ExperimentPeriod
        """
        from .experiment_registry import ExperimentPeriod
        
        end_date = datetime.now()
        train_start = end_date - timedelta(days=365 * 2)  # 2 года
        train_end = end_date - timedelta(days=180)  # 6 месяцев назад
        validation_start = train_end
        validation_end = end_date - timedelta(days=90)  # 3 месяца назад
        test_start = validation_end
        test_end = end_date
        
        return ExperimentPeriod(
            train_start=train_start,
            train_end=train_end,
            validation_start=validation_start,
            validation_end=validation_end,
            test_start=test_start,
            test_end=test_end
        )
    
    def execute_research_plan(self) -> list[ResearchResult]:
        """
        Выполнить план исследований.
        
        Returns:
            Результаты исследований
        """
        if not self.current_plan:
            self.generate_research_plan()
        
        results = []
        
        for plan in self.current_plan:
            # Выполнить эксперимент (упрощённая версия)
            # В реальной системе это будет вызов метода эксперимента
            result = self._execute_experiment(plan.experiment_id, plan.hypothesis_id)
            results.append(result)
        
        return results
    
    def _execute_experiment(
        self,
        experiment_id: str,
        hypothesis_id: str
    ) -> ResearchResult:
        """
        Выполнить эксперимент.
        
        Args:
            experiment_id: Идентификатор эксперимента
            hypothesis_id: Идентификатор гипотезы
        
        Returns:
            Результат исследования
        """
        # Получить эксперимент
        experiment = self.experiment_registry.get_experiment(experiment_id)
        
        if not experiment:
            return ResearchResult(
                experiment_id=experiment_id,
                hypothesis_id=hypothesis_id,
                success=False,
                findings={"error": "Experiment not found"}
            )
        
        # Упрощённое выполнение эксперимента
        # В реальной системе это будет сложный процесс
        
        # Сгенерировать случайные результаты (для демонстрации)
        success = np.random.random() > 0.3  # 70% вероятность успеха
        
        if success:
            findings = {
                "status": "success",
                "metrics": {
                    "sharpe_ratio": np.random.uniform(0.5, 2.0),
                    "p_value": np.random.uniform(0.0, 0.1),
                    "r_squared": np.random.uniform(0.3, 0.9)
                }
            }
            
            # Обновить знания
            self.knowledge_base.successful_experiments += 1
            self.knowledge_base.successful_discoveries.append(experiment_id)
        else:
            findings = {
                "status": "failed",
                "reason": "No significant results found"
            }
            
            # Обновить неудачные эксперименты
            self.knowledge_base.failed_experiments.append(experiment_id)
        
        # Обновить эксперимент
        self.experiment_registry.update_experiment(
            experiment_id,
            results=None,  # В реальной системе будут результаты
            status="COMPLETED" if success else "FAILED"
        )
        
        # Обновить гипотезу
        self.hypothesis_generator.update_hypothesis(
            hypothesis_id,
            status="VALIDATED" if success else "INVALIDATED",
            results=findings
        )
        
        result = ResearchResult(
            experiment_id=experiment_id,
            hypothesis_id=hypothesis_id,
            success=success,
            findings=findings
        )
        
        self.research_history.append(result)
        
        return result
    
    def learn_from_results(self) -> dict[str, Any]:
        """
        Обучение на основе результатов исследований.
        
        Returns:
            Выводы из обучения
        """
        lessons = {
            "new_knowledge": [],
            "updated_strategies": [],
            "retired_components": [],
        }
        
        # Проанализировать результаты исследований
        for result in self.research_history[-10:]:  # Последние 10 результатов
            if result.success:
                # Успешный эксперимент
                experiment = self.experiment_registry.get_experiment(result.experiment_id)
                if experiment:
                    lessons["new_knowledge"].append({
                        "experiment_id": result.experiment_id,
                        "hypothesis": experiment.hypothesis,
                        "findings": result.findings
                    })
            else:
                # Неудачный эксперимент
                hypothesis = self.hypothesis_generator.get_hypothesis(result.hypothesis_id)
                if hypothesis:
                    lessons["retired_components"].append({
                        "hypothesis_id": result.hypothesis_id,
                        "title": hypothesis.title,
                        "reason": result.findings.get("reason", "No significant results")
                    })
        
        return lessons
    
    def get_research_summary(self) -> dict[str, Any]:
        """
        Получить сводку исследований.
        
        Returns:
            Сводка исследований
        """
        return {
            "knowledge_base": self.knowledge_base.to_dict(),
            "current_plan": [plan.to_dict() for plan in self.current_plan],
            "research_history": [result.to_dict() for result in self.research_history[-20:]],
            "statistics": self.experiment_registry.get_statistics(),
            "lessons_learned": self.learn_from_results()
        }
    
    def cleanup_old_data(self, max_age_days: int = 90) -> dict[str, int]:
        """
        Очистить старые данные.
        
        Args:
            max_age_days: Максимальный возраст в днях
        
        Returns:
            Количество удалённых записей
        """
        cleaned = {
            "experiments": self.experiment_registry.cleanup_old_experiments(max_age_days),
            "hypotheses": self.hypothesis_generator.cleanup_old_hypotheses(max_age_days),
            "research_results": 0
        }
        
        # Очистить старые результаты исследований
        cutoff = datetime.now() - timedelta(days=max_age_days)
        results_to_remove = [
            i for i, result in enumerate(self.research_history)
            if result.timestamp < cutoff
        ]
        
        for i in sorted(results_to_remove, reverse=True):
            del self.research_history[i]
        
        cleaned["research_results"] = len(results_to_remove)
        
        return cleaned


# Глобальный экземпляр Research Agent
_research_agent: ResearchAgent | None = None


def get_research_agent() -> ResearchAgent:
    """Получить глобальный Research Agent"""
    global _research_agent
    if _research_agent is None:
        _research_agent = ResearchAgent()
    return _research_agent


def reset_research_agent():
    """Сбросить Research Agent (для тестов)"""
    global _research_agent
    _research_agent = ResearchAgent()
