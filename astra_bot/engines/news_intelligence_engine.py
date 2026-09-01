"""
ASTRA BOT - News Intelligence Engine

Движок анализа новостей (ТЗ Пункты 4, 9, 10)

Анализирует:
- sentiment analysis
- entity recognition
- topic classification
- relevance scoring
- impact assessment
- source credibility
- temporal analysis
- multi-language support

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class Sentiment(str, Enum):
    """Сентимент новости"""
    STRONGLY_POSITIVE = "strongly_positive"
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    STRONGLY_NEGATIVE = "strongly_negative"


class EntityType(str, Enum):
    """Типы сущностей"""
    PERSON = "person"
    ORGANIZATION = "organization"
    COMPANY = "company"
    ASSET = "asset"
    CRYPTOCURRENCY = "cryptocurrency"
    STOCK = "stock"
    INDEX = "index"
    COUNTRY = "country"
    CURRENCY = "currency"
    EVENT = "event"
    LOCATION = "location"
    DATE = "date"
    TIME = "time"


class Topic(str, Enum):
    """Темы новостей"""
    MACROECONOMIC = "macroeconomic"
    POLITICAL = "political"
    FINANCIAL = "financial"
    TECHNOLOGY = "technology"
    REGULATION = "regulation"
    MARKET_MOVES = "market_moves"
    EARNINGS = "earnings"
    MERGERS_ACQUISITIONS = "mergers_acquisitions"
    CRYPTOCURRENCY = "cryptocurrency"
    COMMODITIES = "commodities"
    FOREX = "forex"
    BONDS = "bonds"
    ENERGY = "energy"
    HEALTHCARE = "healthcare"


class ImpactLevel(str, Enum):
    """Уровни влияния"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class SourceCredibility(str, Enum):
    """Надёжность источника"""
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    VERY_LOW = "very_low"


@dataclass
class RecognizedEntity:
    """Распознанная сущность"""
    entity: str
    entity_type: EntityType
    relevance: float = 0.0  # 0-1
    sentiment: Sentiment = Sentiment.NEUTRAL
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "entity_type": self.entity_type.value,
            "relevance": self.relevance,
            "sentiment": self.sentiment.value,
        }


@dataclass
class NewsArticle:
    """Статья новостей"""
    article_id: str
    title: str
    content: str
    url: str = ""
    source: str = ""
    author: str = ""
    
    # Время
    published_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    received_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Язык
    language: str = "en"
    
    # Анализ
    sentiment: Sentiment = Sentiment.NEUTRAL
    sentiment_score: float = 0.0  # -1 до 1
    
    # Сущности
    entities: list[RecognizedEntity] = field(default_factory=list)
    
    # Темы
    topics: list[Topic] = field(default_factory=list)
    
    # Влияние
    impact_level: ImpactLevel = ImpactLevel.NONE
    impact_score: float = 0.0
    
    # Релевантность
    relevance_score: float = 0.0
    
    # Надёжность источника
    source_credibility: SourceCredibility = SourceCredibility.MEDIUM
    
    # Символы (для финансовых новостей)
    symbols: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "source": self.source,
            "author": self.author,
            "published_time": self.published_time.isoformat(),
            "received_time": self.received_time.isoformat(),
            "language": self.language,
            "sentiment": self.sentiment.value,
            "sentiment_score": self.sentiment_score,
            "entities": [e.to_dict() for e in self.entities],
            "topics": [t.value for t in self.topics],
            "impact_level": self.impact_level.value,
            "impact_score": self.impact_score,
            "relevance_score": self.relevance_score,
            "source_credibility": self.source_credibility.value,
            "symbols": self.symbols,
        }


@dataclass
class NewsImpact:
    """Влияние новости на рынок"""
    article_id: str
    symbol: str
    
    # Влияние
    impact_level: ImpactLevel = ImpactLevel.NONE
    impact_score: float = 0.0
    
    # Ожидаемое направление
    expected_direction: str = "neutral"  # up/down/neutral
    
    # Ожидаемая волатильность
    expected_volatility: float = 0.0
    
    # Временной горизонт
    time_horizon: str = "1h"
    
    # Уверенность
    confidence: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "symbol": self.symbol,
            "impact_level": self.impact_level.value,
            "impact_score": self.impact_score,
            "expected_direction": self.expected_direction,
            "expected_volatility": self.expected_volatility,
            "time_horizon": self.time_horizon,
            "confidence": self.confidence,
        }


@dataclass
class NewsAnalysis:
    """Полный анализ новостей"""
    symbol: str
    timestamp: datetime
    
    # Статьи
    articles: list[NewsArticle] = field(default_factory=list)
    
    # Агрегированный анализ
    aggregated_sentiment: Sentiment = Sentiment.NEUTRAL
    aggregated_sentiment_score: float = 0.0
    
    # Агрегированное влияние
    aggregated_impact: ImpactLevel = ImpactLevel.NONE
    aggregated_impact_score: float = 0.0
    
    # Основные темы
    main_topics: list[Topic] = field(default_factory=list)
    
    # Основные сущности
    main_entities: list[RecognizedEntity] = field(default_factory=list)
    
    # Влияние на рынок
    impacts: list[NewsImpact] = field(default_factory=list)
    
    # Статистика
    statistics: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "articles": [a.to_dict() for a in self.articles],
            "aggregated_sentiment": self.aggregated_sentiment.value,
            "aggregated_sentiment_score": self.aggregated_sentiment_score,
            "aggregated_impact": self.aggregated_impact.value,
            "aggregated_impact_score": self.aggregated_impact_score,
            "main_topics": [t.value for t in self.main_topics],
            "main_entities": [e.to_dict() for e in self.main_entities],
            "impacts": [i.to_dict() for i in self.impacts],
            "statistics": self.statistics,
        }


class NewsIntelligenceEngine:
    """
    Движок анализа новостей.
    
    Анализирует новости и оценивает их влияние на рынки.
    """
    
    def __init__(self):
        # База знаний
        self._knowledge_base: dict[str, Any] = {
            "companies": {},
            "assets": {},
            "people": {},
            "topics": {},
        }
        
        # История статей
        self._articles: dict[str, NewsArticle] = {}
        
        # Пороги
        self.thresholds = {
            "positive_sentiment": 0.3,
            "strongly_positive_sentiment": 0.7,
            "negative_sentiment": -0.3,
            "strongly_negative_sentiment": -0.7,
            "high_impact": 0.7,
            "medium_impact": 0.4,
            "high_relevance": 0.7,
            "medium_relevance": 0.4,
        }
        
        # Модели (заглушки для реальных моделей ML)
        self._sentiment_model = None
        self._entity_model = None
        self._topic_model = None
    
    def add_article(
        self,
        article_id: str,
        title: str,
        content: str,
        url: str = "",
        source: str = "",
        author: str = "",
        published_time: datetime | None = None,
        language: str = "en",
    ) -> NewsArticle:
        """
        Добавить статью.
        
        Args:
            article_id: ID статьи
            title: Заголовок
            content: Содержимое
            url: URL
            source: Источник
            author: Автор
            published_time: Время публикации
            language: Язык
        
        Returns:
            Статья
        """
        article = NewsArticle(
            article_id=article_id,
            title=title,
            content=content,
            url=url,
            source=source,
            author=author,
            published_time=published_time or datetime.now(timezone.utc),
            received_time=datetime.now(timezone.utc),
            language=language,
        )
        
        # Проанализировать статью
        article = self.analyze_article(article)
        
        self._articles[article_id] = article
        
        return article
    
    def analyze_article(self, article: NewsArticle) -> NewsArticle:
        """
        Проанализировать статью.
        
        Args:
            article: Статья
        
        Returns:
            Статья с анализом
        """
        # Анализ сентимента
        article.sentiment, article.sentiment_score = self.analyze_sentiment(article)
        
        # Распознавание сущностей
        article.entities = self.recognize_entities(article)
        
        # Классификация тем
        article.topics = self.classify_topics(article)
        
        # Оценка влияния
        article.impact_level, article.impact_score = self.assess_impact(article)
        
        # Оценка релевантности
        article.relevance_score = self.assess_relevance(article)
        
        # Оценка надёжности источника
        article.source_credibility = self.assess_source_credibility(article)
        
        # Извлечение символов
        article.symbols = self.extract_symbols(article)
        
        return article
    
    def analyze_sentiment(self, article: NewsArticle) -> tuple[Sentiment, float]:
        """
        Проанализировать сентимент статьи.
        
        Args:
            article: Статья
        
        Returns:
            Сентимент и оценка
        """
        # Упрощённая логика анализа сентимента
        # В реальности нужно использовать модель ML
        
        content = f"{article.title} {article.content}".lower()
        
        positive_words = ['good', 'great', 'excellent', 'positive', 'bullish', 'up', 'rise', 'growth']
        negative_words = ['bad', 'poor', 'negative', 'bearish', 'down', 'fall', 'drop', 'crash']
        
        positive_count = sum(1 for word in positive_words if word in content)
        negative_count = sum(1 for word in negative_words if word in content)
        
        score = (positive_count - negative_count) / max(len(content.split()), 1)
        
        if score >= self.thresholds["strongly_positive_sentiment"]:
            sentiment = Sentiment.STRONGLY_POSITIVE
        elif score >= self.thresholds["positive_sentiment"]:
            sentiment = Sentiment.POSITIVE
        elif score <= self.thresholds["strongly_negative_sentiment"]:
            sentiment = Sentiment.STRONGLY_NEGATIVE
        elif score <= self.thresholds["negative_sentiment"]:
            sentiment = Sentiment.NEGATIVE
        else:
            sentiment = Sentiment.NEUTRAL
        
        return sentiment, score
    
    def recognize_entities(self, article: NewsArticle) -> list[RecognizedEntity]:
        """
        Распознать сущности в статье.
        
        Args:
            article: Статья
        
        Returns:
            Список сущностей
        """
        # Упрощённая логика распознавания сущностей
        # В реальности нужно использовать модель NER
        
        entities = []
        content = f"{article.title} {article.content}"
        
        # Распознавание компаний (упрощённое)
        companies = ['Apple', 'Microsoft', 'Google', 'Amazon', 'Tesla', 'Bitcoin', 'Ethereum']
        for company in companies:
            if company in content:
                entities.append(RecognizedEntity(
                    entity=company,
                    entity_type=EntityType.COMPANY,
                    relevance=0.8,
                ))
        
        # Распознавание криптовалют
        cryptocurrencies = ['Bitcoin', 'BTC', 'Ethereum', 'ETH', 'Solana', 'SOL', 'Cardano', 'ADA']
        for crypto in cryptocurrencies:
            if crypto in content:
                entities.append(RecognizedEntity(
                    entity=crypto,
                    entity_type=EntityType.CRYPTOCURRENCY,
                    relevance=0.9,
                ))
        
        # Распознавание стран
        countries = ['United States', 'China', 'Russia', 'Germany', 'France', 'Japan']
        for country in countries:
            if country in content:
                entities.append(RecognizedEntity(
                    entity=country,
                    entity_type=EntityType.COUNTRY,
                    relevance=0.7,
                ))
        
        return entities
    
    def classify_topics(self, article: NewsArticle) -> list[Topic]:
        """
        Классифицировать темы статьи.
        
        Args:
            article: Статья
        
        Returns:
            Список тем
        """
        # Упрощённая логика классификации тем
        content = f"{article.title} {article.content}".lower()
        
        topics = []
        
        # Макроэкономика
        macro_words = ['gdp', 'inflation', 'cpi', 'ppi', 'federal reserve', 'interest rate']
        if any(word in content for word in macro_words):
            topics.append(Topic.MACROECONOMIC)
        
        # Политика
        political_words = ['president', 'government', 'election', 'policy', 'law']
        if any(word in content for word in political_words):
            topics.append(Topic.POLITICAL)
        
        # Финансы
        financial_words = ['stock', 'market', 'price', 'trade', 'invest', 'profit']
        if any(word in content for word in financial_words):
            topics.append(Topic.FINANCIAL)
        
        # Технологии
        tech_words = ['technology', 'innovation', 'ai', 'blockchain', 'software']
        if any(word in content for word in tech_words):
            topics.append(Topic.TECHNOLOGY)
        
        # Регулирование
        regulation_words = ['regulation', 'regulator', 'sec', 'compliance', 'ban']
        if any(word in content for word in regulation_words):
            topics.append(Topic.REGULATION)
        
        # Криптовалюты
        crypto_words = ['bitcoin', 'ethereum', 'crypto', 'blockchain', 'defi', 'nft']
        if any(word in content for word in crypto_words):
            topics.append(Topic.CRYPTOCURRENCY)
        
        if not topics:
            topics.append(Topic.FINANCIAL)
        
        return topics
    
    def assess_impact(self, article: NewsArticle) -> tuple[ImpactLevel, float]:
        """
        Оценить влияние статьи.
        
        Args:
            article: Статья
        
        Returns:
            Уровень влияния и оценка
        """
        # Упрощённая логика оценки влияния
        score = 0.0
        
        # Учесть сентимент
        if article.sentiment == Sentiment.STRONGLY_POSITIVE or article.sentiment == Sentiment.STRONGLY_NEGATIVE:
            score += 0.3
        elif article.sentiment == Sentiment.POSITIVE or article.sentiment == Sentiment.NEGATIVE:
            score += 0.2
        
        # Учесть надёжность источника
        if article.source_credibility == SourceCredibility.VERY_HIGH:
            score += 0.2
        elif article.source_credibility == SourceCredibility.HIGH:
            score += 0.15
        elif article.source_credibility == SourceCredibility.LOW:
            score -= 0.1
        
        # Учесть темы
        high_impact_topics = [Topic.REGULATION, Topic.MACROECONOMIC, Topic.EARNINGS]
        if any(topic in article.topics for topic in high_impact_topics):
            score += 0.2
        
        # Учесть сущности
        high_impact_entities = [EntityType.COMPANY, EntityType.CRYPTOCURRENCY, EntityType.COUNTRY]
        if any(entity.entity_type in high_impact_entities for entity in article.entities):
            score += 0.1
        
        # Определить уровень
        if score >= self.thresholds["high_impact"]:
            impact_level = ImpactLevel.HIGH
        elif score >= self.thresholds["medium_impact"]:
            impact_level = ImpactLevel.MEDIUM
        else:
            impact_level = ImpactLevel.LOW
        
        return impact_level, score
    
    def assess_relevance(self, article: NewsArticle) -> float:
        """
        Оценить релевантность статьи.
        
        Args:
            article: Статья
        
        Returns:
            Оценка релевантности (0-1)
        """
        # Упрощённая логика
        score = 0.0
        
        # Учесть количество сущностей
        if article.entities:
            score += min(0.3, len(article.entities) * 0.1)
        
        # Учесть количество тем
        if article.topics:
            score += min(0.2, len(article.topics) * 0.1)
        
        # Учесть сентимент
        if article.sentiment != Sentiment.NEUTRAL:
            score += 0.2
        
        # Учесть длину статьи
        content_length = len(article.content.split())
        if content_length > 100:
            score += 0.1
        elif content_length > 50:
            score += 0.05
        
        return min(1.0, score)
    
    def assess_source_credibility(self, article: NewsArticle) -> SourceCredibility:
        """
        Оценить надёжность источника.
        
        Args:
            article: Статья
        
        Returns:
            Надёжность источника
        """
        # Упрощённая логика
        high_credibility_sources = ['reuters', 'bloomberg', 'wsj', 'ft', 'cnbc']
        low_credibility_sources = ['twitter', 'reddit', 'facebook']
        
        source_lower = article.source.lower()
        
        if any(s in source_lower for s in high_credibility_sources):
            return SourceCredibility.VERY_HIGH
        elif any(s in source_lower for s in low_credibility_sources):
            return SourceCredibility.LOW
        else:
            return SourceCredibility.MEDIUM
    
    def extract_symbols(self, article: NewsArticle) -> list[str]:
        """
        Извлечь символы из статьи.
        
        Args:
            article: Статья
        
        Returns:
            Список символов
        """
        symbols = []
        
        # Извлечение из сущностей
        for entity in article.entities:
            if entity.entity_type == EntityType.CRYPTOCURRENCY:
                # Маппинг названий на символы
                crypto_map = {
                    'Bitcoin': 'BTC',
                    'BTC': 'BTC',
                    'Ethereum': 'ETH',
                    'ETH': 'ETH',
                    'Solana': 'SOL',
                    'SOL': 'SOL',
                }
                if entity.entity in crypto_map:
                    symbols.append(crypto_map[entity.entity])
            elif entity.entity_type == EntityType.STOCK:
                symbols.append(entity.entity)
        
        # Извлечение из текста (упрощённое)
        content = f"{article.title} {article.content}"
        
        # Поиск символов криптовалют
        crypto_symbols = ['BTC', 'ETH', 'SOL', 'ADA', 'XRP', 'DOGE', 'DOT', 'AVAX']
        for symbol in crypto_symbols:
            if symbol in content:
                symbols.append(symbol)
        
        # Удалить дубликаты
        symbols = list(set(symbols))
        
        return symbols
    
    def assess_market_impact(self, article: NewsArticle, symbol: str) -> NewsImpact:
        """
        Оценить влияние новости на конкретный символ.
        
        Args:
            article: Статья
            symbol: Символ
        
        Returns:
            Влияние новости
        """
        # Проверить, упоминается ли символ в статье
        if symbol not in article.symbols:
            return NewsImpact(
                article_id=article.article_id,
                symbol=symbol,
                impact_level=ImpactLevel.NONE,
                confidence=0.0,
            )
        
        # Определить ожидаемое направление
        if article.sentiment == Sentiment.STRONGLY_POSITIVE or article.sentiment == Sentiment.POSITIVE:
            expected_direction = "up"
        elif article.sentiment == Sentiment.STRONGLY_NEGATIVE or article.sentiment == Sentiment.NEGATIVE:
            expected_direction = "down"
        else:
            expected_direction = "neutral"
        
        # Определить ожидаемую волатильность
        if article.impact_level == ImpactLevel.CRITICAL or article.impact_level == ImpactLevel.HIGH:
            expected_volatility = 0.05  # 5%
        elif article.impact_level == ImpactLevel.MEDIUM:
            expected_volatility = 0.02  # 2%
        else:
            expected_volatility = 0.01  # 1%
        
        # Определить уверенность
        confidence = article.relevance_score * 0.5 + article.impact_score * 0.3 + \
                   (0.2 if article.source_credibility == SourceCredibility.VERY_HIGH else 0.1)
        
        # Определить уровень влияния
        impact_score = article.impact_score * article.relevance_score
        
        if impact_score >= self.thresholds["high_impact"]:
            impact_level = ImpactLevel.HIGH
        elif impact_score >= self.thresholds["medium_impact"]:
            impact_level = ImpactLevel.MEDIUM
        else:
            impact_level = ImpactLevel.LOW
        
        return NewsImpact(
            article_id=article.article_id,
            symbol=symbol,
            impact_level=impact_level,
            impact_score=impact_score,
            expected_direction=expected_direction,
            expected_volatility=expected_volatility,
            confidence=confidence,
        )
    
    def analyze_news_for_symbol(
        self,
        symbol: str,
        time_range: tuple[datetime, datetime] | None = None,
    ) -> NewsAnalysis:
        """
        Проанализировать новости для символа.
        
        Args:
            symbol: Символ
            time_range: Временной диапазон
        
        Returns:
            Анализ новостей
        """
        # Получить статьи для символа
        articles = [a for a in self._articles.values() 
                   if symbol in a.symbols]
        
        if time_range:
            articles = [a for a in articles 
                       if time_range[0] <= a.published_time <= time_range[1]]
        
        # Сортировать по времени
        articles.sort(key=lambda x: x.published_time, reverse=True)
        
        if not articles:
            return NewsAnalysis(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
            )
        
        # Агрегировать сентимент
        sentiment_scores = [a.sentiment_score for a in articles]
        avg_sentiment = float(np.mean(sentiment_scores)) if sentiment_scores else 0.0
        
        if avg_sentiment >= self.thresholds["strongly_positive_sentiment"]:
            aggregated_sentiment = Sentiment.STRONGLY_POSITIVE
        elif avg_sentiment >= self.thresholds["positive_sentiment"]:
            aggregated_sentiment = Sentiment.POSITIVE
        elif avg_sentiment <= self.thresholds["strongly_negative_sentiment"]:
            aggregated_sentiment = Sentiment.STRONGLY_NEGATIVE
        elif avg_sentiment <= self.thresholds["negative_sentiment"]:
            aggregated_sentiment = Sentiment.NEGATIVE
        else:
            aggregated_sentiment = Sentiment.NEUTRAL
        
        # Агрегировать влияние
        impact_scores = [a.impact_score for a in articles]
        avg_impact = float(np.mean(impact_scores)) if impact_scores else 0.0
        
        if avg_impact >= self.thresholds["high_impact"]:
            aggregated_impact = ImpactLevel.HIGH
        elif avg_impact >= self.thresholds["medium_impact"]:
            aggregated_impact = ImpactLevel.MEDIUM
        else:
            aggregated_impact = ImpactLevel.LOW
        
        # Основные темы
        topic_counts = {}
        for article in articles:
            for topic in article.topics:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
        
        main_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        main_topics = [t for t, _ in main_topics]
        
        # Основные сущности
        entity_scores = {}
        for article in articles:
            for entity in article.entities:
                if entity.entity not in entity_scores:
                    entity_scores[entity.entity] = 0.0
                entity_scores[entity.entity] += entity.relevance
        
        main_entities = sorted(entity_scores.items(), key=lambda x: x[1], reverse=True)[:5]
        main_entities_list = [RecognizedEntity(
            entity=e,
            entity_type=next((ent.entity_type for ent in articles[0].entities if ent.entity == e), EntityType.ASSET),
            relevance=s,
        ) for e, s in main_entities]
        
        # Влияние на рынок
        impacts = [self.assess_market_impact(a, symbol) for a in articles]
        
        # Статистика
        statistics = {
            "num_articles": len(articles),
            "avg_sentiment_score": avg_sentiment,
            "avg_impact_score": avg_impact,
            "avg_relevance_score": float(np.mean([a.relevance_score for a in articles])) if articles else 0.0,
        }
        
        return NewsAnalysis(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            articles=articles,
            aggregated_sentiment=aggregated_sentiment,
            aggregated_sentiment_score=avg_sentiment,
            aggregated_impact=aggregated_impact,
            aggregated_impact_score=avg_impact,
            main_topics=main_topics,
            main_entities=main_entities_list,
            impacts=impacts,
            statistics=statistics,
        )
    
    def get_article(self, article_id: str) -> NewsArticle | None:
        """
        Получить статью.
        
        Args:
            article_id: ID статьи
        
        Returns:
            Статья или None
        """
        return self._articles.get(article_id)
    
    def search_articles(
        self,
        query: str = "",
        symbol: str = "",
        topic: Topic | None = None,
        sentiment: Sentiment | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[NewsArticle]:
        """
        Поиск статей.
        
        Args:
            query: Поисковый запрос
            symbol: Символ
            topic: Тема
            sentiment: Сентимент
            start_time: Начальное время
            end_time: Конечное время
            limit: Лимит результатов
        
        Returns:
            Список статей
        """
        results = []
        
        for article in self._articles.values():
            # Фильтрация по запросу
            if query:
                if query.lower() not in f"{article.title} {article.content}".lower():
                    continue
            
            # Фильтрация по символу
            if symbol and symbol not in article.symbols:
                continue
            
            # Фильтрация по теме
            if topic and topic not in article.topics:
                continue
            
            # Фильтрация по сентименту
            if sentiment and sentiment != article.sentiment:
                continue
            
            # Фильтрация по времени
            if start_time and article.published_time < start_time:
                continue
            if end_time and article.published_time > end_time:
                continue
            
            results.append(article)
        
        # Сортировать по времени
        results.sort(key=lambda x: x.published_time, reverse=True)
        
        return results[:limit]


# Глобальный экземпляр
_news_intelligence_engine: NewsIntelligenceEngine | None = None


def get_news_intelligence_engine() -> NewsIntelligenceEngine:
    """Получить глобальный News Intelligence Engine"""
    global _news_intelligence_engine
    if _news_intelligence_engine is None:
        _news_intelligence_engine = NewsIntelligenceEngine()
    return _news_intelligence_engine


def reset_news_intelligence_engine():
    """Сбросить News Intelligence Engine (для тестов)"""
    global _news_intelligence_engine
    _news_intelligence_engine = NewsIntelligenceEngine()
