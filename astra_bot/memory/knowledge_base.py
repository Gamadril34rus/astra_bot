"""
ASTRA BOT — Knowledge Base

База знаний (Master Specification v2, Section 54)

Хранить отдельно:
- INVALIDATED
- FAILED
- UNSTABLE
- NO_EDGE

Неудачные исследования не удалять.
Они должны предотвращать повторение бессмысленных экспериментов.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeType(str, Enum):
    """Типы знаний"""
    VALIDATED = "validated"
    INVALIDATED = "invalidated"
    FAILED = "failed"
    UNSTABLE = "unstable"
    NO_EDGE = "no_edge"


@dataclass
class KnowledgeItem:
    """Элемент знаний"""
    knowledge_id: str
    knowledge_type: KnowledgeType
    
    # Содержимое
    title: str
    description: str
    data: dict[str, Any] = field(default_factory=dict)
    
    # Метаданные
    confidence: float = 0.0
    sample_size: int = 0
    validation_status: str = "unvalidated"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    source: str = ""
    
    # Связи
    related_experiments: list[str] = field(default_factory=list)
    related_hypotheses: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "knowledge_type": self.knowledge_type.value,
            "title": self.title,
            "description": self.description,
            "data": self.data,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "validation_status": self.validation_status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source": self.source,
            "related_experiments": self.related_experiments,
            "related_hypotheses": self.related_hypotheses,
        }


@dataclass
class NegativeKnowledge:
    """Негативные знания (что НЕ работает)"""
    knowledge_id: str
    reason: str  # INVALIDATED, FAILED, UNSTABLE, NO_EDGE
    
    # Что не работает
    failed_component: str  # feature, strategy, model, etc.
    component_name: str
    
    # Детали
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    
    # Временные метки
    discovered_at: datetime = field(default_factory=datetime.now)
    confirmed_at: datetime | None = None
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "knowledge_id": self.knowledge_id,
            "reason": self.reason,
            "failed_component": self.failed_component,
            "component_name": self.component_name,
            "description": self.description,
            "evidence": self.evidence,
            "discovered_at": self.discovered_at.isoformat(),
        }
        
        if self.confirmed_at:
            result["confirmed_at"] = self.confirmed_at.isoformat()
        
        return result


class KnowledgeBase:
    """
    База знаний.
    
    Хранит все знания системы, включая негативные знания
    о том, что не работает.
    """
    
    def __init__(self):
        # Хранение знаний
        self.knowledge: dict[str, KnowledgeItem] = {}
        
        # Негативные знания
        self.negative_knowledge: dict[str, NegativeKnowledge] = {}
        
        # Индексы
        self.indexes: dict[str, dict[str, list[str]]] = {
            "by_type": {},
            "by_component": {},
            "by_source": {},
        }
        
        # Счётчики
        self.knowledge_counter = 0
        self.negative_knowledge_counter = 0
    
    def _generate_knowledge_id(self) -> str:
        """Сгенерировать идентификатор знания"""
        self.knowledge_counter += 1
        return f"KNOW_{self.knowledge_counter:05d}"
    
    def _generate_negative_knowledge_id(self) -> str:
        """Сгенерировать идентификатор негативного знания"""
        self.negative_knowledge_counter += 1
        return f"NEG_{self.negative_knowledge_counter:05d}"
    
    def add_knowledge(
        self,
        knowledge_type: KnowledgeType,
        title: str,
        description: str,
        data: dict[str, Any] = None,
        confidence: float = 0.5,
        sample_size: int = 1,
        source: str = "",
        related_experiments: list[str] = None,
        related_hypotheses: list[str] = None
    ) -> str:
        """
        Добавить знание.
        
        Args:
            knowledge_type: Тип знания
            title: Заголовок
            description: Описание
            data: Данные
            confidence: Уверенность
            sample_size: Размер выборки
            source: Источник
            related_experiments: Связанные эксперименты
            related_hypotheses: Связанные гипотезы
        
        Returns:
            Идентификатор знания
        """
        knowledge_id = self._generate_knowledge_id()
        
        knowledge = KnowledgeItem(
            knowledge_id=knowledge_id,
            knowledge_type=knowledge_type,
            title=title,
            description=description,
            data=data or {},
            confidence=confidence,
            sample_size=sample_size,
            source=source,
            related_experiments=related_experiments or [],
            related_hypotheses=related_hypotheses or []
        )
        
        self.knowledge[knowledge_id] = knowledge
        
        # Обновить индексы
        if knowledge_type.value not in self.indexes["by_type"]:
            self.indexes["by_type"][knowledge_type.value] = []
        self.indexes["by_type"][knowledge_type.value].append(knowledge_id)
        
        if source not in self.indexes["by_source"]:
            self.indexes["by_source"][source] = []
        self.indexes["by_source"][source].append(knowledge_id)
        
        logger.info(f"Added knowledge {knowledge_id}: {title}")
        
        return knowledge_id
    
    def add_negative_knowledge(
        self,
        reason: str,
        failed_component: str,
        component_name: str,
        description: str,
        evidence: dict[str, Any] = None
    ) -> str:
        """
        Добавить негативное знание.
        
        Args:
            reason: Причина (INVALIDATED, FAILED, UNSTABLE, NO_EDGE)
            failed_component: Компонент (feature, strategy, model, etc.)
            component_name: Имя компонента
            description: Описание
            evidence: Доказательства
        
        Returns:
            Идентификатор негативного знания
        """
        knowledge_id = self._generate_negative_knowledge_id()
        
        negative_knowledge = NegativeKnowledge(
            knowledge_id=knowledge_id,
            reason=reason,
            failed_component=failed_component,
            component_name=component_name,
            description=description,
            evidence=evidence or {}
        )
        
        self.negative_knowledge[knowledge_id] = negative_knowledge
        
        # Обновить индекс
        component_key = f"{failed_component}:{component_name}"
        if component_key not in self.indexes["by_component"]:
            self.indexes["by_component"][component_key] = []
        self.indexes["by_component"][component_key].append(knowledge_id)
        
        logger.info(f"Added negative knowledge {knowledge_id}: {failed_component} {component_name} is {reason}")
        
        return knowledge_id
    
    def get_knowledge(self, knowledge_id: str) -> KnowledgeItem | None:
        """
        Получить знание.
        
        Args:
            knowledge_id: Идентификатор знания
        
        Returns:
            KnowledgeItem или None
        """
        return self.knowledge.get(knowledge_id)
    
    def get_negative_knowledge(self, knowledge_id: str) -> NegativeKnowledge | None:
        """
        Получить негативное знание.
        
        Args:
            knowledge_id: Идентификатор знания
        
        Returns:
            NegativeKnowledge или None
        """
        return self.negative_knowledge.get(knowledge_id)
    
    def is_component_invalidated(
        self,
        component_type: str,
        component_name: str
    ) -> bool:
        """
        Проверить, invalidирован ли компонент.
        
        Args:
            component_type: Тип компонента
            component_name: Имя компонента
        
        Returns:
            True если компонент invalidирован
        """
        component_key = f"{component_type}:{component_name}"
        
        if component_key not in self.indexes["by_component"]:
            return False
        
        for knowledge_id in self.indexes["by_component"][component_key]:
            nk = self.negative_knowledge.get(knowledge_id)
            if nk and nk.reason in ["INVALIDATED", "FAILED"]:
                return True
        
        return False
    
    def is_component_unstable(
        self,
        component_type: str,
        component_name: str
    ) -> bool:
        """
        Проверить, нестабилен ли компонент.
        
        Args:
            component_type: Тип компонента
            component_name: Имя компонента
        
        Returns:
            True если компонент нестабилен
        """
        component_key = f"{component_type}:{component_name}"
        
        if component_key not in self.indexes["by_component"]:
            return False
        
        for knowledge_id in self.indexes["by_component"][component_key]:
            nk = self.negative_knowledge.get(knowledge_id)
            if nk and nk.reason == "UNSTABLE":
                return True
        
        return False
    
    def has_no_edge(
        self,
        component_type: str,
        component_name: str
    ) -> bool:
        """
        Проверить, нет ли edge у компонента.
        
        Args:
            component_type: Тип компонента
            component_name: Имя компонента
        
        Returns:
            True если у компонента нет edge
        """
        component_key = f"{component_type}:{component_name}"
        
        if component_key not in self.indexes["by_component"]:
            return False
        
        for knowledge_id in self.indexes["by_component"][component_key]:
            nk = self.negative_knowledge.get(knowledge_id)
            if nk and nk.reason == "NO_EDGE":
                return True
        
        return False
    
    def prevent_repetition(
        self,
        component_type: str,
        component_name: str
    ) -> bool:
        """
        Предотвратить повторение бессмысленных экспериментов.
        
        Args:
            component_type: Тип компонента
            component_name: Имя компонента
        
        Returns:
            True если эксперимент не следует повторять
        """
        # Проверить, есть ли негативные знания об этом компоненте
        if self.is_component_invalidated(component_type, component_name):
            return True
        
        if self.is_component_unstable(component_type, component_name):
            return True
        
        if self.has_no_edge(component_type, component_name):
            return True
        
        return False
    
    def search_knowledge(
        self,
        knowledge_type: KnowledgeType | None = None,
        component_type: str | None = None,
        component_name: str | None = None,
        source: str | None = None,
        limit: int = 100
    ) -> list[KnowledgeItem]:
        """
        Поиск знаний.
        
        Args:
            knowledge_type: Тип знания
            component_type: Тип компонента
            component_name: Имя компонента
            source: Источник
            limit: Лимит результатов
        
        Returns:
            Список знаний
        """
        results = []
        
        if knowledge_type:
            # Поиск по типу
            if knowledge_type.value in self.indexes["by_type"]:
                for knowledge_id in self.indexes["by_type"][knowledge_type.value][:limit]:
                    knowledge = self.knowledge.get(knowledge_id)
                    if knowledge:
                        results.append(knowledge)
        
        elif component_type and component_name:
            # Поиск по компоненту
            component_key = f"{component_type}:{component_name}"
            if component_key in self.indexes["by_component"]:
                for knowledge_id in self.indexes["by_component"][component_key][:limit]:
                    knowledge = self.knowledge.get(knowledge_id)
                    if knowledge:
                        results.append(knowledge)
        
        elif source:
            # Поиск по источнику
            if source in self.indexes["by_source"]:
                for knowledge_id in self.indexes["by_source"][source][:limit]:
                    knowledge = self.knowledge.get(knowledge_id)
                    if knowledge:
                        results.append(knowledge)
        
        else:
            # Вернуть все знания
            results = list(self.knowledge.values())[:limit]
        
        return results
    
    def get_statistics(self) -> dict[str, Any]:
        """
        Получить статистику базы знаний.
        
        Returns:
            Статистика
        """
        by_type = {}
        for knowledge_type in KnowledgeType:
            count = len(self.indexes["by_type"].get(knowledge_type.value, []))
            by_type[knowledge_type.value] = count
        
        return {
            "total_knowledge": len(self.knowledge),
            "total_negative_knowledge": len(self.negative_knowledge),
            "by_type": by_type,
            "by_source": {k: len(v) for k, v in self.indexes["by_source"].items()},
            "by_component": {k: len(v) for k, v in self.indexes["by_component"].items()},
        }
    
    def cleanup_old_knowledge(self, max_age_days: int = 365) -> dict[str, int]:
        """
        Очистить старые знания.
        
        Args:
            max_age_days: Максимальный возраст в днях
        
        Returns:
            Количество удалённых записей
        """
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        cleaned = {
            "knowledge": 0,
            "negative_knowledge": 0
        }
        
        # Очистить знания
        knowledge_to_remove = [
            kid for kid, knowledge in self.knowledge.items()
            if knowledge.updated_at < cutoff
        ]
        
        for kid in knowledge_to_remove:
            knowledge = self.knowledge[kid]
            # Удалить из индексов
            if knowledge.knowledge_type.value in self.indexes["by_type"]:
                self.indexes["by_type"][knowledge.knowledge_type.value].remove(kid)
            if knowledge.source in self.indexes["by_source"]:
                self.indexes["by_source"][knowledge.source].remove(kid)
            
            del self.knowledge[kid]
            cleaned["knowledge"] += 1
        
        # Очистить негативные знания
        negative_to_remove = [
            nkid for nkid, nk in self.negative_knowledge.items()
            if nk.discovered_at < cutoff
        ]
        
        for nkid in negative_to_remove:
            nk = self.negative_knowledge[nkid]
            component_key = f"{nk.failed_component}:{nk.component_name}"
            if component_key in self.indexes["by_component"]:
                self.indexes["by_component"][component_key].remove(nkid)
            
            del self.negative_knowledge[nkid]
            cleaned["negative_knowledge"] += 1
        
        return cleaned


# Глобальный экземпляр Knowledge Base
_knowledge_base: KnowledgeBase | None = None


def get_knowledge_base() -> KnowledgeBase:
    """Получить глобальный Knowledge Base"""
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = KnowledgeBase()
    return _knowledge_base


def reset_knowledge_base():
    """Сбросить Knowledge Base (для тестов)"""
    global _knowledge_base
    _knowledge_base = KnowledgeBase()
