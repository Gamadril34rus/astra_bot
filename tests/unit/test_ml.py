"""
ASTRA BOT — Unit Tests for ML Module
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import astra_bot.ml.model_trainer as model_trainer_mod
import astra_bot.ml.temporal_trainer as temporal_trainer_mod
import numpy as np
import pytest
from astra_bot.ml.drift_detector import (
    DriftConfig,
    DriftDetectionResult,
    DriftDetector,
)
from astra_bot.ml.feature_pipeline import (
    FeatureConfig,
    FeaturePipeline,
)
from astra_bot.ml.model_registry import (
    ModelRegistry,
)
from astra_bot.ml.model_trainer import (
    DataPreparation,
    ModelMetrics,
    ModelTrainer,
    TrainingConfig,
)
from astra_bot.ml.predictor import (
    PredictionResult,
    PredictionService,
    Predictor,
)


def _make_lgb_mock() -> MagicMock:
    """Свежий мок LightGBM для подстановки в модули трейнера.

    Никаких глобальных ``sys.modules``: каждый тест получает свой мок через
    monkeypatch. Раньше моки протекали в остальной набор тестов (например,
    test_weekly_learner тренирует *реальную* модель и падал), а из-за порядка
    импортов мок мог вообще не примениться.
    """
    mock_lgb = MagicMock(name="lightgbm")
    mock_clf = MagicMock(name="LGBMClassifier")
    # Возвращаем предсказания под 100 тестовых сэмплов (test_size=0.2 от 500).
    mock_clf.predict.return_value = np.array([0, 1] * 50)
    mock_clf.predict_proba.return_value = np.array([[0.9, 0.1], [0.2, 0.8]] * 50)
    mock_clf.feature_importances_ = np.array([0.1] * 10)
    mock_lgb.LGBMClassifier.return_value = mock_clf
    mock_lgb.Dataset = MagicMock(name="Dataset")
    mock_lgb.early_stopping = MagicMock(name="early_stopping")
    return mock_lgb


@pytest.fixture
def lgb_mock(monkeypatch):
    """Подменить lightgbm в model_trainer и temporal_trainer на мок.

    temporal_trainer импортирует ``lgb`` из model_trainer в момент импорта,
    поэтому патчим обе привязки отдельно.
    """
    mock = _make_lgb_mock()
    monkeypatch.setattr(model_trainer_mod, "lgb", mock)
    monkeypatch.setattr(temporal_trainer_mod, "lgb", mock)
    return mock


class TestFeatureConfig:
    """Тесты конфигурации признаков"""

    def test_default_config(self):
        """Тест конфигурации по умолчанию"""
        config = FeatureConfig()

        assert config.include_price_features is True
        assert config.include_volume_features is True
        assert config.include_volatility_features is True
        assert config.include_momentum_features is True
        assert config.include_trend_features is True
        assert config.include_time_features is True

    def test_custom_config(self):
        """Тест кастомной конфигурации"""
        config = FeatureConfig(
            include_time_features=False,
            include_regime_features=False,
        )

        assert config.include_time_features is False
        assert config.include_regime_features is False


class TestFeaturePipeline:
    """Тесты пайплайна признаков"""

    @pytest.fixture
    def sample_candles(self):
        """Создать тестовые свечи"""
        candles = []
        base_price = 50000.0

        for i in range(300):
            price = base_price + i * 10

            candle = MagicMock()
            candle.open = Decimal(str(price))
            candle.high = Decimal(str(price + 50))
            candle.low = Decimal(str(price - 50))
            candle.close = Decimal(str(price + 25))
            candle.volume = Decimal(str(100 + np.random.random() * 50))
            candle.open_time = int(datetime(2024, 1, 1).timestamp() + i * 3600)

            candles.append(candle)

        return candles

    def test_pipeline_creation(self):
        """Тест создания пайплайна"""
        pipeline = FeaturePipeline()

        assert pipeline is not None
        assert len(pipeline.feature_names) > 0

    def test_feature_generation(self, sample_candles):
        """Тест генерации признаков"""
        pipeline = FeaturePipeline()

        features = pipeline.generate_features(
            symbol="BTC/USDT",
            candles=sample_candles,
        )

        assert features.symbol == "BTC/USDT"
        assert features.is_valid is True
        assert len(features.features) > 0
        assert features.feature_hash is not None

    def test_feature_validation(self, sample_candles):
        """Тест валидации признаков"""
        pipeline = FeaturePipeline()

        features = pipeline.generate_features(
            symbol="BTC/USDT",
            candles=sample_candles,
        )

        assert pipeline.validate_features(features) is True

    def test_insufficient_candles(self):
        """Тест с недостаточным количеством свечей"""
        pipeline = FeaturePipeline()

        short_candles = [MagicMock() for _ in range(10)]
        for i, c in enumerate(short_candles):
            c.close = Decimal(str(50000 + i))
            c.open_time = i

        features = pipeline.generate_features(
            symbol="BTC/USDT",
            candles=short_candles,
        )

        assert features.is_valid is False
        assert len(features.features) == 0


class TestModelTrainer:
    """Тесты трейнера модели"""

    def test_trainer_creation(self):
        """Тест создания трейнера"""
        trainer = ModelTrainer()

        assert trainer is not None
        assert trainer.config.model_type == "lightgbm"

    def test_training_config(self):
        """Тест конфигурации обучения"""
        config = TrainingConfig(
            model_type="xgboost",
            n_estimators=200,
            max_depth=6,
        )

        assert config.model_type == "xgboost"
        assert config.n_estimators == 200
        assert config.max_depth == 6

    def test_synthetic_data(self):
        """Тест синтетических данных"""
        data = DataPreparation.create_synthetic_data(
            n_samples=1000,
            n_features=20,
            positive_rate=0.55,
        )

        assert data.n_samples == 1000
        assert data.n_features == 20
        assert len(data.feature_names) == 20
        assert 0.5 < np.mean(data.labels) < 0.6

    def test_train_model_mock(self, lgb_mock):
        """Тест обучения модели (с моком)"""
        trainer = ModelTrainer()

        training_data = DataPreparation.create_synthetic_data(
            n_samples=500,
            n_features=10,
            positive_rate=0.55,
        )

        # Обучаем модель (с использованием мока LightGBM)
        model = trainer.train(training_data)

        assert model.is_fitted is True
        # Проверяем что метрики рассчитаны
        assert model.metrics.accuracy >= 0.0
        assert model.metrics.roc_auc >= 0.0

    def test_model_metrics(self):
        """Тест метрик модели"""
        metrics = ModelMetrics()

        assert metrics.accuracy == 0.0
        assert metrics.is_good is False
        assert metrics.is_profitable is False

        # Устанавливаем хорошие метрики
        metrics.accuracy = 0.6
        metrics.precision = 0.65
        metrics.recall = 0.55
        metrics.f1_score = 0.6
        metrics.roc_auc = 0.65

        assert metrics.is_good is True
        assert metrics.is_profitable is True

    def test_training_data_split(self):
        """Тест разделения данных"""
        training_data = DataPreparation.create_synthetic_data(
            n_samples=1000,
            n_features=10,
        )

        train_data, test_data = training_data.split(test_size=0.2)

        assert len(train_data.labels) == 800
        assert len(test_data.labels) == 200
        assert train_data.feature_names == test_data.feature_names

    def test_lightgbm_uses_native_params_not_sklearn_aliases(self, lgb_mock):
        """LightGBM не должен получать sklearn-алиасы min_samples_split /
        min_samples_leaf — они заваливают CI-логи предупреждениями."""
        trainer = ModelTrainer()
        trainer._create_model("lightgbm")

        kwargs = lgb_mock.LGBMClassifier.call_args.kwargs
        assert "min_child_samples" in kwargs
        assert kwargs["min_child_samples"] == trainer.config.min_samples_leaf
        assert "min_samples_split" not in kwargs
        assert "min_samples_leaf" not in kwargs

    def test_temporal_trainer_uses_native_lightgbm_params(self, lgb_mock):
        """train_temporal тоже собирает LGBMClassifier с нативными параметрами."""
        from astra_bot.ml.temporal_trainer import train_temporal

        training_data = DataPreparation.create_synthetic_data(
            n_samples=500,
            n_features=10,
            positive_rate=0.5,
        )
        train_temporal(training_data)

        kwargs = lgb_mock.LGBMClassifier.call_args.kwargs
        assert "min_child_samples" in kwargs
        assert "min_samples_split" not in kwargs
        assert "min_samples_leaf" not in kwargs


class TestPredictor:
    """Тесты предиктора"""

    @pytest.fixture
    def sample_candles(self):
        """Создать тестовые свечи"""
        candles = []
        base_price = 50000.0

        for i in range(300):
            price = base_price + i * 10

            candle = MagicMock()
            candle.open = Decimal(str(price))
            candle.high = Decimal(str(price + 50))
            candle.low = Decimal(str(price - 50))
            candle.close = Decimal(str(price + 25))
            candle.volume = Decimal(str(100 + np.random.random() * 50))
            candle.open_time = int(datetime(2024, 1, 1).timestamp() + i * 3600)

            candles.append(candle)

        return candles

    def test_predictor_not_ready(self):
        """Тест предиктора без модели"""
        predictor = Predictor()

        assert predictor.is_ready is False

    def test_predict_without_model(self, sample_candles):
        """Тест предсказания без модели"""
        predictor = Predictor()

        result = predictor.predict(
            symbol="BTC/USDT",
            candles=sample_candles,
        )

        assert result.is_valid is False
        assert "Model not ready" in result.reasons

    def test_prediction_result(self):
        """Тест результата предсказания"""
        result = PredictionResult(
            symbol="BTC/USDT",
            probability=0.75,
            confidence=0.7,  # MEDIUM (>= 0.6)
            prediction=1,
            expected_value=0.5,
        )

        assert result.is_profitable_signal is True
        assert result.confidence_level == "MEDIUM"

        result2 = PredictionResult(
            symbol="BTC/USDT",
            probability=0.4,
            confidence=0.1,
            prediction=0,
        )

        assert result2.is_profitable_signal is False
        assert result2.confidence_level == "VERY_LOW"

    def test_prediction_service_without_model(self):
        """Тест сервиса предсказаний без модели"""
        service = PredictionService()

        assert service.is_ready is False

        result = service.evaluate_signal(
            signal=MagicMock(symbol="BTC/USDT"),
            candles=[],
        )

        assert result["ml_approved"] is False
        assert "ML not ready" in result["reason"]


class TestModelRegistry:
    """Тесты реестра моделей"""

    def test_registry_creation(self, tmp_path):
        """Тест создания реестра"""
        registry = ModelRegistry(registry_dir=str(tmp_path / "registry"))

        assert registry is not None
        assert len(registry._models) == 0

    def test_get_production_model(self, tmp_path):
        """Тест получения production модели"""
        registry = ModelRegistry(registry_dir=str(tmp_path / "registry"))

        production = registry.get_production_model()

        assert production is None

    def test_list_models_empty(self, tmp_path):
        """Тест списка моделей (пустой)"""
        registry = ModelRegistry(registry_dir=str(tmp_path / "registry"))

        models = registry.list_models()

        assert len(models) == 0

    def test_get_registry_stats(self, tmp_path):
        """Тест статистики реестра"""
        registry = ModelRegistry(registry_dir=str(tmp_path / "registry"))

        stats = registry.get_registry_stats()

        assert stats["total_models"] == 0
        assert stats["production_version"] is None


class TestDriftDetector:
    """Тесты детектора дрейфа"""

    def test_detector_creation(self):
        """Тест создания детектора"""
        detector = DriftDetector()

        assert detector is not None
        assert detector.is_drift_detected is False

    def test_custom_config(self):
        """Тест кастомной конфигурации"""
        config = DriftConfig(
            accuracy_drop_threshold=0.1,
            roc_auc_drop_threshold=0.1,
            check_interval_samples=10,
        )

        detector = DriftDetector(config=config)

        assert detector.config.accuracy_drop_threshold == 0.1
        assert detector.config.check_interval_samples == 10

    def test_update_reference_performance(self):
        """Тест обновления reference производительности"""
        detector = DriftDetector()

        metrics = ModelMetrics()
        metrics.accuracy = 0.65
        metrics.roc_auc = 0.7

        detector.update_reference_performance(metrics)

        assert detector._reference_performance is not None
        assert detector._reference_performance.accuracy == 0.65

    def test_record_prediction(self):
        """Тест записи предсказания"""
        detector = DriftDetector()

        detector.record_prediction(
            prediction=1,
            probability=0.75,
            actual_outcome=1,
        )

        assert len(detector._prediction_history) == 1

    def test_check_drift_no_data(self):
        """Тест проверки дрейфа без данных"""
        detector = DriftDetector(config=DriftConfig(check_interval_samples=100))

        result = detector.check_drift()

        assert result.is_drift_detected is False
        assert result.samples_since_last_check == 0

    def test_drift_detection_result(self):
        """Тест результата детекции дрейфа"""
        result = DriftDetectionResult()

        assert result.is_drift_detected is False
        assert result.severity == "none"
        assert result.needs_attention is False
        assert result.needs_retrain is False

        result2 = DriftDetectionResult(
            is_drift_detected=True,
            drift_type="performance",
            severity="high",
            accuracy_drop=0.15,
        )

        assert result2.is_drift_detected is True
        assert result2.needs_attention is True
        assert result2.needs_retrain is True

    def test_reset(self):
        """Тест сброса"""
        detector = DriftDetector()

        detector.update_reference_performance(ModelMetrics())
        detector.record_prediction(1, 0.75, 1)

        detector.reset()

        assert detector._reference_performance is None
        assert len(detector._prediction_history) == 0
        assert detector.is_drift_detected is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
