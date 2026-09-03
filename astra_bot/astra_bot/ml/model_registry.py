"""
ASTRA BOT — Model Registry
Реестр ML моделей
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .model_trainer import MLModel, ModelMetrics, TrainingConfig

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """Информация о модели в реестре"""
    version: str
    model_type: str = "lightgbm"
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "development"  # development, validated, production, deprecated
    metrics: ModelMetrics | None = None
    feature_names: list[str] = field(default_factory=list)
    config: TrainingConfig | None = None
    model_path: str | None = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    # --- TZ §18-22: evidence for the promotion chain and A/B/stress ---
    status_log: list[dict[str, str]] = field(default_factory=list)
    sample_size: int = 0
    expectancy: float | None = None
    oos_expectancy: float | None = None
    walk_forward_expectancy: float | None = None
    stress_metrics: dict[str, Any] = field(default_factory=dict)
    rollback_reason: str | None = None

    @property
    def is_production(self) -> bool:
        return self.status == "production"

    @property
    def is_validated(self) -> bool:
        return self.status in ["validated", "production"]

    def to_dict(self) -> dict:
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
            "status_log": self.status_log,
            "sample_size": self.sample_size,
            "expectancy": self.expectancy,
            "oos_expectancy": self.oos_expectancy,
            "walk_forward_expectancy": self.walk_forward_expectancy,
            "stress_metrics": self.stress_metrics,
            "rollback_reason": self.rollback_reason,
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
        self._models: dict[str, ModelInfo] = {}

        # Текущая production модель
        self._production_model: str | None = None

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
                        status_log=info.get("status_log", []),
                        sample_size=int(info.get("sample_size", 0)),
                        expectancy=info.get("expectancy"),
                        oos_expectancy=info.get("oos_expectancy"),
                        walk_forward_expectancy=info.get(
                            "walk_forward_expectancy"
                        ),
                        stress_metrics=info.get("stress_metrics", {}),
                        rollback_reason=info.get("rollback_reason"),
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
            row = info.to_dict()
            row["created_at"] = info.created_at.isoformat()
            row["production"] = version == self._production_model
            data["models"][version] = {
                "model_type": info.model_type,
                "created_at": info.created_at.isoformat(),
                "status": info.status,
                "description": info.description,
                "tags": info.tags,
                "model_path": info.model_path,
                "production": version == self._production_model,
                "status_log": info.status_log,
                "sample_size": info.sample_size,
                "expectancy": info.expectancy,
                "oos_expectancy": info.oos_expectancy,
                "walk_forward_expectancy": info.walk_forward_expectancy,
                "stress_metrics": info.stress_metrics,
                "rollback_reason": info.rollback_reason,
            }

        with open(registry_file, "w") as f:
            json.dump(data, f, indent=2)

    def register(
        self,
        model: MLModel,
        version: str = None,
        description: str = "",
        tags: list[str] = None,
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
            v for v in self._models
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

    def get_model(self, version: str) -> ModelInfo | None:
        """Получить информацию о модели"""
        return self._models.get(version)

    def get_production_model(self) -> ModelInfo | None:
        """Получить текущую production модель"""
        if not self._production_model:
            return None
        return self._models.get(self._production_model)

    def list_models(
        self,
        status: str = None,
        model_type: str = None,
    ) -> list[ModelInfo]:
        """Список моделей с фильтрами"""
        models = list(self._models.values())

        if status:
            models = [m for m in models if m.status == status]

        if model_type:
            models = [m for m in models if m.model_type == model_type]

        return sorted(models, key=lambda m: m.created_at, reverse=True)

    def delete_model(self, version: str, reason: str = "") -> bool:
        """Мягкое удаление: модель уходит в ``deprecated``, файл и история
        ХРАНИТСЯ (TZ §18: старые версии нужны для rollback)."""
        if version not in self._models:
            return False

        model_info = self._models[version]
        model_info.status = "deprecated"
        model_info.status_log.append({
            "at": datetime.utcnow().isoformat(),
            "status": "deprecated",
            "reason": f"delete_model: {reason}" if reason else "delete_model",
        })
        self._save_registry()

        logger.info(f"Soft-deleted (deprecated) model: {version}")
        return True

    # ------------------------------------------------------------------
    # TZ §18-22: цепочка продвижения, A/B, stress, rollback
    # ------------------------------------------------------------------

    PROMOTION_CHAIN = ["development", "validated", "production", "deprecated"]

    def set_stress_metrics(self, version: str, stress: dict[str, Any]) -> None:
        """Записать stress-результаты (fees×2, slippage×2/×3, Monte Carlo).

        Ожидается ключ ``stable`` (bool) — модель с ``stable: False``
        никогда не поднимается до production автоматически (TZ §22)."""
        if version not in self._models:
            raise ValueError(f"Model not found: {version}")
        self._models[version].stress_metrics = dict(stress)
        self._save_registry()

    def set_evaluation(
        self,
        version: str,
        *,
        sample_size: int = 0,
        expectancy: float | None = None,
        oos_expectancy: float | None = None,
        walk_forward_expectancy: float | None = None,
    ) -> None:
        """Записать оценки (OOS/walk-forward expectancy) для продвижения."""
        if version not in self._models:
            raise ValueError(f"Model not found: {version}")
        info = self._models[version]
        if sample_size:
            info.sample_size = sample_size
        if expectancy is not None:
            info.expectancy = expectancy
        if oos_expectancy is not None:
            info.oos_expectancy = oos_expectancy
        if walk_forward_expectancy is not None:
            info.walk_forward_expectancy = walk_forward_expectancy
        self._save_registry()

    def _transition(self, info: ModelInfo, status: str, reason: str) -> None:
        info.status = status
        info.status_log.append({
            "at": datetime.utcnow().isoformat(),
            "status": status,
            "reason": reason,
        })

    def promote(
        self,
        version: str,
        target_status: str,
        evidence: dict[str, Any] | None = None,
        min_samples: int = 20,
    ) -> tuple[bool, str]:
        """Продвижение по цепочке с гейтами (TZ §18/§22).

        development -> validated: есть метрики и sample size.
        validated -> production: OOS > 0, walk-forward > 0, stress stable,
        A/B не хуже текущей production (иначе нужен override_reason).
        Возвращает (ok, reason).
        """
        if version not in self._models:
            return False, f"Model not found: {version}"
        if target_status not in self.PROMOTION_CHAIN:
            return False, f"unknown status: {target_status}"
        info = self._models[version]
        cur = info.status
        evidence = evidence or {}

        if target_status == "validated" and cur == "development":
            if info.sample_size < min_samples:
                return False, (
                    f"sample_size {info.sample_size} < min_samples {min_samples}"
                )
            if info.metrics is None and not info.expectancy:
                return False, "нет метрик для валидации"
            self._transition(info, "validated", evidence.get("reason", ""))
            self._save_registry()
            return True, ""

        if target_status == "production" and cur == "validated":
            if (info.oos_expectancy or 0) <= 0:
                return False, "OOS expectancy <= 0 — нет доказательства"
            if (info.walk_forward_expectancy or 0) <= 0:
                return False, "walk-forward expectancy <= 0"
            if not info.stress_metrics or info.stress_metrics.get("stable") is not True:
                return False, "нет stable stress test (TZ §22: UNSTABLE не ACTIVE)"
            # A/B против текущей production (если есть): challenger не хуже.
            if self._production_model and self._production_model != version:
                base = self._models[self._production_model]
                if base.expectancy is not None and info.expectancy is not None:
                    if info.expectancy < base.expectancy:
                        if not evidence.get("override_reason"):
                            return False, (
                                f"A/B: expectancy {info.expectancy:.3f} < "
                                f"production {base.expectancy:.3f} "
                                "(нужен override_reason)"
                            )
                        self._transition(
                            info, "production",
                            f"override: {evidence['override_reason']}",
                        )
                    else:
                        self._transition(info, "production", "A/B: не хуже production")
                else:
                    self._transition(info, "production", "A/B: без базовых метрик")
            else:
                self._transition(info, "production", evidence.get("reason", ""))
            # Предыдущая production -> deprecated (файл и история сохраняются).
            if self._production_model and self._production_model != version:
                old = self._models[self._production_model]
                self._transition(old, "deprecated", "заменена новой production")
            self._production_model = version
            self._save_registry()
            return True, ""

        return False, (
            f"переход {cur} -> {target_status} запрещён "
            f"(цепочка: {' -> '.join(self.PROMOTION_CHAIN)})"
        )

    def ab_compare(
        self, base_version: str, challenger_version: str, min_samples: int = 20
    ) -> dict[str, Any]:
        """A/B сравнение (TZ §23): expectancy + sample size, без магии."""
        base = self._models.get(base_version)
        chal = self._models.get(challenger_version)
        if base is None or chal is None:
            return {"verdict": "error", "reason": "model not found"}
        bn, cn = base.sample_size, chal.sample_size
        b_exp = base.expectancy or 0.0
        c_exp = chal.expectancy or 0.0
        insufficient = bn < min_samples or cn < min_samples
        if c_exp > b_exp:
            verdict = "challenger_wins"
        elif c_exp < b_exp:
            verdict = "base_wins"
        else:
            verdict = "tie"
        return {
            "base_version": base_version,
            "challenger_version": challenger_version,
            "base_expectancy": b_exp,
            "challenger_expectancy": c_exp,
            "delta": c_exp - b_exp,
            "base_n": bn,
            "challenger_n": cn,
            "insufficient_samples": insufficient,
            "verdict": verdict,
        }

    def rollback(self, target_version: str, reason: str = "") -> tuple[bool, str]:
        """Rollback на предыдущую версию (TZ §18).

        Текущая production -> deprecated; target (не deprecated) ->
        production. Возвращает (ok, previous_version)."""
        if target_version not in self._models:
            return False, f"Model not found: {target_version}"
        target = self._models[target_version]
        if target_version == self._production_model:
            return False, "уже production"
        # Deprecated делится на «снят при замене» (можно вернуть) и
        # «удалён пользователем» (нельзя): последнее событие в истории —
        # delete_model => заблокировано.
        if target.status == "deprecated":
            last = target.status_log[-1] if target.status_log else {}
            if str(last.get("reason", "")).startswith("delete_model"):
                return False, (
                    f"нельзя rollback на удалённую версию: {target_version}"
                )
        previous = self._production_model
        if previous:
            self._transition(
                self._models[previous], "deprecated",
                f"rollback -> {target_version}: {reason}",
            )
        target.rollback_reason = reason
        self._transition(target, "production", f"rollback: {reason}")
        self._production_model = target_version
        self._save_registry()
        return True, previous or ""

    def validate_model(
        self,
        version: str,
        min_metrics: dict[str, float] = None,
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

    def get_registry_stats(self) -> dict[str, Any]:
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
_registry: ModelRegistry | None = None


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
