"""
ASTRA BOT — Experiment Registry

Регистрация экспериментов (Master Specification v2, Section 43-44)

Каждый experiment должен фиксировать:
- experiment_id
- hypothesis
- dataset
- parameters
- train_period
- validation_period
- OOS_period
- results

Исследование должно быть полностью воспроизводимым.

Immutable experiments: После публикации результата experiment не изменять.
Исправление Experiment #1847 создаёт Experiment #1847-v2, а не переписывает историю.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DatasetInfo:
    """Информация о наборе данных"""
    name: str
    description: str
    source: str
    start_date: datetime
    end_date: datetime
    features: list[str]
    target: str | None = None
    size: int = 0
    hash: str = ""
    
    def calculate_hash(self) -> str:
        """Рассчитать хэш набора данных"""
        data_str = f"{self.name}{self.source}{self.start_date}{self.end_date}{self.features}{self.target}"
        return hashlib.md5(data_str.encode()).hexdigest()
    
    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["start_date"] = self.start_date.isoformat()
        result["end_date"] = self.end_date.isoformat()
        return result


@dataclass
class ExperimentParameters:
    """Параметры эксперимента"""
    parameters: dict[str, Any] = field(default_factory=dict)
    model_type: str = ""
    model_version: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentPeriod:
    """Период эксперимента"""
    train_start: datetime
    train_end: datetime
    validation_start: datetime | None = None
    validation_end: datetime | None = None
    test_start: datetime | None = None
    test_end: datetime | None = None
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
        }
        if self.validation_start:
            result["validation_start"] = self.validation_start.isoformat()
        if self.validation_end:
            result["validation_end"] = self.validation_end.isoformat()
        if self.test_start:
            result["test_start"] = self.test_start.isoformat()
        if self.test_end:
            result["test_end"] = self.test_end.isoformat()
        return result


@dataclass
class ExperimentMetrics:
    """Метрики эксперимента"""
    # Основные метрики
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1_score: float | None = None
    
    # Финансовые метрики
    total_return: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    profit_factor: float | None = None
    
    # Статистические метрики
    p_value: float | None = None
    r_squared: float | None = None
    
    # Метрики OOS
    oos_accuracy: float | None = None
    oos_sharpe: float | None = None
    oos_max_drawdown: float | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ExperimentResult:
    """Результат эксперимента"""
    metrics: ExperimentMetrics
    predictions: list[Any] | None = None
    actuals: list[Any] | None = None
    model_artifact: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "metrics": self.metrics.to_dict(),
        }
        if self.model_artifact:
            result["model_artifact"] = self.model_artifact
        return result


@dataclass
class Experiment:
    """Эксперимент"""
    experiment_id: str
    hypothesis: str
    dataset: DatasetInfo
    parameters: ExperimentParameters
    periods: ExperimentPeriod
    results: ExperimentResult | None = None
    
    # Статус
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED
    
    # Метаданные
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    
    # Версия
    version: int = 1
    parent_experiment_id: str | None = None
    
    # Воспроизводимость
    code_commit: str = ""
    feature_version: str = ""
    data_hash: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "experiment_id": self.experiment_id,
            "hypothesis": self.hypothesis,
            "dataset": self.dataset.to_dict(),
            "parameters": self.parameters.to_dict(),
            "periods": self.periods.to_dict(),
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "version": self.version,
            "code_commit": self.code_commit,
            "feature_version": self.feature_version,
            "data_hash": self.data_hash,
        }
        
        if self.results:
            result["results"] = self.results.to_dict()
        if self.completed_at:
            result["completed_at"] = self.completed_at.isoformat()
        if self.parent_experiment_id:
            result["parent_experiment_id"] = self.parent_experiment_id
        
        return result
    
    def calculate_data_hash(self) -> str:
        """Рассчитать хэш данных"""
        return self.dataset.calculate_hash()


class ExperimentRegistry:
    """
    Реестр экспериментов.
    
    Хранит все эксперименты и обеспечивает их воспроизводимость.
    """
    
    def __init__(self):
        # Хранение экспериментов
        self.experiments: dict[str, Experiment] = {}
        
        # Счётчик экспериментов
        self.experiment_counter = 0
        
        # Текущие эксперименты
        self.running_experiments: dict[str, Experiment] = {}
    
    def generate_experiment_id(self) -> str:
        """Сгенерировать идентификатор эксперимента"""
        self.experiment_counter += 1
        return f"EXP_{self.experiment_counter:05d}"
    
    def register_experiment(
        self,
        hypothesis: str,
        dataset: DatasetInfo,
        parameters: ExperimentParameters,
        periods: ExperimentPeriod,
        code_commit: str = "",
        feature_version: str = "",
    ) -> Experiment:
        """
        Зарегистрировать новый эксперимент.
        
        Args:
            hypothesis: Гипотеза
            dataset: Набор данных
            parameters: Параметры
            periods: Периоды
            code_commit: Коммит кода
            feature_version: Версия факторов
        
        Returns:
            Experiment
        """
        experiment_id = self.generate_experiment_id()
        
        # Рассчитать хэш данных
        data_hash = dataset.calculate_hash()
        
        experiment = Experiment(
            experiment_id=experiment_id,
            hypothesis=hypothesis,
            dataset=dataset,
            parameters=parameters,
            periods=periods,
            code_commit=code_commit,
            feature_version=feature_version,
            data_hash=data_hash,
        )
        
        self.experiments[experiment_id] = experiment
        self.running_experiments[experiment_id] = experiment
        
        logger.info(f"Registered experiment {experiment_id}: {hypothesis}")
        
        return experiment
    
    def update_experiment(
        self,
        experiment_id: str,
        results: ExperimentResult,
        status: str = "COMPLETED"
    ) -> Experiment | None:
        """
        Обновить эксперимент результатами.
        
        Args:
            experiment_id: Идентификатор эксперимента
            results: Результаты
            status: Статус
        
        Returns:
            Обновлённый эксперимент или None
        """
        if experiment_id not in self.experiments:
            logger.warning(f"Experiment {experiment_id} not found")
            return None
        
        experiment = self.experiments[experiment_id]
        
        # Эксперименты неизменяемы! Создаём новую версию
        new_version = experiment.version + 1
        
        new_experiment = Experiment(
            experiment_id=f"{experiment_id}-v{new_version}",
            hypothesis=experiment.hypothesis,
            dataset=experiment.dataset,
            parameters=experiment.parameters,
            periods=experiment.periods,
            results=results,
            status=status,
            created_at=experiment.created_at,
            updated_at=datetime.now(),
            completed_at=datetime.now(),
            version=new_version,
            parent_experiment_id=experiment.experiment_id,
            code_commit=experiment.code_commit,
            feature_version=experiment.feature_version,
            data_hash=experiment.data_hash,
        )
        
        # Сохранить новую версию
        self.experiments[new_experiment.experiment_id] = new_experiment
        
        # Удалить из текущих
        if experiment_id in self.running_experiments:
            del self.running_experiments[experiment_id]
        
        logger.info(f"Updated experiment {experiment_id} -> {new_experiment.experiment_id}")
        
        return new_experiment
    
    def get_experiment(self, experiment_id: str) -> Experiment | None:
        """
        Получить эксперимент.
        
        Args:
            experiment_id: Идентификатор эксперимента
        
        Returns:
            Experiment или None
        """
        return self.experiments.get(experiment_id)
    
    def get_experiments_by_hypothesis(self, hypothesis: str) -> list[Experiment]:
        """
        Получить эксперименты по гипотезе.
        
        Args:
            hypothesis: Гипотеза
        
        Returns:
            Список экспериментов
        """
        return [
            exp for exp in self.experiments.values()
            if exp.hypothesis == hypothesis
        ]
    
    def get_experiments_by_status(self, status: str) -> list[Experiment]:
        """
        Получить эксперименты по статусу.
        
        Args:
            status: Статус
        
        Returns:
            Список экспериментов
        """
        return [
            exp for exp in self.experiments.values()
            if exp.status == status
        ]
    
    def get_experiments_by_dataset(self, dataset_name: str) -> list[Experiment]:
        """
        Получить эксперименты по набору данных.
        
        Args:
            dataset_name: Имя набора данных
        
        Returns:
            Список экспериментов
        """
        return [
            exp for exp in self.experiments.values()
            if exp.dataset.name == dataset_name
        ]
    
    def search_experiments(
        self,
        hypothesis: str | None = None,
        status: str | None = None,
        dataset_name: str | None = None,
        limit: int = 100
    ) -> list[Experiment]:
        """
        Поиск экспериментов.
        
        Args:
            hypothesis: Гипотеза
            status: Статус
            dataset_name: Имя набора данных
            limit: Лимит результатов
        
        Returns:
            Список экспериментов
        """
        results = []
        
        for exp in self.experiments.values():
            if hypothesis and exp.hypothesis != hypothesis:
                continue
            if status and exp.status != status:
                continue
            if dataset_name and exp.dataset.name != dataset_name:
                continue
            
            results.append(exp)
            
            if len(results) >= limit:
                break
        
        return results
    
    def get_statistics(self) -> dict[str, Any]:
        """
        Получить статистику по экспериментам.
        
        Returns:
            Статистика
        """
        total = len(self.experiments)
        by_status = {}
        by_dataset = {}
        
        for exp in self.experiments.values():
            # По статусу
            if exp.status not in by_status:
                by_status[exp.status] = 0
            by_status[exp.status] += 1
            
            # По набору данных
            if exp.dataset.name not in by_dataset:
                by_dataset[exp.dataset.name] = 0
            by_dataset[exp.dataset.name] += 1
        
        return {
            "total_experiments": total,
            "by_status": by_status,
            "by_dataset": by_dataset,
            "running_experiments": len(self.running_experiments),
        }
    
    def cleanup_old_experiments(self, max_age_days: int = 365) -> int:
        """
        Очистить старые эксперименты.
        
        Args:
            max_age_days: Максимальный возраст в днях
        
        Returns:
            Количество удалённых экспериментов
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        experiments_to_remove = [
            exp_id for exp_id, exp in self.experiments.items()
            if exp.created_at < cutoff
        ]
        
        for exp_id in experiments_to_remove:
            del self.experiments[exp_id]
        
        return len(experiments_to_remove)


# Глобальный экземпляр Experiment Registry
_experiment_registry: ExperimentRegistry | None = None


def get_experiment_registry() -> ExperimentRegistry:
    """Получить глобальный Experiment Registry"""
    global _experiment_registry
    if _experiment_registry is None:
        _experiment_registry = ExperimentRegistry()
    return _experiment_registry


def reset_experiment_registry():
    """Сбросить Experiment Registry (для тестов)"""
    global _experiment_registry
    _experiment_registry = ExperimentRegistry()
