"""
ASTRA BOT - Academic Research Engine

Движок академических исследований (ТЗ Пункты 49-51, 94-95)

Исследует:
- Применение academic research к финансовым рынкам
- Backtest academic strategies
- Оценка применимости academic research к текущему рынку

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

from .. import models

logger = logging.getLogger(__name__)


class ResearchCategory(str, Enum):
    """Категории академических исследований"""
    MARKET_MICROSTRUCTURE = "market_microstructure"
    BEHAVIORAL_FINANCE = "behavioral_finance"
    ASSET_PRICING = "asset_pricing"
    PORTFOLIO_OPTIMIZATION = "portfolio_optimization"
    RISK_MANAGEMENT = "risk_management"
    MACHINE_LEARNING = "machine_learning"
    TIME_SERIES_ANALYSIS = "time_series_analysis"
    MARKET_EFFICIENCY = "market_efficiency"
    ALGORITHMIC_TRADING = "algorithmic_trading"


class ResearchStatus(str, Enum):
    """Статус исследования"""
    PROPOSED = "proposed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    VALIDATED = "validated"
    REJECTED = "rejected"


class ApplicabilityLevel(str, Enum):
    """Уровень применимости"""
    HIGHLY_APPLICABLE = "highly_applicable"
    MODERATELY_APPLICABLE = "moderately_applicable"
    LITTLE_APPLICABLE = "little_applicable"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class ResearchPaper:
    """Научная статья"""
    paper_id: str
    title: str
    authors: list[str]
    publication_date: datetime | None = None
    journal: str = ""
    doi: str = ""
    
    # Категория
    category: ResearchCategory = ResearchCategory.ALGORITHMIC_TRADING
    
    # Краткое описание
    abstract: str = ""
    
    # Ключевые слова
    keywords: list[str] = field(default_factory=list)
    
    # Методология
    methodology: str = ""
    
    # Основные выводы
    findings: str = ""
    
    # Применимость к финансовым рынкам
    applicability: ApplicabilityLevel = ApplicabilityLevel.MODERATELY_APPLICABLE
    
    # Цитаты
    citations: int = 0
    
    # Ссылка
    url: str = ""
    
    # Временная метка добавления
    added_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "publication_date": self.publication_date.isoformat() if self.publication_date else None,
            "journal": self.journal,
            "doi": self.doi,
            "category": self.category.value,
            "abstract": self.abstract,
            "keywords": self.keywords,
            "methodology": self.methodology,
            "findings": self.findings,
            "applicability": self.applicability.value,
            "citations": self.citations,
            "url": self.url,
            "added_timestamp": self.added_timestamp.isoformat(),
        }


@dataclass
class ResearchStrategy:
    """Стратегия на основе академического исследования"""
    strategy_id: str
    paper_id: str
    strategy_name: str
    
    # Описание
    description: str = ""
    
    # Параметры
    parameters: dict[str, Any] = field(default_factory=dict)
    
    # Бэктест
    backtest_results: dict[str, Any] = field(default_factory=dict)
    
    # Производительность
    performance_metrics: dict[str, float] = field(default_factory=dict)
    
    # Статус
    status: ResearchStatus = ResearchStatus.PROPOSED
    
    # Применимость
    applicability: ApplicabilityLevel = ApplicabilityLevel.MODERATELY_APPLICABLE
    
    # Ограничения
    limitations: list[str] = field(default_factory=list)
    
    # Временная метка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "paper_id": self.paper_id,
            "strategy_name": self.strategy_name,
            "description": self.description,
            "parameters": self.parameters,
            "backtest_results": self.backtest_results,
            "performance_metrics": self.performance_metrics,
            "status": self.status.value,
            "applicability": self.applicability.value,
            "limitations": self.limitations,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ApplicabilityAssessment:
    """Оценка применимости исследования к текущему рынку"""
    assessment_id: str
    paper_id: str
    strategy_id: str
    
    # Временной горизонт
    time_horizon: str = "1h"
    
    # Текущее состояние рынка
    market_state: dict[str, Any] = field(default_factory=dict)
    
    # Применимость
    applicability_level: ApplicabilityLevel = ApplicabilityLevel.MODERATELY_APPLICABLE
    applicability_score: float = 0.5  # 0-1
    
    # Факторы применимости
    factors: dict[str, float] = field(default_factory=dict)
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    # Уверенность
    confidence: float = 0.5
    
    # Временная метка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "paper_id": self.paper_id,
            "strategy_id": self.strategy_id,
            "time_horizon": self.time_horizon,
            "market_state": self.market_state,
            "applicability_level": self.applicability_level.value,
            "applicability_score": self.applicability_score,
            "factors": self.factors,
            "recommendations": self.recommendations,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AcademicResearchAnalysis:
    """Полный анализ академического исследования"""
    analysis_id: str
    paper_id: str
    
    # Статья
    paper: ResearchPaper
    
    # Стратегии
    strategies: list[ResearchStrategy] = field(default_factory=list)
    
    # Оценки применимости
    assessments: list[ApplicabilityAssessment] = field(default_factory=list)
    
    # Итоговый вывод
    conclusion: str = ""
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    # Временная метка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "paper_id": self.paper_id,
            "paper": self.paper.to_dict(),
            "strategies": [s.to_dict() for s in self.strategies],
            "assessments": [a.to_dict() for a in self.assessments],
            "conclusion": self.conclusion,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat(),
        }


class AcademicResearchEngine:
    """
    Движок академических исследований.
    
    Исследует и применяет академические исследования к финансовым рынкам.
    """
    
    def __init__(self):
        # База данных статей
        self._papers: dict[str, ResearchPaper] = {}
        
        # Стратегии
        self._strategies: dict[str, ResearchStrategy] = {}
        
        # Оценки применимости
        self._assessments: dict[str, ApplicabilityAssessment] = {}
        
        # Анализы
        self._analyses: dict[str, AcademicResearchAnalysis] = {}
        
        # Пороги
        self.thresholds = {
            "high_applicability_score": 0.8,
            "moderate_applicability_score": 0.6,
            "low_applicability_score": 0.4,
            "min_citations": 10,
            "recent_publication_years": 5,
        }
    
    def add_paper(
        self,
        paper_id: str,
        title: str,
        authors: list[str],
        category: ResearchCategory = ResearchCategory.ALGORITHMIC_TRADING,
        abstract: str = "",
        publication_date: datetime | None = None,
        journal: str = "",
        doi: str = "",
        keywords: list[str] | None = None,
        methodology: str = "",
        findings: str = "",
        applicability: ApplicabilityLevel = ApplicabilityLevel.MODERATELY_APPLICABLE,
        citations: int = 0,
        url: str = "",
    ) -> ResearchPaper:
        """
        Добавить научную статью.
        
        Args:
            paper_id: ID статьи
            title: Название
            authors: Авторы
            category: Категория
            abstract: Аннотация
            publication_date: Дата публикации
            journal: Журнал
            doi: DOI
            keywords: Ключевые слова
            methodology: Методология
            findings: Основные выводы
            applicability: Применимость
            citations: Цитаты
            url: Ссылка
        
        Returns:
            Статья
        """
        paper = ResearchPaper(
            paper_id=paper_id,
            title=title,
            authors=authors,
            category=category,
            abstract=abstract,
            publication_date=publication_date,
            journal=journal,
            doi=doi,
            keywords=keywords or [],
            methodology=methodology,
            findings=findings,
            applicability=applicability,
            citations=citations,
            url=url,
        )
        
        self._papers[paper_id] = paper
        
        return paper
    
    def create_strategy(
        self,
        strategy_id: str,
        paper_id: str,
        strategy_name: str,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> ResearchStrategy:
        """
        Создать стратегию на основе исследования.
        
        Args:
            strategy_id: ID стратегии
            paper_id: ID статьи
            strategy_name: Название стратегии
            description: Описание
            parameters: Параметры
        
        Returns:
            Стратегия
        """
        strategy = ResearchStrategy(
            strategy_id=strategy_id,
            paper_id=paper_id,
            strategy_name=strategy_name,
            description=description,
            parameters=parameters or {},
            status=ResearchStatus.PROPOSED,
        )
        
        self._strategies[strategy_id] = strategy
        
        return strategy
    
    def backtest_strategy(
        self,
        strategy_id: str,
        candles: list[models.Candle],
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Протестировать стратегию.
        
        Args:
            strategy_id: ID стратегии
            candles: Список свечей
            parameters: Параметры
        
        Returns:
            Результаты бэктеста
        """
        strategy = self._strategies.get(strategy_id)
        if not strategy:
            return {"error": "Strategy not found"}
        
        if not candles:
            return {"error": "No candle data"}
        
        # Упрощённая логика бэктеста
        # В реальности нужно реализовать конкретную стратегию
        closes = [float(c.close) for c in candles]
        
        # Пример: Простая стратегия на основе скользящих средних
        if "fast_period" in strategy.parameters and "slow_period" in strategy.parameters:
            fast_period = strategy.parameters.get("fast_period", 10)
            slow_period = strategy.parameters.get("slow_period", 50)
            
            # Рассчитать скользящие средние
            fast_ma = []
            slow_ma = []
            
            for i in range(len(closes)):
                if i >= fast_period - 1:
                    fast_ma.append(float(np.mean(closes[i-fast_period+1:i+1])))
                else:
                    fast_ma.append(np.nan)
                
                if i >= slow_period - 1:
                    slow_ma.append(float(np.mean(closes[i-slow_period+1:i+1])))
                else:
                    slow_ma.append(np.nan)
            
            # Сигналы
            signals = []
            for i in range(1, len(fast_ma)):
                if fast_ma[i] > slow_ma[i] and fast_ma[i-1] <= slow_ma[i-1]:
                    signals.append("buy")
                elif fast_ma[i] < slow_ma[i] and fast_ma[i-1] >= slow_ma[i-1]:
                    signals.append("sell")
                else:
                    signals.append("hold")
            
            # Рассчитать PnL
            pnl = 0.0
            position = 0  # 0 = нет позиции, 1 = long, -1 = short
            entry_price = 0.0
            
            for i in range(1, len(signals)):
                if signals[i] == "buy" and position == 0:
                    position = 1
                    entry_price = closes[i]
                elif signals[i] == "sell" and position == 1:
                    pnl += (closes[i] - entry_price) / entry_price * 100
                    position = 0
                elif signals[i] == "sell" and position == 0:
                    position = -1
                    entry_price = closes[i]
                elif signals[i] == "buy" and position == -1:
                    pnl += (entry_price - closes[i]) / entry_price * 100
                    position = 0
            
            # Рассчитать метрики
            total_return = pnl
            num_trades = signals.count("buy") + signals.count("sell")
            win_rate = 0.5  # Упрощённое значение
            sharpe_ratio = 0.0  # Упрощённое значение
            
            # Обновить стратегию
            strategy.backtest_results = {
                "total_return_pct": total_return,
                "num_trades": num_trades,
                "win_rate": win_rate,
                "sharpe_ratio": sharpe_ratio,
            }
            
            strategy.performance_metrics = {
                "return_pct": total_return,
                "sharpe_ratio": sharpe_ratio,
                "max_drawdown": 0.0,
                "profit_factor": 1.0,
            }
            
            strategy.status = ResearchStatus.COMPLETED
            
            return {
                "total_return_pct": total_return,
                "num_trades": num_trades,
                "win_rate": win_rate,
                "sharpe_ratio": sharpe_ratio,
            }
        
        return {"error": "Strategy parameters not supported"}
    
    def assess_applicability(
        self,
        assessment_id: str,
        strategy_id: str,
        market_state: dict[str, Any],
        time_horizon: str = "1h",
    ) -> ApplicabilityAssessment:
        """
        Оценить применимость стратегии к текущему рынку.
        
        Args:
            assessment_id: ID оценки
            strategy_id: ID стратегии
            market_state: Текущее состояние рынка
            time_horizon: Временной горизонт
        
        Returns:
            Оценка применимости
        """
        strategy = self._strategies.get(strategy_id)
        if not strategy:
            raise ValueError("Strategy not found")
        
        paper = self._papers.get(strategy.paper_id)
        if not paper:
            raise ValueError("Paper not found")
        
        # Оценить применимость
        score = 0.5
        factors = {}
        
        # Фактор 1: Соответствие временного горизонта
        if time_horizon == paper.category.value:
            factors["time_horizon_match"] = 1.0
            score += 0.1
        else:
            factors["time_horizon_match"] = 0.5
        
        # Фактор 2: Состояние рынка
        # Упрощённая логика - нужно доработать
        if market_state.get("volatility") == "high":
            factors["market_volatility"] = 0.8 if paper.category == ResearchCategory.RISK_MANAGEMENT else 0.5
        else:
            factors["market_volatility"] = 0.5
        
        # Фактор 3: Производительность стратегии
        if strategy.performance_metrics.get("return_pct", 0) > 0:
            factors["strategy_performance"] = min(1.0, strategy.performance_metrics["return_pct"] / 10)
            score += 0.1
        else:
            factors["strategy_performance"] = 0.3
        
        # Фактор 4: Применимость статьи
        if paper.applicability == ApplicabilityLevel.HIGHLY_APPLICABLE:
            factors["paper_applicability"] = 1.0
            score += 0.1
        elif paper.applicability == ApplicabilityLevel.MODERATELY_APPLICABLE:
            factors["paper_applicability"] = 0.7
            score += 0.05
        else:
            factors["paper_applicability"] = 0.3
        
        # Фактор 5: Цитаты
        if paper.citations >= self.thresholds["min_citations"]:
            factors["citations"] = min(1.0, paper.citations / 100)
            score += 0.05
        else:
            factors["citations"] = 0.3
        
        # Определить уровень применимости
        if score >= self.thresholds["high_applicability_score"]:
            applicability_level = ApplicabilityLevel.HIGHLY_APPLICABLE
        elif score >= self.thresholds["moderate_applicability_score"]:
            applicability_level = ApplicabilityLevel.MODERATELY_APPLICABLE
        elif score >= self.thresholds["low_applicability_score"]:
            applicability_level = ApplicabilityLevel.LITTLE_APPLICABLE
        else:
            applicability_level = ApplicabilityLevel.NOT_APPLICABLE
        
        # Создать рекомендации
        recommendations = []
        
        if applicability_level == ApplicabilityLevel.HIGHLY_APPLICABLE:
            recommendations.append("Strongly recommended for use")
            recommendations.append("Consider allocating significant capital")
        elif applicability_level == ApplicabilityLevel.MODERATELY_APPLICABLE:
            recommendations.append("Recommended for use with caution")
            recommendations.append("Consider limited capital allocation")
        elif applicability_level == ApplicabilityLevel.LITTLE_APPLICABLE:
            recommendations.append("Use with extreme caution")
            recommendations.append("Consider paper trading first")
        else:
            recommendations.append("Not recommended for use")
            recommendations.append("Consider alternative strategies")
        
        # Уверенность
        confidence = min(1.0, 0.5 + (score - 0.5) * 0.5)
        
        assessment = ApplicabilityAssessment(
            assessment_id=assessment_id,
            paper_id=strategy.paper_id,
            strategy_id=strategy_id,
            time_horizon=time_horizon,
            market_state=market_state,
            applicability_level=applicability_level,
            applicability_score=score,
            factors=factors,
            recommendations=recommendations,
            confidence=confidence,
        )
        
        self._assessments[assessment_id] = assessment
        
        return assessment
    
    def analyze_paper(
        self,
        paper_id: str,
    ) -> AcademicResearchAnalysis:
        """
        Проанализировать статью.
        
        Args:
            paper_id: ID статьи
        
        Returns:
            Полный анализ
        """
        paper = self._papers.get(paper_id)
        if not paper:
            raise ValueError("Paper not found")
        
        # Найти стратегии для статьи
        strategies = [s for s in self._strategies.values() if s.paper_id == paper_id]
        
        # Найти оценки для стратегий
        assessments = [a for a in self._assessments.values() if a.paper_id == paper_id]
        
        # Создать вывод
        conclusion = f"Analysis of paper '{paper.title}' by {', '.join(paper.authors)}"
        
        if strategies:
            conclusion += f"\nFound {len(strategies)} strategies based on this paper"
            
            # Добавить информацию о производительности
            profitable_strategies = [s for s in strategies if s.performance_metrics.get("return_pct", 0) > 0]
            if profitable_strategies:
                conclusion += f"\n{len(profitable_strategies)} strategies show positive returns"
        
        if assessments:
            avg_score = np.mean([a.applicability_score for a in assessments])
            conclusion += f"\nAverage applicability score: {avg_score:.2f}"
        
        # Создать рекомендации
        recommendations = []
        
        if strategies and assessments:
            avg_score = np.mean([a.applicability_score for a in assessments])
            if avg_score >= self.thresholds["high_applicability_score"]:
                recommendations.append("Consider implementing strategies from this paper")
            elif avg_score >= self.thresholds["moderate_applicability_score"]:
                recommendations.append("Consider further research on this paper")
        
        if not strategies:
            recommendations.append("Consider developing strategies based on this paper")
        
        analysis = AcademicResearchAnalysis(
            analysis_id=f"analysis_{paper_id}",
            paper_id=paper_id,
            paper=paper,
            strategies=strategies,
            assessments=assessments,
            conclusion=conclusion,
            recommendations=recommendations,
        )
        
        self._analyses[f"analysis_{paper_id}"] = analysis
        
        return analysis
    
    def get_paper(self, paper_id: str) -> ResearchPaper | None:
        """
        Получить статью.
        
        Args:
            paper_id: ID статьи
        
        Returns:
            Статья или None
        """
        return self._papers.get(paper_id)
    
    def get_strategy(self, strategy_id: str) -> ResearchStrategy | None:
        """
        Получить стратегию.
        
        Args:
            strategy_id: ID стратегии
        
        Returns:
            Стратегия или None
        """
        return self._strategies.get(strategy_id)
    
    def get_assessment(self, assessment_id: str) -> ApplicabilityAssessment | None:
        """
        Получить оценку применимости.
        
        Args:
            assessment_id: ID оценки
        
        Returns:
            Оценка или None
        """
        return self._assessments.get(assessment_id)
    
    def get_analysis(self, analysis_id: str) -> AcademicResearchAnalysis | None:
        """
        Получить анализ.
        
        Args:
            analysis_id: ID анализа
        
        Returns:
            Анализ или None
        """
        return self._analyses.get(analysis_id)
    
    def search_papers(
        self,
        category: ResearchCategory | None = None,
        author: str = "",
        keyword: str = "",
        min_citations: int = 0,
    ) -> list[ResearchPaper]:
        """
        Поиск статей.
        
        Args:
            category: Категория
            author: Автор
            keyword: Ключевое слово
            min_citations: Минимальное количество цитирований
        
        Returns:
            Список статей
        """
        results = []
        for paper in self._papers.values():
            if category and paper.category != category:
                continue
            if author and author not in paper.authors:
                continue
            if keyword and keyword.lower() not in paper.title.lower() and \
               keyword.lower() not in paper.abstract.lower() and \
               keyword.lower() not in str(paper.keywords).lower():
                continue
            if paper.citations < min_citations:
                continue
            results.append(paper)
        
        return results
    
    def search_strategies(
        self,
        paper_id: str = "",
        status: ResearchStatus | None = None,
        min_return: float = -999,
    ) -> list[ResearchStrategy]:
        """
        Поиск стратегий.
        
        Args:
            paper_id: ID статьи
            status: Статус
            min_return: Минимальный возврат
        
        Returns:
            Список стратегий
        """
        results = []
        for strategy in self._strategies.values():
            if paper_id and strategy.paper_id != paper_id:
                continue
            if status and strategy.status != status:
                continue
            if strategy.performance_metrics.get("return_pct", 0) < min_return:
                continue
            results.append(strategy)
        
        return results


# Глобальный экземпляр
_academic_research_engine: AcademicResearchEngine | None = None


def get_academic_research_engine() -> AcademicResearchEngine:
    """Получить глобальный Academic Research Engine"""
    global _academic_research_engine
    if _academic_research_engine is None:
        _academic_research_engine = AcademicResearchEngine()
    return _academic_research_engine


def reset_academic_research_engine():
    """Сбросить Academic Research Engine (для тестов)"""
    global _academic_research_engine
    _academic_research_engine = AcademicResearchEngine()
