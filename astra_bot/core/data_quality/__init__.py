"""
ASTRA BOT - Data Quality Module

Модуль контроля качества данных (ТЗ Пункт 2)

Отвечает за:
- Проверку timestamp и timezone
- Проверку последовательности свечей
- Обнаружение дубликатов
- Обнаружение пропусков
- Проверку аномальных OHLC значений
- Проверку качества данных
- Создание dataset registry
- Генерацию предупреждений и ошибок
"""

from .data_quality_engine import (
    DataQualityEngine,
    get_data_quality_engine,
    DataQualityCheck,
    DataQualityResult,
    DataQualityWarning,
    DataQualityError,
    QualityCheckType,
    QualityLevel,
    DatasetMetadata,
)
from .dataset_registry import DatasetRegistry, get_dataset_registry

__all__ = [
    "DataQualityEngine",
    "get_data_quality_engine",
    "DatasetRegistry",
    "get_dataset_registry",
    "DataQualityCheck",
    "DataQualityResult",
    "DataQualityWarning",
    "DataQualityError",
    "QualityCheckType",
    "QualityLevel",
    "DatasetMetadata",
]
