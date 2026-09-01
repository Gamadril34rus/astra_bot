"""
ASTRA BOT - Dataset Registry

Реестр dataset с метаданными и историей качества (ТЗ Пункт 2)

Каждый dataset должен иметь:
- dataset_id
- source
- symbol
- timeframe
- start
- end
- download_time
- version
- checksum
- quality_score
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .data_quality_engine import DatasetMetadata, DataQualityResult, get_data_quality_engine

logger = logging.getLogger(__name__)


@dataclass
class DatasetVersion:
    """Версия dataset"""
    version: str
    checksum: str
    download_time: datetime
    quality_score: float
    quality_level: str
    num_candles: int
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "checksum": self.checksum,
            "download_time": self.download_time.isoformat(),
            "quality_score": self.quality_score,
            "quality_level": self.quality_level,
            "num_candles": self.num_candles,
            "metadata": self.metadata,
        }


@dataclass
class DatasetRecord:
    """Запись dataset в реестре"""
    dataset_id: str
    source: str
    symbol: str
    timeframe: str
    current_version: str = "1.0"
    versions: dict[str, DatasetVersion] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    quality_history: list[DataQualityResult] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    is_active: bool = True
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "source": self.source,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "current_version": self.current_version,
            "versions": {k: v.to_dict() for k, v in self.versions.items()},
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_active": self.is_active,
            "quality_history_count": len(self.quality_history),
            "warnings_count": len(self.warnings),
            "errors_count": len(self.errors),
        }
    
    def add_version(self, version: DatasetVersion) -> None:
        """Добавить новую версию"""
        self.versions[version.version] = version
        self.current_version = version.version
        self.updated_at = datetime.now(timezone.utc)
    
    def add_quality_result(self, result: DataQualityResult) -> None:
        """Добавить результат проверки качества"""
        self.quality_history.append(result)
        self.warnings.extend([w.to_dict() for w in result.warnings])
        self.errors.extend([e.to_dict() for e in result.errors])
        self.updated_at = datetime.now(timezone.utc)


class DatasetRegistry:
    """
    Реестр всех dataset.
    
    Хранит метаданные и историю качества для каждого dataset.
    """
    
    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else Path("data/dataset_registry")
        self._datasets: dict[str, DatasetRecord] = {}
        self._load_from_disk()
    
    def _load_from_disk(self) -> None:
        """Загрузить реестр с диска"""
        if not self.storage_path.exists():
            return
        
        try:
            for file_path in self.storage_path.glob("*.json"):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    dataset_id = data.get("dataset_id")
                    if dataset_id:
                        # Восстановить объект
                        record = DatasetRecord(
                            dataset_id=data.get("dataset_id", ""),
                            source=data.get("source", ""),
                            symbol=data.get("symbol", ""),
                            timeframe=data.get("timeframe", ""),
                            current_version=data.get("current_version", "1.0"),
                            created_at=datetime.fromisoformat(data.get("created_at")) if data.get("created_at") else datetime.now(timezone.utc),
                            updated_at=datetime.fromisoformat(data.get("updated_at")) if data.get("updated_at") else datetime.now(timezone.utc),
                            is_active=data.get("is_active", True),
                        )
                        
                        # Восстановить версии
                        for ver, ver_data in data.get("versions", {}).items():
                            version = DatasetVersion(
                                version=ver,
                                checksum=ver_data.get("checksum", ""),
                                download_time=datetime.fromisoformat(ver_data.get("download_time")) if ver_data.get("download_time") else datetime.now(timezone.utc),
                                quality_score=ver_data.get("quality_score", 0.0),
                                quality_level=ver_data.get("quality_level", "poor"),
                                num_candles=ver_data.get("num_candles", 0),
                                metadata=ver_data.get("metadata", {}),
                            )
                            record.versions[ver] = version
                        
                        self._datasets[dataset_id] = record
        except Exception as e:
            logger.error(f"Error loading dataset registry: {e}")
    
    def _save_to_disk(self, record: DatasetRecord) -> None:
        """Сохранить запись на диск"""
        try:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            file_path = self.storage_path / f"{record.dataset_id}.json"
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(record.to_dict(), f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            logger.error(f"Error saving dataset record {record.dataset_id}: {e}")
    
    def register_dataset(
        self,
        candles: list[Any],
        dataset_id: str,
        source: str,
        symbol: str,
        timeframe: str,
        download_time: datetime | None = None,
    ) -> DatasetMetadata:
        """
        Зарегистрировать новый dataset.
        
        Args:
            candles: Список свечей
            dataset_id: Уникальный ID dataset
            source: Источник данных
            symbol: Символ
            timeframe: Таймфрейм
            download_time: Время загрузки
        
        Returns:
            Метаданные dataset
        """
        # Проверить, есть ли уже такой dataset
        if dataset_id in self._datasets:
            record = self._datasets[dataset_id]
        else:
            record = DatasetRecord(
                dataset_id=dataset_id,
                source=source,
                symbol=symbol,
                timeframe=timeframe,
            )
            self._datasets[dataset_id] = record
        
        # Выполнить проверку качества
        quality_engine = get_data_quality_engine()
        metadata = quality_engine.create_dataset_metadata(
            candles, dataset_id, source, symbol, timeframe, download_time
        )
        
        # Создать версию
        version = DatasetVersion(
            version=metadata.version,
            checksum=metadata.checksum,
            download_time=metadata.download_time,
            quality_score=metadata.quality_score,
            quality_level=metadata.quality_level.value,
            num_candles=metadata.num_candles,
            metadata={
                "num_gaps": metadata.num_gaps,
                "num_duplicates": metadata.num_duplicates,
                "anomalies": metadata.anomalies,
            },
        )
        
        # Добавить версию
        record.add_version(version)
        
        # Добавить результат проверки
        quality_result = quality_engine.assess_data_quality(
            candles, dataset_id, symbol, timeframe, source
        )
        record.add_quality_result(quality_result)
        
        # Сохранить на диск
        self._save_to_disk(record)
        
        return metadata
    
    def get_dataset(self, dataset_id: str) -> DatasetRecord | None:
        """Получить запись dataset"""
        return self._datasets.get(dataset_id)
    
    def get_datasets_by_symbol(self, symbol: str) -> list[DatasetRecord]:
        """Получить все dataset по символу"""
        return [r for r in self._datasets.values() if r.symbol == symbol]
    
    def get_datasets_by_source(self, source: str) -> list[DatasetRecord]:
        """Получить все dataset по источнику"""
        return [r for r in self._datasets.values() if r.source == source]
    
    def get_datasets_by_quality(self, min_quality: float = 0.7) -> list[DatasetRecord]:
        """Получить dataset с минимальным уровнем качества"""
        return [
            r for r in self._datasets.values()
            if r.versions.get(r.current_version, DatasetVersion("1.0", "", datetime.now(timezone.utc), 0.0, "bad", 0)).quality_score >= min_quality
        ]
    
    def search_datasets(
        self,
        symbol: str = "",
        timeframe: str = "",
        source: str = "",
        min_quality: float = 0.0,
        is_active: bool = True,
    ) -> list[DatasetRecord]:
        """
        Поиск dataset по критериям.
        
        Args:
            symbol: Символ
            timeframe: Таймфрейм
            source: Источник
            min_quality: Минимальный уровень качества
            is_active: Только активные
        
        Returns:
            Список соответствующих dataset
        """
        results = []
        for record in self._datasets.values():
            if is_active and not record.is_active:
                continue
            if symbol and record.symbol != symbol:
                continue
            if timeframe and record.timeframe != timeframe:
                continue
            if source and record.source != source:
                continue
            
            current_version = record.versions.get(record.current_version)
            if current_version and current_version.quality_score >= min_quality:
                results.append(record)
        
        return results
    
    def deactivate_dataset(self, dataset_id: str) -> bool:
        """Деактивировать dataset"""
        if dataset_id in self._datasets:
            self._datasets[dataset_id].is_active = False
            self._save_to_disk(self._datasets[dataset_id])
            return True
        return False
    
    def get_statistics(self) -> dict[str, Any]:
        """Получить статистику по dataset"""
        total = len(self._datasets)
        active = sum(1 for r in self._datasets.values() if r.is_active)
        
        # Статистика по качеству
        quality_distribution = {}
        for record in self._datasets.values():
            current_version = record.versions.get(record.current_version)
            if current_version:
                level = current_version.quality_level
                quality_distribution[level] = quality_distribution.get(level, 0) + 1
        
        # Статистика по символам
        symbol_distribution = {}
        for record in self._datasets.values():
            symbol_distribution[record.symbol] = symbol_distribution.get(record.symbol, 0) + 1
        
        # Статистика по источникам
        source_distribution = {}
        for record in self._datasets.values():
            source_distribution[record.source] = source_distribution.get(record.source, 0) + 1
        
        return {
            "total_datasets": total,
            "active_datasets": active,
            "inactive_datasets": total - active,
            "quality_distribution": quality_distribution,
            "symbol_distribution": symbol_distribution,
            "source_distribution": source_distribution,
        }
    
    def cleanup_old_versions(self, max_versions: int = 5) -> int:
        """
        Очистить старые версии dataset.
        
        Args:
            max_versions: Максимальное количество версий для хранения
        
        Returns:
            Количество удалённых версий
        """
        removed_count = 0
        for record in self._datasets.values():
            if len(record.versions) > max_versions:
                # Удалить старые версии (кроме текущей)
                versions_to_remove = sorted(
                    [v for v in record.versions.keys() if v != record.current_version],
                    key=lambda x: record.versions[x].download_time
                )[:len(record.versions) - max_versions]
                
                for version in versions_to_remove:
                    del record.versions[version]
                    removed_count += 1
                
                self._save_to_disk(record)
        
        return removed_count


# Глобальный экземпляр
_dataset_registry: DatasetRegistry | None = None


def get_dataset_registry() -> DatasetRegistry:
    """Получить глобальный Dataset Registry"""
    global _dataset_registry
    if _dataset_registry is None:
        _dataset_registry = DatasetRegistry()
    return _dataset_registry


def reset_dataset_registry():
    """Сбросить Dataset Registry (для тестов)"""
    global _dataset_registry
    _dataset_registry = DatasetRegistry()
