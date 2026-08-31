"""
ASTRA BOT — Memory Layer

Содержит компоненты для хранения и управления памятью:
- memory_manager: Управление памятью
- lesson_quality_engine: Оценка качества уроков
- knowledge_base: База знаний
"""

# Импортируем компоненты по мере их реализации
try:
    from .memory_manager import MemoryManager, get_memory_manager
except ImportError:
    MemoryManager = None
    get_memory_manager = None

try:
    from .lesson_quality_engine import LessonQualityEngine, get_lesson_quality_engine
except ImportError:
    LessonQualityEngine = None
    get_lesson_quality_engine = None

try:
    from .knowledge_base import KnowledgeBase, get_knowledge_base
except ImportError:
    KnowledgeBase = None
    get_knowledge_base = None

__all__ = [
    "MemoryManager",
    "get_memory_manager",
    "LessonQualityEngine",
    "get_lesson_quality_engine",
    "KnowledgeBase",
    "get_knowledge_base",
]
