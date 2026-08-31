"""
ASTRA BOT — Research Layer

Содержит компоненты для исследования и обнаружения:
- experiment_registry: Регистрация экспериментов
- statistical_tests: Статистические тесты
- hypothesis_generator: Генерация гипотез
- research_agent: Автономный исследовательский агент
"""

# Импортируем компоненты по мере их реализации
try:
    from .experiment_registry import ExperimentRegistry, get_experiment_registry
except ImportError:
    ExperimentRegistry = None
    get_experiment_registry = None

try:
    from .statistical_tests import StatisticalTests, get_statistical_tests
except ImportError:
    StatisticalTests = None
    get_statistical_tests = None

try:
    from .hypothesis_generator import HypothesisGenerator, get_hypothesis_generator
except ImportError:
    HypothesisGenerator = None
    get_hypothesis_generator = None

try:
    from .research_agent import ResearchAgent, get_research_agent
except ImportError:
    ResearchAgent = None
    get_research_agent = None

__all__ = [
    "ExperimentRegistry",
    "get_experiment_registry",
    "StatisticalTests",
    "get_statistical_tests",
    "HypothesisGenerator",
    "get_hypothesis_generator",
    "ResearchAgent",
    "get_research_agent",
]
