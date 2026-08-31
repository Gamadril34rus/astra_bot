"""
ASTRA BOT — Uncertainty Engine

Движок оценки неопределённости (Master Specification v2, Section 6)

Отвечает за расчёт:
- prediction confidence
- model uncertainty
- data uncertainty
- regime uncertainty
- sample uncertainty
- model disagreement

Итог: total_uncertainty

Ключевой принцип: Высокий disagreement не является автоматически SELL или BUY.
Он является самостоятельным feature для дальнейшего анализа.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class UncertaintyType(str, Enum):
    """Типы неопределённости"""
    PREDICTION_CONFIDENCE = "prediction_confidence"
    MODEL_UNCERTAINTY = "model_uncertainty"
    DATA_UNCERTAINTY = "data_uncertainty"
    REGIME_UNCERTAINTY = "regime_uncertainty"
    SAMPLE_UNCERTAINTY = "sample_uncertainty"
    MODEL_DISAGREEMENT = "model_disagreement"
    TOTAL = "total_uncertainty"


@dataclass
class UncertaintyComponent:
    """Компонент неопределённости"""
    type: UncertaintyType
    value: float  # 0-1, где 0 = полная уверенность, 1 = максимальная неопределённость
    description: str
    metadata: dict = field(default_factory=dict)


@dataclass
class UncertaintyResult:
    """Результат оценки неопределённости"""
    components: dict[UncertaintyType, UncertaintyComponent]
    total_uncertainty: float
    timestamp: datetime
    symbol: str
    timeframe: str
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "components": {k: v.to_dict() for k, v in self.components.items()},
            "total_uncertainty": self.total_uncertainty,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }
    
    def get_component(self, component_type: UncertaintyType) -> UncertaintyComponent | None:
        return self.components.get(component_type)


@dataclass
class ModelPrediction:
    """Предсказание модели с метаданными"""
    direction: str  # long/short/neutral
    probability: float  # 0-1
    expected_return: float  # %
    model_name: str
    model_version: str
    features_used: list[str]
    sample_size: int
    training_date: datetime | None = None


@dataclass
class MarketDataQuality:
    """Качество рыночных данных"""
    spread_pct: float
    depth: float
    volume: float
    volatility: float
    data_gaps: int
    latency_ms: float
    
    @property
    def is_high_quality(self) -> bool:
        """Данные достаточно качественные"""
        return (self.spread_pct < 0.005 and 
                self.depth > 1000 and 
                self.volume > 0 and
                self.data_gaps == 0)


@dataclass
class RegimeAssessment:
    """Оценка режима рынка"""
    current_regime: str
    regime_confidence: float  # 0-1
    regime_stability: float  # 0-1, стабильность текущего режима
    transition_probability: float  # 0-1, вероятность смены режима
    historical_coverage: int  # количество исторических наблюдений в похожем режиме


class UncertaintyEngine:
    """
    Движок оценки неопределённости.
    
    Рассчитывает различные компоненты неопределённости и объединяет их
    в итоговую оценку total_uncertainty.
    """
    
    def __init__(self):
        # Веса компонентов неопределённости
        self.weights = {
            UncertaintyType.PREDICTION_CONFIDENCE: 0.25,
            UncertaintyType.MODEL_UNCERTAINTY: 0.20,
            UncertaintyType.DATA_UNCERTAINTY: 0.15,
            UncertaintyType.REGIME_UNCERTAINTY: 0.20,
            UncertaintyType.SAMPLE_UNCERTAINTY: 0.10,
            UncertaintyType.MODEL_DISAGREEMENT: 0.10,
        }
        
        # Пороги для классификации неопределённости
        self.thresholds = {
            "low": 0.3,
            "medium": 0.6,
            "high": 0.8,
        }
    
    def calculate_prediction_confidence(
        self, 
        probability: float, 
        model_calibration: float | None = None
    ) -> UncertaintyComponent:
        """
        Рассчитать неопределённость предсказания.
        
        Args:
            probability: Вероятность предсказания (0-1)
            model_calibration: Калибровочный коэффициент модели (0-1)
        
        Returns:
            UncertaintyComponent
        """
        # Базовая неопределённость: чем ближе к 0.5, тем выше неопределённость
        base_uncertainty = abs(probability - 0.5) * 2
        base_uncertainty = 1 - base_uncertainty  # Инвертируем: 0.5 -> 1.0, 0.0 или 1.0 -> 0.0
        
        # Корректировка на калибровку модели
        if model_calibration is not None:
            # Если модель переоценивает уверенность, увеличиваем неопределённость
            calibration_factor = 1 + (1 - model_calibration) * 0.5
            base_uncertainty = min(1.0, base_uncertainty * calibration_factor)
        
        return UncertaintyComponent(
            type=UncertaintyType.PREDICTION_CONFIDENCE,
            value=base_uncertainty,
            description=f"Prediction confidence uncertainty: {base_uncertainty:.3f}",
            metadata={"probability": probability, "calibration_factor": model_calibration}
        )
    
    def calculate_model_uncertainty(
        self, 
        predictions: list[ModelPrediction],
        current_prediction: ModelPrediction
    ) -> UncertaintyComponent:
        """
        Рассчитать неопределённость модели на основе исторических предсказаний.
        
        Args:
            predictions: Исторические предсказания модели
            current_prediction: Текущее предсказание
        
        Returns:
            UncertaintyComponent
        """
        if not predictions:
            return UncertaintyComponent(
                type=UncertaintyType.MODEL_UNCERTAINTY,
                value=1.0,  # Максимальная неопределённость без истории
                description="No historical predictions available",
                metadata={"sample_size": 0}
            )
        
        # Рассчитать стандартное отклонение предсказаний
        probs = [p.probability for p in predictions[-100:]]  # Последние 100 предсказаний
        std_dev = np.std(probs) if len(probs) > 1 else 0.0
        
        # Нормализовать стандартное отклонение в диапазон 0-1
        max_std = 0.5  # Максимальное стандартное отклонение вероятности
        model_uncertainty = min(1.0, std_dev / max_std)
        
        # Учесть размер выборки
        sample_size = len(predictions)
        if sample_size < 10:
            sample_factor = 1.0 - (sample_size / 10) * 0.5
            model_uncertainty = min(1.0, model_uncertainty + sample_factor)
        
        return UncertaintyComponent(
            type=UncertaintyType.MODEL_UNCERTAINTY,
            value=model_uncertainty,
            description=f"Model uncertainty: {model_uncertainty:.3f} (std={std_dev:.3f}, n={sample_size})",
            metadata={"std_dev": std_dev, "sample_size": sample_size}
        )
    
    def calculate_data_uncertainty(self, data_quality: MarketDataQuality) -> UncertaintyComponent:
        """
        Рассчитать неопределённость данных.
        
        Args:
            data_quality: Оценка качества рыночных данных
        
        Returns:
            UncertaintyComponent
        """
        # Рассчитать неопределённость на основе качества данных
        uncertainty_factors = []
        
        # Spread
        spread_factor = min(1.0, data_quality.spread_pct / 0.01)  # 1% spread = max
        
        # Depth
        depth_factor = 0.0 if data_quality.depth > 10000 else 1.0 - (data_quality.depth / 10000)
        
        # Volume
        volume_factor = 0.0 if data_quality.volume > 1000 else 1.0 - (data_quality.volume / 1000)
        
        # Data gaps
        gap_factor = min(1.0, data_quality.data_gaps * 0.1)
        
        # Latency
        latency_factor = min(1.0, data_quality.latency_ms / 1000)
        
        # Объединить факторы
        data_uncertainty = np.mean([
            spread_factor, 
            depth_factor, 
            volume_factor, 
            gap_factor,
            latency_factor
        ])
        
        return UncertaintyComponent(
            type=UncertaintyType.DATA_UNCERTAINTY,
            value=data_uncertainty,
            description=f"Data uncertainty: {data_uncertainty:.3f}",
            metadata={
                "spread_pct": data_quality.spread_pct,
                "depth": data_quality.depth,
                "volume": data_quality.volume,
                "data_gaps": data_quality.data_gaps,
                "latency_ms": data_quality.latency_ms
            }
        )
    
    def calculate_regime_uncertainty(self, regime_assessment: RegimeAssessment) -> UncertaintyComponent:
        """
        Рассчитать неопределённость режима.
        
        Args:
            regime_assessment: Оценка текущего режима рынка
        
        Returns:
            UncertaintyComponent
        """
        # Основные факторы неопределённости режима
        confidence_factor = 1.0 - regime_assessment.regime_confidence
        stability_factor = 1.0 - regime_assessment.regime_stability
        transition_factor = regime_assessment.transition_probability
        
        # Неопределённость на основе исторического покрытия
        if regime_assessment.historical_coverage < 10:
            coverage_factor = 1.0
        elif regime_assessment.historical_coverage < 100:
            coverage_factor = 0.5
        else:
            coverage_factor = 0.0
        
        # Объединить факторы
        regime_uncertainty = np.mean([
            confidence_factor,
            stability_factor,
            transition_factor,
            coverage_factor
        ])
        
        return UncertaintyComponent(
            type=UncertaintyType.REGIME_UNCERTAINTY,
            value=regime_uncertainty,
            description=f"Regime uncertainty: {regime_uncertainty:.3f} (coverage={regime_assessment.historical_coverage})",
            metadata={
                "regime": regime_assessment.current_regime,
                "confidence": regime_assessment.regime_confidence,
                "stability": regime_assessment.regime_stability,
                "transition_prob": regime_assessment.transition_probability,
                "historical_coverage": regime_assessment.historical_coverage
            }
        )
    
    def calculate_sample_uncertainty(
        self, 
        sample_size: int, 
        population_size: int | None = None
    ) -> UncertaintyComponent:
        """
        Рассчитать неопределённость выборки.
        
        Args:
            sample_size: Размер текущей выборки
            population_size: Размер генеральной совокупности (опционально)
        
        Returns:
            UncertaintyComponent
        """
        if sample_size <= 0:
            return UncertaintyComponent(
                type=UncertaintyType.SAMPLE_UNCERTAINTY,
                value=1.0,
                description="Zero sample size",
                metadata={"sample_size": 0}
            )
        
        # Неопределённость на основе размера выборки
        if sample_size >= 1000:
            sample_uncertainty = 0.0
        elif sample_size >= 100:
            sample_uncertainty = 0.3
        elif sample_size >= 10:
            sample_uncertainty = 0.7
        else:
            sample_uncertainty = 0.9
        
        # Корректировка на основе соотношения выборка/популяция
        if population_size and population_size > 0:
            ratio = sample_size / population_size
            if ratio < 0.1:
                sample_uncertainty = min(1.0, sample_uncertainty * 1.5)
        
        return UncertaintyComponent(
            type=UncertaintyType.SAMPLE_UNCERTAINTY,
            value=sample_uncertainty,
            description=f"Sample uncertainty: {sample_uncertainty:.3f} (n={sample_size})",
            metadata={"sample_size": sample_size, "population_size": population_size}
        )
    
    def calculate_model_disagreement(
        self, 
        predictions: list[ModelPrediction]
    ) -> UncertaintyComponent:
        """
        Рассчитать disagreement между моделями (Section 7).
        
        Args:
            predictions: Предсказания разных моделей
        
        Returns:
            UncertaintyComponent
        """
        if len(predictions) < 2:
            return UncertaintyComponent(
                type=UncertaintyType.MODEL_DISAGREEMENT,
                value=0.0,
                description="Single model - no disagreement",
                metadata={"num_models": len(predictions)}
            )
        
        # Рассчитать disagreement по направлению
        directions = [p.direction for p in predictions]
        unique_directions = set(directions)
        
        if len(unique_directions) == 1:
            direction_disagreement = 0.0
        elif len(unique_directions) == 2:
            # Две разные модели - максимальный disagreement
            direction_disagreement = 1.0
        else:
            # Три или более разных направления
            direction_disagreement = 0.8
        
        # Рассчитать disagreement по вероятности
        probs = [p.probability for p in predictions]
        prob_std = np.std(probs)
        prob_disagreement = min(1.0, prob_std / 0.5)
        
        # Рассчитать disagreement по ожидаемой доходности
        returns = [p.expected_return for p in predictions]
        return_std = np.std(returns)
        return_disagreement = min(1.0, return_std / 10)  # 10% стандартное отклонение = max
        
        # Объединить факторы
        model_disagreement = np.mean([
            direction_disagreement,
            prob_disagreement,
            return_disagreement
        ])
        
        return UncertaintyComponent(
            type=UncertaintyType.MODEL_DISAGREEMENT,
            value=model_disagreement,
            description=f"Model disagreement: {model_disagreement:.3f} ({len(predictions)} models)",
            metadata={
                "num_models": len(predictions),
                "direction_disagreement": direction_disagreement,
                "prob_disagreement": prob_disagreement,
                "return_disagreement": return_disagreement
            }
        )
    
    def calculate_total_uncertainty(
        self, 
        components: dict[UncertaintyType, UncertaintyComponent]
    ) -> float:
        """
        Рассчитать итоговую неопределённость.
        
        Args:
            components: Словарь компонентов неопределённости
        
        Returns:
            Итоговая неопределённость (0-1)
        """
        total = 0.0
        total_weight = 0.0
        
        for component_type, component in components.items():
            weight = self.weights.get(component_type, 0.0)
            if weight > 0:
                total += component.value * weight
                total_weight += weight
        
        if total_weight > 0:
            return total / total_weight
        return 0.0
    
    def assess_uncertainty(
        self,
        symbol: str,
        timeframe: str,
        current_prediction: ModelPrediction,
        historical_predictions: list[ModelPrediction],
        data_quality: MarketDataQuality,
        regime_assessment: RegimeAssessment,
        sample_size: int,
        population_size: int | None = None,
        ensemble_predictions: list[ModelPrediction] | None = None
    ) -> UncertaintyResult:
        """
        Полная оценка неопределённости.
        
        Args:
            symbol: Символ инструмента
            timeframe: Таймфрейм
            current_prediction: Текущее предсказание
            historical_predictions: Исторические предсказания модели
            data_quality: Качество рыночных данных
            regime_assessment: Оценка режима рынка
            sample_size: Размер выборки
            population_size: Размер генеральной совокупности
            ensemble_predictions: Предсказания ансамбля моделей
        
        Returns:
            UncertaintyResult с полной оценкой
        """
        components = {}
        
        # 1. Prediction Confidence
        # Extract calibration from features_used if it's a dict, otherwise None
        calibration = None
        if isinstance(current_prediction.features_used, dict):
            calibration = current_prediction.features_used.get("calibration", None)
        
        components[UncertaintyType.PREDICTION_CONFIDENCE] = (
            self.calculate_prediction_confidence(
                current_prediction.probability,
                calibration
            )
        )
        
        # 2. Model Uncertainty
        components[UncertaintyType.MODEL_UNCERTAINTY] = (
            self.calculate_model_uncertainty(
                historical_predictions,
                current_prediction
            )
        )
        
        # 3. Data Uncertainty
        components[UncertaintyType.DATA_UNCERTAINTY] = (
            self.calculate_data_uncertainty(data_quality)
        )
        
        # 4. Regime Uncertainty
        components[UncertaintyType.REGIME_UNCERTAINTY] = (
            self.calculate_regime_uncertainty(regime_assessment)
        )
        
        # 5. Sample Uncertainty
        components[UncertaintyType.SAMPLE_UNCERTAINTY] = (
            self.calculate_sample_uncertainty(sample_size, population_size)
        )
        
        # 6. Model Disagreement
        if ensemble_predictions:
            components[UncertaintyType.MODEL_DISAGREEMENT] = (
                self.calculate_model_disagreement(ensemble_predictions)
            )
        else:
            components[UncertaintyType.MODEL_DISAGREEMENT] = UncertaintyComponent(
                type=UncertaintyType.MODEL_DISAGREEMENT,
                value=0.0,
                description="No ensemble predictions available",
                metadata={"num_models": 0}
            )
        
        # Рассчитать итоговую неопределённость
        total_uncertainty = self.calculate_total_uncertainty(components)
        
        return UncertaintyResult(
            components=components,
            total_uncertainty=total_uncertainty,
            timestamp=datetime.now(),
            symbol=symbol,
            timeframe=timeframe
        )
    
    def classify_uncertainty_level(self, uncertainty: float) -> str:
        """
        Классифицировать уровень неопределённости.
        
        Args:
            uncertainty: Значение неопределённости (0-1)
        
        Returns:
            Уровень неопределённости: low/medium/high/extreme
        """
        if uncertainty < self.thresholds["low"]:
            return "low"
        elif uncertainty < self.thresholds["medium"]:
            return "medium"
        elif uncertainty < self.thresholds["high"]:
            return "high"
        else:
            return "extreme"
    
    def should_trade(self, uncertainty: float, min_confidence_threshold: float = 0.7) -> bool:
        """
        Определить, следует ли торговать при данном уровне неопределённости.
        
        Args:
            uncertainty: Итоговая неопределённость
            min_confidence_threshold: Минимальный порог уверенности (0-1)
        
        Returns:
            True если можно торговать, False если лучше воздержаться
        """
        confidence = 1.0 - uncertainty
        return confidence >= min_confidence_threshold


# Глобальный экземпляр Uncertainty Engine
_uncertainty_engine: UncertaintyEngine | None = None


def get_uncertainty_engine() -> UncertaintyEngine:
    """Получить глобальный Uncertainty Engine"""
    global _uncertainty_engine
    if _uncertainty_engine is None:
        _uncertainty_engine = UncertaintyEngine()
    return _uncertainty_engine


def reset_uncertainty_engine():
    """Сбросить Uncertainty Engine (для тестов)"""
    global _uncertainty_engine
    _uncertainty_engine = UncertaintyEngine()
