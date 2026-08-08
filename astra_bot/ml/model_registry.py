"""
ASTRA BOT — Model Registry
Реестр ML моделей
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path
import json

from .model_trainer import MLModel, ModelMetrics, TrainingConfig

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """Информация о модели в реестре"""
    version: str
    model_type: str = "lightgbm"
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "development"  # development, validated, production, deprecated
    metrics: Optional[ModelMetrics] = None
    feature_names: List[str] = field(default_factory=list)
    config: Optional[TrainingConfig] = None
    model_path: Optional[str] = None
    description: str = ""
    tags: List[str] = field(default_factory=list)
    
    @property
    def is_production(self) -> bool:
        return self.status == "production"
    
    @property
    def is_validated(self) -> bool:
        return self.status in ["validated", "production"]
    
    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "model_type": self.model_type,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
            "metrics": {
                "accuracy": self.metrics.accuracy if self.metrics else None,
                "precision": self.metrics.precision if self.metrics else None,
                "recall": self.metrics.recall if self.metrics else None,
                "f1_score": self.metrics.f1_score if self.metrics else None,
                "roc_auc": self.metrics.roc_auc if self.metrics else None,
            } if self.metrics else None,
            "feature_count": len(self.feature_names),
            "description": self.description,
            "tags": self.tags,
        }


class ModelRegistry:
    """
    Реестр ML моделей.
    
    Управляет версиями моделей:
    - Регистрация новых моделей
    - Валидация перед production
    - Управление production версией
    - История всех версий
    """
    
    def __init__(self, registry_dir: str = "models/registry"):
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        
        # Словарь моделей
        self._models: Dict[str, ModelInfo] = {}
        
        # Текущая production модель
        self._production_model: Optional[str] = None
        
        # Загрузка существующих моделей
        self._load_registry()
    
    def _load_registry(self):
        """Загрузить реестр из файлов"""
        registry_file = self.registry_dir / "registry.json"
        
        if registry_file.exists():
            try:
                with open(registry_file) as f:
                    data = json.load(f)
                
                for version, info in data.get("models", {}).items():
                    model_info = ModelInfo(
                        version=version,
                        model_type=info.get("model_type", "lightgbm"),
                        created_at=datetime.fromisoformat(info["created_at"]),
                        status=info.get("status", "development"),
                        description=info.get("description", ""),
                        tags=info.get("tags", []),
                        model_path=info.get("model_path"),
                    )
                    self._models[version] = model_info
                    
                    if info.get("production", False):
                        self._production_model = version
                
                logger.info(f"Loaded registry with {len(self._models)} models")
            except Exception as e:
                logger.warning(f"Failed to load registry: {e}")
    
    def _save_registry(self):
        """Сохранить реестр в файл"""
        registry_file = self.registry_dir / "registry.json"
        
        data = {
            "models": {},
            "production_model": self._production_model,
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        for version, info in self._models.items():
            data["models"][version] = {
                "model_type": info.model_type,
                "created_at": info.created_at.isoformat(),
                "status": info.status,
                "description": info.description,
                "tags": info.tags,
                "model_path": info.model_path,
                "production": version == self._production_model,
            }
        
        with open(registry_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def register(
        self,
        model: MLModel,
        version: str = None,
        description: str = "",
        tags: List[str] = None,
    ) -> ModelInfo:
        """
        Зарегистрировать новую модель.
        
        Args:
            model: Обученная модель
            version: Версия модели (auto-generated если None)
            description: Описание
            tags: Теги
        
        Returns:
            ModelInfo зарегистрированной модели
        """
        if version is None:
            version = self._generate_version()
        
        if version in self._models:
            raise ValueError(f"Model version already exists: {version}")
        
        model_path = self.registry_dir / f"{version}.pkl"
        
        # Сохраняем модель
        model.save(str(model_path))
        
        model_info = ModelInfo(
            version=version,
            model_type=model.config.model_type if model.config else "lightgbm",
            status="development",
            metrics=model.metrics,
            feature_names=model.feature_names,
            config=model.config,
            model_path=str(model_path),
            description=description,
            tags=tags or [],
        )
        
        self._models[version] = model_info
        self._save_registry()
        
        logger.info(f"Registered model: {version}")
        return model_info
    
    def _generate_version(self) -> str:
        """Сгенерировать версию модели"""
        # Формат: ML-YYYYMMDD-NNN
        today = datetime.utcnow().strftime("%Y%m%d")
        
        # Находим максимальный номер для сегодня
        existing_versions = [
            v for v in self._models.keys()
            if v.startswith(f"ML-{today}")
        ]
        
        if not existing_versions:
            return f"ML-{today}-001"
        
        max_num = max(
            int(v.split("-")[-1]) for v in existing_versions
        )
        
        return f"ML-{today}-{max_num + 1:03d}"
    
    def promote_to_production(self, version: str) -> bool:
        """
        Повысить модель до production.
        
        Args:
            version: Версия модели
        
        Returns:
            True если успешно
        """
        if version not in self._models:
            raise ValueError(f"Model not found: {version}")
        
        model_info = self._models[version]
        
        # Проверка что модель валидирована
        if not model_info.is_validated:
            raise ValueError(
                f"Cannot promote unvalidated model: {version}. "
                f"Status: {model_info.status}"
            )
        
        # Deprecate current production
        if self._production_model:
            self._models[self._production_model].status = "deprecated"
        
        # Устанавливаем новую production
        model_info.status = "production"
        self._production_model = version
        self._save_registry()
        
        logger.info(f"Promoted model {version} to production")
        return True
    
    def demote_from_production(self, version: str) -> bool:
        """Снять модель с production"""
        if version != self._production_model:
            return False
        
        self._models[version].status = "validated"
        self._production_model = None
        self._save_registry()
        
        logger.info(f"Demoted model {version} from production")
        return True
    
    def get_model(self, version: str) -> Optional[ModelInfo]:
        """Получить информацию о модели"""
        return self._models.get(version)
    
    def get_production_model(self) -> Optional[ModelInfo]:
        """Получить текущую production модель"""
        if not self._production_model:
            return None
        return self._models.get(self._production_model)
    
    def list_models(
        self,
        status: str = None,
        model_type: str = None,
    ) -> List[ModelInfo]:
        """Список моделей с фильтрами"""
        models = list(self._models.values())
        
        if status:
            models = [m for m in models if m.status == status]
        
        if model_type:
            models = [m for m in models if m.model_type == model_type]
        
        return sorted(models, key=lambda m: m.created_at, reverse=True)
    
    def delete_model(self, version: str) -> bool:
        """Удалить модель"""
        if version not in self._models:
            return False
        
        # Нельзя удалить production модель
        if version == self._production_model:
            raise ValueError("Cannot delete production model")
        
        # Удаляем файл
        model_info = self._models[version]
        if model_info.model_path and Path(model_info.model_path).exists():
            Path(model_info.model_path).unlink()
        
        del self._models[version]
        self._save_registry()
        
        logger.info(f"Deleted model: {version}")
        return True
    
    def validate_model(
        self,
        version: str,
        min_metrics: Dict[str, float] = None,
    ) -> bool:
        """
        Валидировать модель.
        
        Args:
            version: Версия модели
            min_metrics: Минимальные метрики
        
        Returns:
            True если модель прошла валидацию
        """
        if version not in self._models:
            raise ValueError(f"Model not found: {version}")
        
        model_info = self._models[version]
        
        if not model_info.metrics:
            raise ValueError("Model has no metrics")
        
        # Проверка минимальных метрик
        min_metrics = min_metrics or {
            "accuracy": 0.50,
            "precision": 0.50,
            "recall": 0.40,
            "f1_score": 0.45,
            "roc_auc": 0.52,
        }
        
        for metric_name, min_value in min_metrics.items():
            actual_value = getattr(model_info.metrics, metric_name, 0)
            if actual_value < min_value:
                logger.warning(
                    f"Model {version} failed validation: "
                    f"{metric_name}={actual_value:.3f} < {min_value}"
                )
                return False
        
        # Обновляем статус
        model_info.status = "validated"
        self._save_registry()
        
        logger.info(f"Model {version} validated successfully")
        return True
    
    def get_registry_stats(self) -> Dict[str, Any]:
        """Статистика реестра"""
        models = list(self._models.values())
        
        return {
            "total_models": len(models),
            "by_status": {
                status: sum(1 for m in models if m.status == status)
                for status in ["development", "validated", "production", "deprecated"]
            },
            "by_type": {
                m.model_type: sum(1 for m in models if m.model_type == m.model_type)
                for m in models
            },
            "production_version": self._production_model,
            "latest_models": [
                m.version for m in models[:5]
            ],
        }


# Глобальный реестр
_registry: Optional[ModelRegistry] = None


def get_registry() -> ModelRegistry:
    """Получить глобальный реестр моделей"""
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry


def reset_registry():
    """Сбросить реестр (для тестов)"""
    global _registry
    _registry = None
