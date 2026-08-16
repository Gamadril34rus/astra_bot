"""Непрерывное дообучение модели по накопленным урокам."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .model_trainer import ModelTrainer, TrainingConfig, TrainingData

logger = logging.getLogger(__name__)
DEFAULT_MODEL_PATH = Path("models/current.pkl")
DEFAULT_LESSONS_PATH = Path("models/lessons.jsonl")

# Совместимость со старым self-play и внешними импортами.
FEATURE_COLUMNS = [
    "return_1h",
    "return_4h",
    "return_24h",
    "sma20_gap",
    "atr_pct",
    "rsi",
    "volume_ratio",
    "confidence",
    "cross_btc_1h",
    "cross_eth_1h",
    "cross_sol_1h",
]


@dataclass
class WeeklyLearningResult:
    version: str
    n_samples: int
    positive_rate: float
    roc_auc: float
    accuracy: float
    model_path: Path
    trained: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "n_samples": self.n_samples,
            "positive_rate": self.positive_rate,
            "roc_auc": self.roc_auc,
            "accuracy": self.accuracy,
            "model_path": str(self.model_path),
            "trained": self.trained,
            "message": self.message,
        }


def load_lessons(path: Path = DEFAULT_LESSONS_PATH) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def lesson_feature_columns(lessons: list[dict]) -> list[str]:
    """Union feature schema всех накопленных уроков, стабильная сортировка."""
    columns: set[str] = set()
    for lesson in lessons:
        features = lesson.get("features") or {}
        columns.update(str(k) for k in features.keys())
    return sorted(columns)


def lessons_to_training_data(lessons: list[dict]) -> TrainingData:
    if not lessons:
        raise ValueError("Нет уроков для обучения")
    columns = lesson_feature_columns(lessons)
    if not columns:
        raise ValueError("Уроки не содержат признаков")

    X = np.zeros((len(lessons), len(columns)), dtype=float)
    y = np.zeros(len(lessons), dtype=int)
    for i, lesson in enumerate(lessons):
        feats = lesson.get("features") or {}
        for j, col in enumerate(columns):
            try:
                X[i, j] = float(feats.get(col, 0.0))
            except (TypeError, ValueError):
                X[i, j] = 0.0
        y[i] = 1 if lesson.get("outcome") == "win" else 0

    return TrainingData(
        features=X,
        labels=y,
        feature_names=columns,
        metadata={
            "n_samples": len(lessons),
            "positive_rate": float(np.mean(y)),
            "source": "continuous_lessons",
        },
    )


def train_weekly(
    lessons_path: Path = DEFAULT_LESSONS_PATH,
    model_path: Path = DEFAULT_MODEL_PATH,
    min_samples: int = 200,
    model_type: str = "lightgbm",
) -> WeeklyLearningResult:
    """Переобучить модель, когда накоплена достаточная выборка."""
    try:
        from .live_lessons import merge_into_main_lessons
        merge_into_main_lessons(main_path=lessons_path)
    except Exception as exc:
        logger.debug("live-lessons merge skipped: %s", exc)

    lessons = load_lessons(lessons_path)
    if len(lessons) < min_samples:
        msg = f"Собрано {len(lessons)} уроков, нужно минимум {min_samples}"
        return WeeklyLearningResult(
            version="none", n_samples=len(lessons), positive_rate=0.0,
            roc_auc=0.0, accuracy=0.0, model_path=model_path,
            trained=False, message=msg,
        )

    dataset = lessons_to_training_data(lessons)
    trainer = ModelTrainer(TrainingConfig(model_type=model_type))
    version = "ML-continuous-" + datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    model = trainer.train(dataset, model_type=model_type)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(model_path), version=version)

    return WeeklyLearningResult(
        version=version,
        n_samples=dataset.n_samples,
        positive_rate=float(np.mean(dataset.labels)),
        roc_auc=float(getattr(model.metrics, "roc_auc", 0.0) or 0.0),
        accuracy=float(getattr(model.metrics, "accuracy", 0.0) or 0.0),
        model_path=model_path,
        trained=True,
        message=f"Модель {version} обучена на {dataset.n_samples} уроках",
    )
