"""
ASTRA BOT — ML Model Trainer
Обучение ML моделей
"""

import logging
import pickle
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..core import models

# ML-зависимости опциональны: подтягиваются лениво, чтобы бот запускался без
# установленного scikit-learn/lightgbm (например, в минимальном прод-образе).
try:
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )
    from sklearn.model_selection import (
        StratifiedKFold,
        cross_val_score,
        train_test_split,
    )
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover - зависимости опциональны
    SKLEARN_AVAILABLE = False

    def _missing_sklearn(*_args, **_kwargs):
        raise ImportError(
            "scikit-learn is required for ML training. "
            "Install with `pip install scikit-learn`."
        )

    train_test_split = cross_val_score = StratifiedKFold = _missing_sklearn
    accuracy_score = precision_score = recall_score = f1_score = _missing_sklearn
    roc_auc_score = classification_report = confusion_matrix = _missing_sklearn
    StandardScaler = _missing_sklearn

try:
    import lightgbm as lgb

    LIGHTGBM_AVAILABLE = True
except ImportError:  # pragma: no cover
    LIGHTGBM_AVAILABLE = False
    lgb = None

logger = logging.getLogger(__name__)


@dataclass
class TrainingConfig:
    """Конфигурация обучения"""
    # Модель
    model_type: str = "lightgbm"  # lightgbm, xgboost, random_forest
    n_estimators: int = 100
    max_depth: int = 5
    learning_rate: float = 0.1
    min_samples_split: int = 20
    min_samples_leaf: int = 10

    # Валидация
    test_size: float = 0.2
    cv_folds: int = 5
    random_state: int = 42

    # Настройки
    target_column: str = "target"
    feature_columns: list[str] = field(default_factory=list)

    # Пути
    model_dir: str = "models"
    model_filename: str = "ml_model.pkl"

    # Early stopping
    early_stopping_rounds: int = 10
    verbose: int = 0

    def __post_init__(self):
        # Создать директорию для моделей
        Path(self.model_dir).mkdir(parents=True, exist_ok=True)

    @property
    def model_path(self) -> str:
        """Путь к файлу модели"""
        return str(Path(self.model_dir) / self.model_filename)


@dataclass
class TrainingData:
    """Данные для обучения"""
    features: np.ndarray
    labels: np.ndarray
    feature_names: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def n_samples(self) -> int:
        return len(self.labels)

    @property
    def n_features(self) -> int:
        return self.features.shape[1]

    @property
    def class_distribution(self) -> dict[int, int]:
        """Распределение классов"""
        unique, counts = np.unique(self.labels, return_counts=True)
        return dict(zip(unique, counts, strict=False))

    def split(
        self,
        test_size: float = 0.2,
        random_state: int = 42,
    ) -> tuple["TrainingData", "TrainingData"]:
        """Разделить на train/test"""
        X_train, X_test, y_train, y_test = train_test_split(
            self.features,
            self.labels,
            test_size=test_size,
            random_state=random_state,
            stratify=self.labels,
        )

        train_data = TrainingData(
            features=X_train,
            labels=y_train,
            feature_names=self.feature_names,
            metadata={"split": "train"},
        )

        test_data = TrainingData(
            features=X_test,
            labels=y_test,
            feature_names=self.feature_names,
            metadata={"split": "test"},
        )

        return train_data, test_data


@dataclass
class ModelMetrics:
    """Метрики модели"""
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    roc_auc: float = 0.0
    confusion_matrix: list[list[int]] = field(default_factory=list)
    classification_report: str = ""
    feature_importance: dict[str, float] = field(default_factory=dict)
    training_time_seconds: float = 0.0
    cv_scores: list[float] = field(default_factory=list)

    @property
    def is_good(self) -> bool:
        """Хорошая ли модель"""
        return self.roc_auc > 0.55 and self.f1_score > 0.5

    @property
    def is_profitable(self) -> bool:
        """Модель с положительным ожиданием"""
        # Упрощённая оценка: если precision > recall, модель более консервативна
        return self.precision > 0.5 and self.roc_auc > 0.5


class MLModel:
    """ML модель"""

    def __init__(
        self,
        model: Any = None,
        config: TrainingConfig = None,
        metrics: ModelMetrics = None,
        feature_names: list[str] = None,
    ):
        self.model = model
        self.config = config or TrainingConfig()
        self.metrics = metrics or ModelMetrics()
        self.feature_names = feature_names or []
        self.is_fitted = model is not None
        # Версия обучения (заполняется при save()).
        self.version: str = ""
        self.saved_at: str = ""

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Предсказать"""
        if not self.is_fitted:
            raise ValueError("Model not fitted")
        return self.model.predict(features)

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Предсказать вероятности"""
        if not self.is_fitted:
            raise ValueError("Model not fitted")

        if hasattr(self.model, "predict_proba"):
            return self.model.predict_proba(features)
        else:
            # Для моделей без predict_proba
            predictions = self.predict(features)
            proba = np.zeros((len(predictions), 2))
            proba[:, 0] = 1 - predictions
            proba[:, 1] = predictions
            return proba

    def predict_probability(self, features: np.ndarray) -> float:
        """Предсказать вероятность класса 1"""
        proba = self.predict_proba(features)
        if len(proba.shape) == 2:
            return float(proba[0, 1])
        return float(proba[0])

    def get_feature_importance(self) -> dict[str, float]:
        """Получить важность признаков"""
        if not self.is_fitted:
            return {}

        if hasattr(self.model, "feature_importances_"):
            importances = self.model.feature_importances_
            return {
                name: float(imp)
                for name, imp in zip(
                    self.feature_names, importances, strict=False
                )
            }
        return {}

    def save(self, path: str, version: str | None = None):
        """Сохранить модель.

        ``version`` может быть передана вызывающим кодом (weekly learner)
        и затем доступна как ``MLModel.version`` после загрузки.
        """
        from datetime import datetime

        version = version or self.version or ""
        saved_at = datetime.now(UTC).isoformat()
        model_data = {
            "model": self.model,
            "config": self.config,
            "metrics": self.metrics,
            "feature_names": self.feature_names,
            "is_fitted": self.is_fitted,
            "version": version,
            "saved_at": saved_at,
        }
        with open(path, "wb") as f:
            pickle.dump(model_data, f)
        self.version = version
        self.saved_at = saved_at
        logger.info("Model saved to %s (version=%s)", path, version)

    @classmethod
    def load(cls, path: str) -> "MLModel":
        """Загрузить модель"""
        with open(path, "rb") as f:
            model_data = pickle.load(f)

        model = cls(
            model=model_data["model"],
            config=model_data.get("config"),
            metrics=model_data.get("metrics"),
            feature_names=model_data.get("feature_names", []),
        )
        model.is_fitted = model_data.get("is_fitted", False)
        model.version = model_data.get("version", "")
        model.saved_at = model_data.get("saved_at", "")

        logger.info("Model loaded from %s (version=%s)", path, model.version)
        return model


class ModelTrainer:
    """
    Тренировщик ML моделей.

    Поддерживает:
    - LightGBM (рекомендуется для старта)
    - XGBoost
    - Random Forest
    """

    def __init__(self, config: TrainingConfig = None):
        self.config = config or TrainingConfig()

    def train(
        self,
        training_data: TrainingData,
        model_type: str = None,
    ) -> MLModel:
        """
        Обучить модель.

        Args:
            training_data: Данные для обучения
            model_type: Тип модели (по умолчанию из config)

        Returns:
            Обученная MLModel
        """
        model_type = model_type or self.config.model_type

        if not SKLEARN_AVAILABLE:
            raise ImportError(
                "scikit-learn is required for model training. "
                "Install with `pip install scikit-learn`."
            )
        if model_type == "lightgbm" and not LIGHTGBM_AVAILABLE:
            raise ImportError(
                "lightgbm is required for LightGBM training. "
                "Install with `pip install lightgbm`."
            )

        logger.info(f"Training {model_type} model with {training_data.n_samples} samples")

        start_time = datetime.utcnow()

        # Разделение на train/test
        train_data, test_data = training_data.split(
            test_size=self.config.test_size,
            random_state=self.config.random_state,
        )

        # Создание модели
        model = self._create_model(model_type)

        # Обучение
        if model_type == "lightgbm":
            self._train_lightgbm(model, train_data, test_data)
        elif model_type == "xgboost":
            self._train_xgboost(model, train_data, test_data)
        elif model_type == "random_forest":
            self._train_random_forest(model, train_data, test_data)
        else:
            raise ValueError(f"Unknown model type: {model_type}")

        # Расчёт метрик
        metrics = self._evaluate_model(model, train_data, test_data)

        training_time = (datetime.utcnow() - start_time).total_seconds()
        metrics.training_time_seconds = training_time

        logger.info(
            f"Training completed in {training_time:.1f}s: "
            f"accuracy={metrics.accuracy:.3f}, "
            f"roc_auc={metrics.roc_auc:.3f}"
        )

        return MLModel(
            model=model,
            config=self.config,
            metrics=metrics,
            feature_names=training_data.feature_names,
        )

    def _create_model(self, model_type: str) -> Any:
        """Создать модель"""
        if model_type == "lightgbm":
            return lgb.LGBMClassifier(
                n_estimators=self.config.n_estimators,
                max_depth=self.config.max_depth,
                learning_rate=self.config.learning_rate,
                min_samples_split=self.config.min_samples_split,
                min_samples_leaf=self.config.min_samples_leaf,
                verbose=self.config.verbose,
                random_state=self.config.random_state,
                n_jobs=-1,
            )
        elif model_type == "xgboost":
            try:
                import xgboost as xgb
                return xgb.XGBClassifier(
                    n_estimators=self.config.n_estimators,
                    max_depth=self.config.max_depth,
                    learning_rate=self.config.learning_rate,
                    min_child_weight=self.config.min_samples_leaf,
                    random_state=self.config.random_state,
                    n_jobs=-1,
                    use_label_encoder=False,
                    eval_metric="logloss",
                )
            except ImportError:
                logger.warning("XGBoost not available, falling back to LightGBM")
                return lgb.LGBMClassifier(
                    n_estimators=self.config.n_estimators,
                    max_depth=self.config.max_depth,
                    learning_rate=self.config.learning_rate,
                    random_state=self.config.random_state,
                    n_jobs=-1,
                )
        elif model_type == "random_forest":
            from sklearn.ensemble import RandomForestClassifier
            return RandomForestClassifier(
                n_estimators=self.config.n_estimators,
                max_depth=self.config.max_depth,
                min_samples_split=self.config.min_samples_split,
                min_samples_leaf=self.config.min_samples_leaf,
                random_state=self.config.random_state,
                n_jobs=-1,
            )
        else:
            # По умолчанию LightGBM
            return lgb.LGBMClassifier(
                n_estimators=self.config.n_estimators,
                max_depth=self.config.max_depth,
                learning_rate=self.config.learning_rate,
                random_state=self.config.random_state,
                n_jobs=-1,
            )

    def _train_lightgbm(
        self,
        model: "lgb.LGBMClassifier",
        train_data: TrainingData,
        test_data: TrainingData,
    ):
        """Обучить LightGBM sklearn-совместимую модель.

        LGBMClassifier.fit() принимает массивы X/y, а не ``lgb.Dataset``.
        Dataset используется только в low-level API ``lgb.train``.
        """
        eval_set = [(test_data.features, test_data.labels)]
        fit_kwargs: dict[str, Any] = {
            "eval_set": eval_set,
        }
        if self.config.early_stopping_rounds > 0 and LIGHTGBM_AVAILABLE:
            fit_kwargs["callbacks"] = [
                lgb.early_stopping(
                    self.config.early_stopping_rounds,
                    verbose=False,
                ),
            ]
        model.fit(train_data.features, train_data.labels, **fit_kwargs)

    def _train_xgboost(
        self,
        model: Any,
        train_data: TrainingData,
        test_data: TrainingData,
    ):
        """Обучить XGBoost модель"""
        model.fit(
            train_data.features,
            train_data.labels,
            eval_set=[(test_data.features, test_data.labels)],
            verbose=False,
        )

    def _train_random_forest(
        self,
        model: Any,
        train_data: TrainingData,
        test_data: TrainingData,
    ):
        """Обучить Random Forest модель"""
        model.fit(train_data.features, train_data.labels)

    def _evaluate_model(
        self,
        model: Any,
        train_data: TrainingData,
        test_data: TrainingData,
    ) -> ModelMetrics:
        """Оценить модель"""
        metrics = ModelMetrics()

        # Предсказания
        y_pred = model.predict(test_data.features)
        y_proba = model.predict_proba(test_data.features)

        # Метрики
        metrics.accuracy = accuracy_score(test_data.labels, y_pred)
        metrics.precision = precision_score(test_data.labels, y_pred, zero_division=0)
        metrics.recall = recall_score(test_data.labels, y_pred, zero_division=0)
        metrics.f1_score = f1_score(test_data.labels, y_pred, zero_division=0)

        # ROC-AUC
        if len(np.unique(test_data.labels)) > 1:
            metrics.roc_auc = roc_auc_score(test_data.labels, y_proba[:, 1])

        # Confusion matrix
        cm = confusion_matrix(test_data.labels, y_pred)
        metrics.confusion_matrix = cm.tolist()

        # Classification report
        metrics.classification_report = classification_report(
            test_data.labels, y_pred, zero_division=0
        )

        # Feature importance
        metrics.feature_importance = {
            name: float(imp)
            for name, imp in zip(
                test_data.feature_names,
                model.feature_importances_ if hasattr(model, 'feature_importances_') else [0] * len(test_data.feature_names), strict=False
            )
        }

        # Cross-validation
        try:
            cv = StratifiedKFold(
                n_splits=self.config.cv_folds,
                shuffle=True,
                random_state=self.config.random_state,
            )
            cv_scores = cross_val_score(
                model,
                train_data.features,
                train_data.labels,
                cv=cv,
                scoring="roc_auc",
            )
            metrics.cv_scores = cv_scores.tolist()
        except Exception as e:
            logger.warning(f"CV failed: {e}")

        return metrics

    def cross_validate(
        self,
        training_data: TrainingData,
        model_type: str = None,
    ) -> list[float]:
        """Cross-validation"""
        model = self._create_model(model_type or self.config.model_type)

        cv = StratifiedKFold(
            n_splits=self.config.cv_folds,
            shuffle=True,
            random_state=self.config.random_state,
        )

        scores = cross_val_score(
            model,
            training_data.features,
            training_data.labels,
            cv=cv,
            scoring="roc_auc",
        )

        logger.info(f"CV scores: {scores}")
        return scores.tolist()


class DataPreparation:
    """
    Подготовка данных для обучения.

    Создаёт training dataset из исторических данных и результатов сделок.
    """

    @staticmethod
    def prepare_from_candles_and_trades(
        candles: list[models.Candle],
        trades: list[dict],
        feature_pipeline: Any = None,
        min_trades: int = 100,
    ) -> TrainingData:
        """
        Подготовить данные из свечей и сделок.

        Args:
            candles: Исторические свечи
            trades: Список сделок с результатами
            feature_pipeline: Пайплайн признаков
            min_trades: Минимальное количество сделок

        Returns:
            TrainingData для обучения
        """
        if len(trades) < min_trades:
            raise ValueError(
                f"Not enough trades: {len(trades)} < {min_trades}"
            )

        feature_pipeline = feature_pipeline or get_feature_pipeline()

        # Создаём DataFrame
        data = []

        for trade in trades:
            # Находим свечи на момент сделки
            trade_time = trade.get("timestamp")
            if not trade_time:
                continue

            # Фильтруем свечи до времени сделки
            trade_candles = [
                c for c in candles
                if c.open_time <= trade_time
            ]

            if len(trade_candles) < 50:
                continue

            # Генерируем признаки
            features = feature_pipeline.generate_features(
                symbol=trade.get("symbol", "BTC/USDT"),
                candles=trade_candles,
            )

            if not features.is_valid:
                continue

            # Целевая переменная: 1 если прибыльная сделка, 0 иначе
            target = 1 if trade.get("pnl", 0) > 0 else 0

            data.append({
                "features": features.features,
                "target": target,
                "trade_id": trade.get("id"),
            })

        if len(data) < min_trades:
            raise ValueError(f"Not enough valid samples: {len(data)}")

        # Создаём массивы
        feature_names = feature_pipeline.feature_names

        X = np.array([
            [d["features"].get(name, 0.0) for name in feature_names]
            for d in data
        ])

        y = np.array([d["target"] for d in data])

        return TrainingData(
            features=X,
            labels=y,
            feature_names=feature_names,
            metadata={
                "n_samples": len(data),
                "n_trades": len(trades),
                "positive_rate": float(np.mean(y)),
            },
        )

    @staticmethod
    def create_synthetic_data(
        n_samples: int = 1000,
        n_features: int = 20,
        positive_rate: float = 0.55,
        random_state: int = 42,
    ) -> TrainingData:
        """
        Создать синтетические данные для тестирования.

        Используется для юнит-тестов и отладки.
        """
        np.random.seed(random_state)

        feature_names = [f"feature_{i}" for i in range(n_features)]

        # Генерируем признаки
        X = np.random.randn(n_samples, n_features)

        # Добавляем сигнал (чтобы модель могла выучить что-то)
        signal = X[:, 0] * 0.5 + X[:, 1] * 0.3
        noise = np.random.randn(n_samples) * 0.5

        # Создаём метки
        prob = 1 / (1 + np.exp(-(signal + noise)))
        y = (prob > (1 - positive_rate)).astype(int)

        # Корректируем чтобы достичь нужного positive_rate
        current_rate = np.mean(y)
        if abs(current_rate - positive_rate) > 0.05:
            # Перемешиваем некоторые метки
            n_to_flip = int(abs(current_rate - positive_rate) * n_samples)
            flip_indices = np.random.choice(
                n_samples,
                n_to_flip,
                replace=False,
            )
            y[flip_indices] = 1 - y[flip_indices]

        return TrainingData(
            features=X,
            labels=y,
            feature_names=feature_names,
            metadata={
                "n_samples": n_samples,
                "n_features": n_features,
                "positive_rate": float(np.mean(y)),
                "synthetic": True,
            },
        )


# Глобальный трейнер
_trainer: ModelTrainer | None = None


def get_trainer() -> ModelTrainer:
    """Получить глобального трейнера"""
    global _trainer
    if _trainer is None:
        _trainer = ModelTrainer()
    return _trainer


def reset_trainer():
    """Сбросить трейнер (для тестов)"""
    global _trainer
    _trainer = None


# Утилита для получения пайплайна
def get_feature_pipeline():
    """Получить глобальный пайплайн признаков"""
    from .feature_pipeline import get_feature_pipeline as _get_fp
    return _get_fp()
