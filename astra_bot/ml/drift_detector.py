"""
ASTRA BOT — Drift Detector
Детекция дрейфа ML модели
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from .model_trainer import ModelMetrics

logger = logging.getLogger(__name__)


@dataclass
class DriftConfig:
    """Конфигурация детекции дрейфа"""
    # Пороги
    accuracy_drop_threshold: float = 0.05  # 5% падение accuracy
    roc_auc_drop_threshold: float = 0.05   # 5% падение ROC-AUC
    prediction_drift_threshold: float = 0.1  # 10% изменение распределения предсказаний

    # Окна
    performance_window_size: int = 100  # Количество свежих предсказаний для оценки
    reference_window_size: int = 500     # Размер reference окна

    # Частота проверки
    check_interval_samples: int = 50  # Проверять каждые N предсказаний

    # Автоматические действия
    auto_detect: bool = True
    auto_alert: bool = True
    auto_retrain: bool = False  # Только если явно разрешено


@dataclass
class DriftDetectionResult:
    """Результат детекции дрейфа"""
    is_drift_detected: bool = False
    drift_type: str = ""  # performance, data, concept
    severity: str = "none"  # none, low, medium, high

    # Метрики
    current_accuracy: float | None = None
    reference_accuracy: float | None = None
    accuracy_drop: float | None = None

    current_roc_auc: float | None = None
    reference_roc_auc: float | None = None
    roc_auc_drop: float | None = None

    prediction_distribution_change: float | None = None

    # Время
    detected_at: datetime = field(default_factory=datetime.utcnow)
    samples_since_last_check: int = 0

    reasons: list[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return self.severity in ["medium", "high"]

    @property
    def needs_retrain(self) -> bool:
        return self.severity == "high"

    def to_dict(self) -> dict:
        return {
            "is_drift_detected": self.is_drift_detected,
            "drift_type": self.drift_type,
            "severity": self.severity,
            "current_accuracy": self.current_accuracy,
            "reference_accuracy": self.reference_accuracy,
            "accuracy_drop": self.accuracy_drop,
            "current_roc_auc": self.current_roc_auc,
            "reference_roc_auc": self.reference_roc_auc,
            "roc_auc_drop": self.roc_auc_drop,
            "prediction_distribution_change": self.prediction_distribution_change,
            "samples_since_last_check": self.samples_since_last_check,
            "reasons": self.reasons,
        }


class DriftDetector:
    """
    Детектор дрейфа ML модели.

    Мониторит:
    - Performance drift (падение accuracy, ROC-AUC)
    - Data drift (изменение распределения входных данных)
    - Concept drift (изменение взаимосвязи features-targets)
    """

    def __init__(self, config: DriftConfig = None):
        self.config = config or DriftConfig()

        # История производительности
        self._performance_history: deque = deque(maxlen=self.config.performance_window_size)
        self._reference_performance: ModelMetrics | None = None

        # История предсказаний
        self._prediction_history: deque = deque(maxlen=self.config.reference_window_size * 2)

        # Счётчик для проверки
        self._samples_since_check = 0

        # Статус
        self._last_drift_result: DriftDetectionResult | None = None
        self._drift_detected = False

    def update_reference_performance(self, metrics: ModelMetrics):
        """Обновить reference производительность"""
        self._reference_performance = metrics
        logger.info(
            f"Reference performance updated: "
            f"accuracy={metrics.accuracy:.3f}, "
            f"roc_auc={metrics.roc_auc:.3f}"
        )

    def record_prediction(
        self,
        prediction: int,
        probability: float,
        actual_outcome: int | None = None,
        features: np.ndarray | None = None,
    ):
        """
        Записать предсказание для мониторинга.

        Args:
            prediction: Предсказанный класс (0 или 1)
            probability: Вероятность предсказания
            actual_outcome: Фактический результат (если известен)
            features: Использованные признаки
        """
        self._prediction_history.append({
            "prediction": prediction,
            "probability": probability,
            "actual_outcome": actual_outcome,
            "features": features,
            "timestamp": datetime.utcnow(),
        })

        self._samples_since_check += 1

    def check_drift(self, current_metrics: ModelMetrics = None) -> DriftDetectionResult:
        """
        Проверить наличие дрейфа.

        Args:
            current_metrics: Текущие метрики модели (если доступны)

        Returns:
            DriftDetectionResult
        """
        result = DriftDetectionResult()

        # Нужно достаточно данных
        if len(self._prediction_history) < self.config.check_interval_samples:
            result.samples_since_last_check = self._samples_since_check
            return result

        # === Performance Drift ===
        if current_metrics:
            self._check_performance_drift(current_metrics, result)

        # === Data Drift ===
        self._check_data_drift(result)

        # === Резюме ===
        result.samples_since_last_check = self._samples_since_check
        result.detected_at = datetime.utcnow()

        if result.is_drift_detected:
            self._drift_detected = True
            logger.warning(
                f"Drift detected: {result.drift_type}, "
                f"severity={result.severity}"
            )
        else:
            self._drift_detected = False

        self._last_drift_result = result
        self._samples_since_check = 0

        return result

    def _check_performance_drift(
        self,
        current_metrics: ModelMetrics,
        result: DriftDetectionResult,
    ):
        """Проверить performance drift"""
        if not self._reference_performance:
            # Первая установка reference
            self._reference_performance = current_metrics
            return

        # Accuracy drop
        accuracy_drop = (
            self._reference_performance.accuracy - current_metrics.accuracy
        )

        if accuracy_drop > self.config.accuracy_drop_threshold:
            result.is_drift_detected = True
            result.drift_type = "performance"
            result.severity = "high" if accuracy_drop > 0.1 else "medium"
            result.current_accuracy = current_metrics.accuracy
            result.reference_accuracy = self._reference_performance.accuracy
            result.accuracy_drop = accuracy_drop
            result.reasons.append(
                f"Accuracy dropped by {accuracy_drop:.3f}"
            )

        # ROC-AUC drop
        roc_auc_drop = (
            self._reference_performance.roc_auc - current_metrics.roc_auc
        )

        if roc_auc_drop > self.config.roc_auc_drop_threshold:
            result.is_drift_detected = True
            result.drift_type = "performance"
            result.severity = "high" if roc_auc_drop > 0.1 else "medium"
            result.current_roc_auc = current_metrics.roc_auc
            result.reference_roc_auc = self._reference_performance.roc_auc
            result.roc_auc_drop = roc_auc_drop
            result.reasons.append(
                f"ROC-AUC dropped by {roc_auc_drop:.3f}"
            )

        # Обновляем reference если всё в порядке
        if not result.is_drift_detected:
            # Можно постепенно обновлять reference
            pass

    def _check_data_drift(self, result: DriftDetectionResult):
        """Проверить data drift"""
        if len(self._prediction_history) < self.config.reference_window_size:
            return

        # Разделяем на reference и current окна
        ref_window = list(self._prediction_history)[
            :self.config.reference_window_size
        ]
        current_window = list(self._prediction_history)[
            -self.config.reference_window_size:
        ]

        if len(ref_window) < 50 or len(current_window) < 50:
            return

        # Сравниваем распределения вероятностей
        ref_probs = np.array([p["probability"] for p in ref_window])
        current_probs = np.array([p["probability"] for p in current_window])

        # Статистический тест (упрощённый)
        ref_mean = np.mean(ref_probs)
        current_mean = np.mean(current_probs)
        ref_std = np.std(ref_probs)

        if ref_std > 0:
            distribution_change = abs(current_mean - ref_mean) / ref_std

            if distribution_change > self.config.prediction_drift_threshold:
                result.is_drift_detected = True
                result.drift_type = result.drift_type or "data"
                result.prediction_distribution_change = float(distribution_change)

                if not result.reasons:
                    result.reasons.append(
                        f"Prediction distribution changed by {distribution_change:.2f} std"
                    )

    @property
    def is_drift_detected(self) -> bool:
        """Обнаружен ли дрейф"""
        return self._drift_detected

    @property
    def last_result(self) -> DriftDetectionResult | None:
        """Последний результат проверки"""
        return self._last_drift_result

    def get_stats(self) -> dict[str, Any]:
        """Получить статистику детектора"""
        return {
            "is_drift_detected": self._drift_detected,
            "samples_recorded": len(self._prediction_history),
            "samples_since_check": self._samples_since_check,
            "reference_performance": {
                "accuracy": self._reference_performance.accuracy
                if self._reference_performance else None,
                "roc_auc": self._reference_performance.roc_auc
                if self._reference_performance else None,
            } if self._reference_performance else None,
            "config": {
                "accuracy_drop_threshold": self.config.accuracy_drop_threshold,
                "roc_auc_drop_threshold": self.config.roc_auc_drop_threshold,
                "prediction_drift_threshold": self.config.prediction_drift_threshold,
                "check_interval_samples": self.config.check_interval_samples,
            },
        }

    def reset(self):
        """Сбросить детектор"""
        self._performance_history.clear()
        self._prediction_history.clear()
        self._reference_performance = None
        self._last_drift_result = None
        self._drift_detected = False
        self._samples_since_check = 0
        logger.info("Drift detector reset")


# Глобальный детектор
_detector: DriftDetector | None = None


def get_drift_detector() -> DriftDetector:
    """Получить глобальный детектор дрейфа"""
    global _detector
    if _detector is None:
        _detector = DriftDetector()
    return _detector


def reset_drift_detector():
    """Сбросить детектор (для тестов)"""
    global _detector
    _detector = None
