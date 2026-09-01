"""
ASTRA BOT - Research Module

Модуль исследований (ТЗ Пункты 4, 5, 9, 29, 49-51, 94-95)

Содержит:
- EventResponseEngine: Анализ причинно-временных связей
- CausalityResearchEngine: Причинно-следственный анализ
- AcademicResearchEngine: Исследования из академических источников
"""

from .event_response_engine import EventResponseEngine, get_event_response_engine, reset_event_response_engine
from .causality_research_engine import CausalityResearchEngine, get_causality_research_engine, reset_causality_research_engine
from .academic_research_engine import AcademicResearchEngine, get_academic_research_engine, reset_academic_research_engine

__all__ = [
    "EventResponseEngine",
    "get_event_response_engine",
    "reset_event_response_engine",
    "CausalityResearchEngine",
    "get_causality_research_engine",
    "reset_causality_research_engine",
    "AcademicResearchEngine",
    "get_academic_research_engine",
    "reset_academic_research_engine",
]
