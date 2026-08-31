"""
ASTRA BOT — Lesson Quality Engine

Оценка качества уроков (Master Specification v2, Section 53)

Lesson не должен быть:
- "BTC упал."

Он должен иметь форму:
- CONDITION
- OBSERVED EFFECT
- STATISTICAL EVIDENCE
- OOS RESULT
- CONFIDENCE
- LIMITATIONS
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class LessonQuality(str, Enum):
    """Качество урока"""
    POOR = "poor"
    FAIR = "fair"
    GOOD = "good"
    EXCELLENT = "excellent"


@dataclass
class LessonAssessment:
    """Оценка урока"""
    lesson_id: str
    quality: LessonQuality
    score: float  # 0-1
    
    # Детали оценки
    condition_quality: float = 0.0
    effect_quality: float = 0.0
    evidence_quality: float = 0.0
    oos_quality: float = 0.0
    confidence_quality: float = 0.0
    limitations_quality: float = 0.0
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "quality": self.quality.value,
            "score": self.score,
            "condition_quality": self.condition_quality,
            "effect_quality": self.effect_quality,
            "evidence_quality": self.evidence_quality,
            "oos_quality": self.oos_quality,
            "confidence_quality": self.confidence_quality,
            "limitations_quality": self.limitations_quality,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class Lesson:
    """Урок"""
    lesson_id: str
    condition: str
    observed_effect: str
    statistical_evidence: float
    oos_result: float
    confidence: float
    limitations: list[str] = field(default_factory=list)
    
    # Дополнительная информация
    symbol: str | None = None
    timeframe: str | None = None
    strategy: str | None = None
    
    # Временные метки
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "lesson_id": self.lesson_id,
            "condition": self.condition,
            "observed_effect": self.observed_effect,
            "statistical_evidence": self.statistical_evidence,
            "oos_result": self.oos_result,
            "confidence": self.confidence,
            "limitations": self.limitations,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        
        if self.symbol:
            result["symbol"] = self.symbol
        if self.timeframe:
            result["timeframe"] = self.timeframe
        if self.strategy:
            result["strategy"] = self.strategy
        
        return result


class LessonQualityEngine:
    """
    Движок оценки качества уроков.
    
    Проверяет, что уроки соответствуют требуемому формату и имеют достаточное качество.
    """
    
    def __init__(self):
        # Пороги качества
        self.thresholds = {
            "excellent": 0.9,
            "good": 0.7,
            "fair": 0.5,
            "poor": 0.0,
        }
        
        # Веса компонентов
        self.weights = {
            "condition": 0.25,
            "effect": 0.20,
            "evidence": 0.25,
            "oos": 0.15,
            "confidence": 0.10,
            "limitations": 0.05,
        }
    
    def assess_condition_quality(self, condition: str) -> float:
        """
        Оценить качество условия.
        
        Args:
            condition: Условие
        
        Returns:
            Оценка качества (0-1)
        """
        if not condition or len(condition.strip()) == 0:
            return 0.0
        
        # Проверить длину
        if len(condition) < 10:
            return 0.3
        
        # Проверить конкретность
        # Хорошее условие должно содержать конкретные значения или события
        specific_keywords = [
            ">", "<", "=", "==", "!=", ">=", "<=",
            "increased", "decreased", "above", "below",
            "when", "if", "after", "before", "during"
        ]
        
        has_specific = any(keyword in condition.lower() for keyword in specific_keywords)
        
        if has_specific:
            return 0.8
        else:
            # Проверить на общие фразы
            general_phrases = [
                "market", "price", "btc", "eth",
                "went up", "went down", "moved"
            ]
            
            has_general = any(phrase in condition.lower() for phrase in general_phrases)
            
            if has_general and len(condition) < 20:
                return 0.2  # Слишком общее
            
            return 0.5
    
    def assess_effect_quality(self, observed_effect: str) -> float:
        """
        Оценить качество наблюдаемого эффекта.
        
        Args:
            observed_effect: Наблюдаемый эффект
        
        Returns:
            Оценка качества (0-1)
        """
        if not observed_effect or len(observed_effect.strip()) == 0:
            return 0.0
        
        # Проверить длину
        if len(observed_effect) < 10:
            return 0.3
        
        # Проверить конкретность
        specific_keywords = [
            "increased by", "decreased by", "rose by", "fell by",
            "profit", "loss", "win rate", "sharpe",
            "%", "pips", "points"
        ]
        
        has_specific = any(keyword in observed_effect.lower() for keyword in specific_keywords)
        
        if has_specific:
            return 0.9
        else:
            return 0.5
    
    def assess_evidence_quality(self, statistical_evidence: float) -> float:
        """
        Оценить качество статистического доказательства.
        
        Args:
            statistical_evidence: Статистическое доказательство (p-value, R², etc.)
        
        Returns:
            Оценка качества (0-1)
        """
        # Предполагаем, что statistical_evidence это p-value или аналогичная метрика
        # Чем меньше p-value, тем лучше доказательство
        
        if statistical_evidence <= 0.01:
            return 1.0
        elif statistical_evidence <= 0.05:
            return 0.8
        elif statistical_evidence <= 0.10:
            return 0.6
        elif statistical_evidence <= 0.20:
            return 0.4
        else:
            return 0.2
    
    def assess_oos_quality(self, oos_result: float) -> float:
        """
        Оценить качество OOS результата.
        
        Args:
            oos_result: Результат OOS
        
        Returns:
            Оценка качества (0-1)
        """
        # Предполагаем, что oos_result это доходность или аналогичная метрика
        
        if oos_result > 0.1:  # 10% доходность
            return 1.0
        elif oos_result > 0.05:
            return 0.8
        elif oos_result > 0:
            return 0.6
        elif oos_result > -0.05:
            return 0.4
        else:
            return 0.2
    
    def assess_confidence_quality(self, confidence: float) -> float:
        """
        Оценить качество уверенности.
        
        Args:
            confidence: Уверенность (0-1)
        
        Returns:
            Оценка качества (0-1)
        """
        # Уверенность должна быть обоснованной
        # Слишком высокая или слишком низкая уверенность может быть подозрительной
        
        if confidence >= 0.9:
            return 0.8  # Очень высокая уверенность требует сильных доказательств
        elif confidence >= 0.7:
            return 1.0
        elif confidence >= 0.5:
            return 0.8
        elif confidence >= 0.3:
            return 0.6
        else:
            return 0.4
    
    def assess_limitations_quality(self, limitations: list[str]) -> float:
        """
        Оценить качество ограничений.
        
        Args:
            limitations: Список ограничений
        
        Returns:
            Оценка качества (0-1)
        """
        if not limitations:
            return 0.5  # Нет ограничений указано
        
        # Хорошие ограничения должны быть конкретными
        if len(limitations) >= 3:
            return 0.9
        elif len(limitations) >= 1:
            return 0.7
        else:
            return 0.5
    
    def assess_lesson(self, lesson: Lesson) -> LessonAssessment:
        """
        Оценить качество урока.
        
        Args:
            lesson: Урок
        
        Returns:
            LessonAssessment
        """
        # Оценить компоненты
        condition_quality = self.assess_condition_quality(lesson.condition)
        effect_quality = self.assess_effect_quality(lesson.observed_effect)
        evidence_quality = self.assess_evidence_quality(lesson.statistical_evidence)
        oos_quality = self.assess_oos_quality(lesson.oos_result)
        confidence_quality = self.assess_confidence_quality(lesson.confidence)
        limitations_quality = self.assess_limitations_quality(lesson.limitations)
        
        # Рассчитать общую оценку
        score = (
            self.weights["condition"] * condition_quality +
            self.weights["effect"] * effect_quality +
            self.weights["evidence"] * evidence_quality +
            self.weights["oos"] * oos_quality +
            self.weights["confidence"] * confidence_quality +
            self.weights["limitations"] * limitations_quality
        )
        
        # Определить качество
        if score >= self.thresholds["excellent"]:
            quality = LessonQuality.EXCELLENT
        elif score >= self.thresholds["good"]:
            quality = LessonQuality.GOOD
        elif score >= self.thresholds["fair"]:
            quality = LessonQuality.FAIR
        else:
            quality = LessonQuality.POOR
        
        # Сгенерировать рекомендации
        recommendations = []
        
        if condition_quality < 0.5:
            recommendations.append("Improve condition specificity")
        if effect_quality < 0.5:
            recommendations.append("Provide more specific observed effect")
        if evidence_quality < 0.5:
            recommendations.append("Strengthen statistical evidence")
        if oos_quality < 0.5:
            recommendations.append("Improve OOS validation")
        if confidence_quality < 0.5:
            recommendations.append("Justify confidence level")
        if limitations_quality < 0.5:
            recommendations.append("Add more limitations")
        
        assessment = LessonAssessment(
            lesson_id=lesson.lesson_id,
            quality=quality,
            score=score,
            condition_quality=condition_quality,
            effect_quality=effect_quality,
            evidence_quality=evidence_quality,
            oos_quality=oos_quality,
            confidence_quality=confidence_quality,
            limitations_quality=limitations_quality,
            recommendations=recommendations
        )
        
        return assessment
    
    def is_lesson_valid(self, lesson: Lesson) -> bool:
        """
        Проверить, валиден ли урок.
        
        Args:
            lesson: Урок
        
        Returns:
            True если урок валиден
        """
        assessment = self.assess_lesson(lesson)
        
        # Урок валиден, если его качество не POOR
        return assessment.quality != LessonQuality.POOR
    
    def filter_quality_lessons(
        self,
        lessons: list[Lesson],
        min_quality: LessonQuality = LessonQuality.FAIR
    ) -> list[Lesson]:
        """
        Отфильтровать уроки по качеству.
        
        Args:
            lessons: Список уроков
            min_quality: Минимальное качество
        
        Returns:
            Отфильтрованный список
        """
        quality_thresholds = {
            LessonQuality.EXCELLENT: 0.9,
            LessonQuality.GOOD: 0.7,
            LessonQuality.FAIR: 0.5,
            LessonQuality.POOR: 0.0,
        }
        
        min_score = quality_thresholds[min_quality]
        
        filtered = []
        for lesson in lessons:
            assessment = self.assess_lesson(lesson)
            if assessment.score >= min_score:
                filtered.append(lesson)
        
        return filtered
    
    def get_quality_statistics(self, lessons: list[Lesson]) -> dict[str, Any]:
        """
        Получить статистику качества уроков.
        
        Args:
            lessons: Список уроков
        
        Returns:
            Статистика качества
        """
        if not lessons:
            return {}
        
        quality_counts = {
            LessonQuality.EXCELLENT: 0,
            LessonQuality.GOOD: 0,
            LessonQuality.FAIR: 0,
            LessonQuality.POOR: 0,
        }
        
        scores = []
        for lesson in lessons:
            assessment = self.assess_lesson(lesson)
            quality_counts[assessment.quality] += 1
            scores.append(assessment.score)
        
        return {
            "total": len(lessons),
            "by_quality": {k.value: v for k, v in quality_counts.items()},
            "avg_score": np.mean(scores),
            "std_score": np.std(scores),
            "min_score": min(scores),
            "max_score": max(scores),
        }


# Глобальный экземпляр Lesson Quality Engine
_lesson_quality_engine: LessonQualityEngine | None = None


def get_lesson_quality_engine() -> LessonQualityEngine:
    """Получить глобальный Lesson Quality Engine"""
    global _lesson_quality_engine
    if _lesson_quality_engine is None:
        _lesson_quality_engine = LessonQualityEngine()
    return _lesson_quality_engine


def reset_lesson_quality_engine():
    """Сбросить Lesson Quality Engine (для тестов)"""
    global _lesson_quality_engine
    _lesson_quality_engine = LessonQualityEngine()
