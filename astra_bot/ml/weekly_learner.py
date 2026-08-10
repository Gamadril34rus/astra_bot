"""
ASTRA BOT — Weekly learner.

Замыкает цикл обучения «без депозита»:

1. Читает уроки, накопленные walk-forward self-play (models/lessons.jsonl).
2. Если накоплено >= min_samples уроков — обучает LightGBM на актуальных
   признаках (включая кросс-рыночные и новостные).
3. Сохраняет модель как ``models/current.pkl`` и регистрирует версию
   ``ML-weekly-YYYYMMDD``.
4. Self-play на следующем проходе использует эту модель как фильтр:
       pred = predictor.predict_probability(features)
       входит в сделку только если ``pred >= min_prob``.

Так каждый вечер/утро недельного цикла модель «вспоминает» свои ошибки
(через lessons) и «решает», стоит ли входить в следующую ставку.
"""

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

# Признаки, которые ждёт модель. Должны совпадать с набором, который
# формирует self-play._feature_snapshot.
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
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def lessons_to_training_data(lessons: list[dict]) -> TrainingData:
    """Превратить уроки self-play в TrainingData для LightGBM."""
    if not lessons:
        raise ValueError("Нет уроков для обучения")

    X = np.zeros((len(lessons), len(FEATURE_COLUMNS)), dtype=float)
    y = np.zeros(len(lessons), dtype=int)

    for i, lesson in enumerate(lessons):
        feats = lesson.get("features") or {}
        for j, col in enumerate(FEATURE_COLUMNS):
            X[i, j] = float(feats.get(col, 0.0))
        y[i] = 1 if lesson.get("outcome") == "win" else 0

    return TrainingData(
        features=X,
        labels=y,
        feature_names=list(FEATURE_COLUMNS),
        metadata={
            "n_samples": len(lessons),
            "positive_rate": float(np.mean(y)),
            "source": "weekly_self_play",
        },
    )


def train_weekly(
    lessons_path: Path = DEFAULT_LESSONS_PATH,
    model_path: Path = DEFAULT_MODEL_PATH,
    min_samples: int = 200,
    model_type: str = "lightgbm",
) -> WeeklyLearningResult:
    """Обучить/обновить модель из lessons.jsonl."""
    lessons = load_lessons(lessons_path)
    if len(lessons) < min_samples:
        msg = (
            f"Собрано {len(lessons)} уроков, нужно минимум {min_samples} — "
            f"модель пока не переобучается"
        )
        logger.info(msg)
        return WeeklyLearningResult(
            version="none",
            n_samples=len(lessons),
            positive_rate=0.0,
            roc_auc=0.0,
            accuracy=0.0,
            model_path=model_path,
            trained=False,
            message=msg,
        )

    dataset = lessons_to_training_data(lessons)
    config = TrainingConfig(model_type=model_type)
    trainer = ModelTrainer(config)
    model = trainer.train(dataset, model_type=model_type)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(model_path))

    version = "ML-weekly-" + datetime.now(tz=UTC).strftime("%Y%m%d-%H%M")
    roc_auc = float(getattr(model.metrics, "roc_auc", 0.0) or 0.0)
    accuracy = float(getattr(model.metrics, "accuracy", 0.0) or 0.0)

    logger.info(
        "Weekly model trained: version=%s samples=%d auc=%.3f acc=%.3f",
        version,
        dataset.n_samples,
        roc_auc,
        accuracy,
    )
    return WeeklyLearningResult(
        version=version,
        n_samples=dataset.n_samples,
        positive_rate=float(np.mean(dataset.labels)),
        roc_auc=roc_auc,
        accuracy=accuracy,
        model_path=model_path,
        trained=True,
        message=f"Модель {version} обучена на {dataset.n_samples} уроках",
    )
