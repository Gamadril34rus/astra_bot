"""
ASTRA BOT — ML Predictor
Сервис предсказаний ML модели
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any
from pathlib import Path

import numpy as np

from .feature_pipeline import FeaturePipeline, FeatureVector
from .model_trainer import MLModel, ModelMetrics, TrainingConfig

logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    """Результат предсказания"""
    symbol: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    probability: float = 0.5  # Вероятность profitable trade
    confidence: float = 0.0  # Уверенность модели
    prediction: int = 0  # 0 или 1
    expected_value: Optional[float] = None
    feature_vector: Optional[FeatureVector] = None
    model_version: str = "unknown"
    is_valid: bool = True
    reasons: List[str] = field(default_factory=list)
    
    @property
    def is_profitable_signal(self) -> bool:
        """Является ли сигнал прибыльным"""
        return self.probability > 0.5 and self.is_valid
    
    @property
    def confidence_level(self) -> str:
        """Уровень уверенности"""
        if self.confidence >= 0.8:
            return "HIGH"
        elif self.confidence >= 0.6:
            return "MEDIUM"
        elif self.confidence >= 0.4:
            return "LOW"
        return "VERY_LOW"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "probability": self.probability,
            "confidence": self.confidence,
            "prediction": self.prediction,
            "expected_value": self.expected_value,
            "model_version": self.model_version,
            "is_valid": self.is_valid,
            "confidence_level": self.confidence_level,
            "reasons": self.reasons,
        }


class Predictor:
    """
    Сервис предсказаний на основе ML модели.
    
    Используется для оценки вероятности прибыльной сделки
    перед отправкой сигнала в Risk Engine.
    """
    
    def __init__(
        self,
        model: MLModel = None,
        feature_pipeline: FeaturePipeline = None,
        model_version: str = "ML-001",
    ):
        self.model = model
        self.feature_pipeline = feature_pipeline or FeaturePipeline()
        self.model_version = model_version
        self._is_ready = model is not None and model.is_fitted
    
    @property
    def is_ready(self) -> bool:
        """Готов ли сервис к предсказаниям"""
        return self._is_ready and self.model is not None
    
    def predict(
        self,
        symbol: str,
        candles: List,
        orderbook: Any = None,
        market_regime: str = None,
        btc_correlation: float = None,
        current_time: datetime = None,
    ) -> PredictionResult:
        """
        Сделать предсказание.
        
        Args:
            symbol: Торговый символ
            candles: Исторические свечи
            orderbook: Стакан заявок (опционально)
            market_regime: Режим рынка (опционально)
            btc_correlation: Корреляция с BTC (опционально)
            current_time: Текущее время (опционально)
        
        Returns:
            PredictionResult с вероятностью и confidence
        """
        if not self.is_ready:
            return PredictionResult(
                symbol=symbol,
                is_valid=False,
                reasons=["Model not ready"],
            )
        
        try:
            # Генерация признаков
            feature_vector = self.feature_pipeline.generate_features(
                symbol=symbol,
                candles=candles,
                orderbook=orderbook,
                market_regime=market_regime,
                btc_correlation=btc_correlation,
                current_time=current_time,
            )
            
            # Валидация
            if not self.feature_pipeline.validate_features(feature_vector):
                return PredictionResult(
                    symbol=symbol,
                    is_valid=False,
                    reasons=["Invalid features"],
                    feature_vector=feature_vector,
                )
            
            # Подготовка данных
            feature_array = feature_vector.to_array(self.feature_pipeline.feature_names)
            feature_array = feature_array.reshape(1, -1)
            
            # Предсказание
            probability = self.model.predict_probability(feature_array)
            
            # Confidence на основе вероятности
            # Чем дальше от 0.5, тем выше confidence
            confidence = abs(probability - 0.5) * 2  # 0-1 scale
            
            # Предсказание класса
            prediction = 1 if probability > 0.5 else 0
            
            # Расчёт ожидаемого значения (упрощённо)
            # EV = P(win) * avg_win - P(loss) * avg_loss
            # Без знания avg_win/avg_loss используем упрощённую оценку
            expected_value = (probability - 0.5) * 2  # -1 to 1
            
            reasons = []
            if probability > 0.7:
                reasons.append("High probability signal")
            elif probability < 0.3:
                reasons.append("Low probability signal")
            
            if confidence > 0.6:
                reasons.append("High model confidence")
            
            return PredictionResult(
                symbol=symbol,
                probability=probability,
                confidence=confidence,
                prediction=prediction,
                expected_value=expected_value,
                feature_vector=feature_vector,
                model_version=self.model_version,
                is_valid=True,
                reasons=reasons,
            )
        
        except Exception as e:
            logger.error(f"Prediction error for {symbol}: {e}")
            return PredictionResult(
                symbol=symbol,
                is_valid=False,
                reasons=[f"Prediction error: {str(e)}"],
            )
    
    def predict_batch(
        self,
        predictions_data: List[Dict],
    ) -> List[PredictionResult]:
        """Пакетное предсказание"""
        results = []
        
        for data in predictions_data:
            result = self.predict(
                symbol=data.get("symbol", "UNKNOWN"),
                candles=data.get("candles", []),
                orderbook=data.get("orderbook"),
                market_regime=data.get("market_regime"),
                btc_correlation=data.get("btc_correlation"),
                current_time=data.get("current_time"),
            )
            results.append(result)
        
        return results
    
    def get_model_info(self) -> Dict[str, Any]:
        """Получить информацию о модели"""
        if not self.model:
            return {"status": "no_model"}
        
        metrics = self.model.metrics
        importance = self.model.get_feature_importance()
        
        # Топ важных признаков
        top_features = sorted(
            importance.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:10]
        
        return {
            "status": "ready" if self.is_ready else "not_ready",
            "model_version": self.model_version,
            "is_fitted": self.model.is_fitted,
            "metrics": {
                "accuracy": metrics.accuracy,
                "precision": metrics.precision,
                "recall": metrics.recall,
                "f1_score": metrics.f1_score,
                "roc_auc": metrics.roc_auc,
                "training_time": metrics.training_time_seconds,
            },
            "top_features": [
                {"name": name, "importance": imp}
                for name, imp in top_features
            ],
            "feature_count": len(self.feature_pipeline.feature_names),
        }
    
    def load_model(self, model_path: str) -> bool:
        """Загрузить модель из файла"""
        try:
            self.model = MLModel.load(model_path)
            self.model_version = f"loaded_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            self._is_ready = self.model.is_fitted
            logger.info(f"Model loaded from {model_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return False
    
    def save_model(self, model_path: str) -> bool:
        """Сохранить модель в файл"""
        if not self.model:
            logger.error("No model to save")
            return False
        
        try:
            self.model.save(model_path)
            return True
        except Exception as e:
            logger.error(f"Failed to save model: {e}")
            return False


class PredictionService:
    """
    Сервис предсказаний для интеграции с trading системой.
    
    Оборачивает Predictor и предоставляет удобный интерфейс
    для использования в trading loop.
    """
    
    def __init__(
        self,
        predictor: Predictor = None,
        min_probability: float = 0.55,
        min_confidence: float = 0.3,
    ):
        self.predictor = predictor or Predictor()
        self.min_probability = min_probability
        self.min_confidence = min_confidence
    
    @property
    def is_ready(self) -> bool:
        return self.predictor.is_ready
    
    def evaluate_signal(
        self,
        signal: Any,
        candles: List,
        orderbook: Any = None,
        market_regime: str = None,
    ) -> Dict[str, Any]:
        """
        Оценить торговый сигнал с помощью ML.
        
        Возвращает уточнённый сигнал с ML вероятностью.
        """
        if not self.is_ready:
            return {
                "signal": signal,
                "ml_probability": None,
                "ml_confidence": None,
                "ml_approved": False,
                "reason": "ML not ready",
            }
        
        # Делаем предсказание
        prediction = self.predictor.predict(
            symbol=signal.symbol,
            candles=candles,
            orderbook=orderbook,
            market_regime=market_regime,
        )
        
        # Решаем принимать ли сигнал
        ml_approved = (
            prediction.is_valid
            and prediction.probability >= self.min_probability
            and prediction.confidence >= self.min_confidence
        )
        
        return {
            "signal": signal,
            "ml_probability": prediction.probability,
            "ml_confidence": prediction.confidence,
            "ml_prediction": prediction.prediction,
            "ml_expected_value": prediction.expected_value,
            "ml_approved": ml_approved,
            "ml_model_version": prediction.model_version,
            "ml_reasons": prediction.reasons,
        }
    
    def should_trade(
        self,
        symbol: str,
        candles: List,
        orderbook: Any = None,
        market_regime: str = None,
    ) -> bool:
        """
        Следует ли-trades по текущему состоянию рынка.
        
        Возвращает True если ML модель даёт положительный сигнал.
        """
        prediction = self.predictor.predict(
            symbol=symbol,
            candles=candles,
            orderbook=orderbook,
            market_regime=market_regime,
        )
        
        return (
            prediction.is_valid
            and prediction.probability >= self.min_probability
            and prediction.confidence >= self.min_confidence
        )
    
    def get_health_status(self) -> Dict[str, Any]:
        """Получить статус здоровья ML сервиса"""
        model_info = self.predictor.get_model_info()
        
        return {
            "status": "healthy" if self.is_ready else "degraded",
            "model_ready": self.is_ready,
            "model_info": model_info,
            "min_probability_threshold": self.min_probability,
            "min_confidence_threshold": self.min_confidence,
        }


# Глобальный сервис
_prediction_service: Optional[PredictionService] = None


def get_prediction_service() -> PredictionService:
    """Получить глобальный prediction service"""
    global _prediction_service
    if _prediction_service is None:
        _prediction_service = PredictionService()
    return _prediction_service


def reset_prediction_service():
    """Сбросить prediction service (для тестов)"""
    global _prediction_service
    _prediction_service = None
