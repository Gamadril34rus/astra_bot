"""
ASTRA BOT - Memory System

Система памяти (ТЗ Пункты 97-100)

Хранит:
- all raw data
- all processed data
- all decisions
- all actions
- all reasoning
- all results

Обеспечивает:
- versioning
- compression
- encryption
- backup
- recovery

"""

import logging
import json
import pickle
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional
from pathlib import Path
import hashlib
import uuid

import numpy as np

logger = logging.getLogger(__name__)


class DataType(str, Enum):
    """Типы данных"""
    RAW = "raw"  # Сырые данные
    PROCESSED = "processed"  # Обработанные данные
    DECISION = "decision"  # Решения
    ACTION = "action"  # Действия
    REASONING = "reasoning"  # Рассуждения
    RESULT = "result"  # Результаты
    CONFIGURATION = "configuration"  # Конфигурация
    MODEL = "model"  # Модели


class CompressionMethod(str, Enum):
    """Методы сжатия"""
    NONE = "none"
    ZLIB = "zlib"
    GZIP = "gzip"
    BZ2 = "bz2"
    LZMA = "lzma"


class StorageType(str, Enum):
    """Типы хранилища"""
    MEMORY = "memory"  # В памяти
    DISK = "disk"  # На диске
    DATABASE = "database"  # В базе данных
    CLOUD = "cloud"  # В облаке


@dataclass
class DataEntry:
    """Запись данных"""
    data_id: str
    data_type: DataType
    symbol: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Данные
    data: Any = None
    
    # Метаданные
    metadata: dict[str, Any] = field(default_factory=dict)
    
    # Версия
    version: str = "1.0"
    
    # Сжатие
    compression: CompressionMethod = CompressionMethod.NONE
    
    # Хэш
    hash_value: str = ""
    
    # Размер
    size_bytes: int = 0
    
    def __post_init__(self):
        if self.data_id == "":
            self.data_id = str(uuid.uuid4())
        
        if self.hash_value == "":
            self._calculate_hash()
        
        if self.size_bytes == 0 and self.data is not None:
            self._calculate_size()
    
    def _calculate_hash(self):
        """Рассчитать хэш"""
        if self.data is None:
            self.hash_value = ""
            return
        
        try:
            # Сериализовать данные
            if isinstance(self.data, (dict, list, str, int, float, bool)):
                data_str = json.dumps(self.data, sort_keys=True, default=str)
            else:
                data_str = str(self.data)
            
            self.hash_value = hashlib.sha256(data_str.encode()).hexdigest()
        except Exception as e:
            logger.error(f"Error calculating hash: {e}")
            self.hash_value = ""
    
    def _calculate_size(self):
        """Рассчитать размер"""
        try:
            if isinstance(self.data, (dict, list, str)):
                self.size_bytes = len(json.dumps(self.data, default=str).encode())
            elif isinstance(self.data, bytes):
                self.size_bytes = len(self.data)
            elif self.data is not None:
                self.size_bytes = len(str(self.data).encode())
            else:
                self.size_bytes = 0
        except Exception as e:
            logger.error(f"Error calculating size: {e}")
            self.size_bytes = 0
    
    def compress(self, method: CompressionMethod = CompressionMethod.ZLIB) -> bytes:
        """
        Сжать данные.
        
        Args:
            method: Метод сжатия
        
        Returns:
            Сжатые данные
        """
        if self.data is None:
            return b""
        
        try:
            if isinstance(self.data, (dict, list)):
                data_bytes = json.dumps(self.data, default=str).encode()
            elif isinstance(self.data, str):
                data_bytes = self.data.encode()
            elif isinstance(self.data, bytes):
                data_bytes = self.data
            else:
                data_bytes = pickle.dumps(self.data)
            
            if method == CompressionMethod.ZLIB:
                return zlib.compress(data_bytes)
            elif method == CompressionMethod.GZIP:
                import gzip
                return gzip.compress(data_bytes)
            elif method == CompressionMethod.BZ2:
                import bz2
                return bz2.compress(data_bytes)
            elif method == CompressionMethod.LZMA:
                import lzma
                return lzma.compress(data_bytes)
            else:
                return data_bytes
        except Exception as e:
            logger.error(f"Error compressing data: {e}")
            return b""
    
    def decompress(self, compressed_data: bytes) -> Any:
        """
        Распаковать данные.
        
        Args:
            compressed_data: Сжатые данные
        
        Returns:
            Распакованные данные
        """
        try:
            # Попробовать разные методы
            try:
                return zlib.decompress(compressed_data)
            except:
                pass
            
            try:
                import gzip
                return gzip.decompress(compressed_data)
            except:
                pass
            
            try:
                import bz2
                return bz2.decompress(compressed_data)
            except:
                pass
            
            try:
                import lzma
                return lzma.decompress(compressed_data)
            except:
                pass
            
            return compressed_data
        except Exception as e:
            logger.error(f"Error decompressing data: {e}")
            return None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "data_id": self.data_id,
            "data_type": self.data_type.value,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "version": self.version,
            "compression": self.compression.value,
            "hash_value": self.hash_value,
            "size_bytes": self.size_bytes,
        }


@dataclass
class VersionInfo:
    """Информация о версии"""
    version: str
    timestamp: datetime
    changes: list[str] = field(default_factory=list)
    author: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "timestamp": self.timestamp.isoformat(),
            "changes": self.changes,
            "author": self.author,
        }


@dataclass
class BackupInfo:
    """Информация о резервной копии"""
    backup_id: str
    timestamp: datetime
    storage_type: StorageType
    location: str
    size_bytes: int = 0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "timestamp": self.timestamp.isoformat(),
            "storage_type": self.storage_type.value,
            "location": self.location,
            "size_bytes": self.size_bytes,
        }


class MemorySystem:
    """
    Система памяти.
    
    Хранит и управляет всеми данными системы.
    """
    
    def __init__(self, storage_path: str = "./memory"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Данные в памяти
        self._memory_data: dict[str, DataEntry] = {}
        
        # Индексы
        self._indexes: dict[str, dict[str, list[str]]] = {
            "by_type": {},
            "by_symbol": {},
            "by_timestamp": {},
        }
        
        # Версии
        self._versions: dict[str, list[VersionInfo]] = {}
        
        # Резервные копии
        self._backups: list[BackupInfo] = []
        
        # Пороги
        self.thresholds = {
            "max_memory_size_mb": 1024,  # 1GB
            "max_disk_size_mb": 10240,  # 10GB
            "auto_backup_interval_hours": 24,
            "auto_cleanup_days": 30,
        }
        
        # Создать директории
        self._create_directories()
    
    def _create_directories(self):
        """Создать необходимые директории"""
        (self.storage_path / "raw").mkdir(parents=True, exist_ok=True)
        (self.storage_path / "processed").mkdir(parents=True, exist_ok=True)
        (self.storage_path / "decisions").mkdir(parents=True, exist_ok=True)
        (self.storage_path / "actions").mkdir(parents=True, exist_ok=True)
        (self.storage_path / "reasoning").mkdir(parents=True, exist_ok=True)
        (self.storage_path / "results").mkdir(parents=True, exist_ok=True)
        (self.storage_path / "backups").mkdir(parents=True, exist_ok=True)
    
    def store(self, data: Any, data_type: DataType, symbol: str = "", metadata: dict[str, Any] | None = None) -> str:
        """
        Сохранить данные.
        
        Args:
            data: Данные
            data_type: Тип данных
            symbol: Символ
            metadata: Метаданные
        
        Returns:
            ID данных
        """
        entry = DataEntry(
            data_id=str(uuid.uuid4()),
            data_type=data_type,
            symbol=symbol,
            data=data,
            metadata=metadata or {},
        )
        
        # Сохранить в памяти
        self._memory_data[entry.data_id] = entry
        
        # Обновить индексы
        self._update_indexes(entry)
        
        # Автоматическая очистка
        self._auto_cleanup()
        
        return entry.data_id
    
    def store_to_disk(self, data: Any, data_type: DataType, symbol: str = "", metadata: dict[str, Any] | None = None) -> str:
        """
        Сохранить данные на диск.
        
        Args:
            data: Данные
            data_type: Тип данных
            symbol: Символ
            metadata: Метаданные
        
        Returns:
            ID данных
        """
        entry = DataEntry(
            data_id=str(uuid.uuid4()),
            data_type=data_type,
            symbol=symbol,
            data=data,
            metadata=metadata or {},
            compression=CompressionMethod.ZLIB,
        )
        
        # Сжать данные
        compressed_data = entry.compress()
        
        # Сохранить на диск
        file_path = self.storage_path / data_type.value / f"{entry.data_id}.bin"
        
        try:
            with open(file_path, "wb") as f:
                f.write(compressed_data)
            
            # Обновить индексы
            self._update_indexes(entry)
            
            return entry.data_id
        except Exception as e:
            logger.error(f"Error storing to disk: {e}")
            return ""
    
    def retrieve(self, data_id: str) -> DataEntry | None:
        """
        Получить данные.
        
        Args:
            data_id: ID данных
        
        Returns:
            Запись данных или None
        """
        # Проверить память
        if data_id in self._memory_data:
            return self._memory_data[data_id]
        
        # Проверить диск
        for data_type in DataType:
            file_path = self.storage_path / data_type.value / f"{data_id}.bin"
            if file_path.exists():
                try:
                    with open(file_path, "rb") as f:
                        compressed_data = f.read()
                    
                    # Создать временную запись
                    entry = DataEntry(data_id=data_id, data_type=data_type)
                    data = entry.decompress(compressed_data)
                    
                    if data is not None:
                        entry.data = data
                        return entry
                except Exception as e:
                    logger.error(f"Error retrieving from disk: {e}")
        
        return None
    
    def _update_indexes(self, entry: DataEntry):
        """Обновить индексы"""
        # По типу
        if entry.data_type.value not in self._indexes["by_type"]:
            self._indexes["by_type"][entry.data_type.value] = []
        if entry.data_id not in self._indexes["by_type"][entry.data_type.value]:
            self._indexes["by_type"][entry.data_type.value].append(entry.data_id)
        
        # По символу
        if entry.symbol:
            if entry.symbol not in self._indexes["by_symbol"]:
                self._indexes["by_symbol"][entry.symbol] = []
            if entry.data_id not in self._indexes["by_symbol"][entry.symbol]:
                self._indexes["by_symbol"][entry.symbol].append(entry.data_id)
        
        # По временной метке
        date_str = entry.timestamp.strftime("%Y-%m-%d")
        if date_str not in self._indexes["by_timestamp"]:
            self._indexes["by_timestamp"][date_str] = []
        if entry.data_id not in self._indexes["by_timestamp"][date_str]:
            self._indexes["by_timestamp"][date_str].append(entry.data_id)
    
    def query(
        self,
        data_type: DataType | None = None,
        symbol: str = "",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[DataEntry]:
        """
        Запрос данных.
        
        Args:
            data_type: Тип данных
            symbol: Символ
            start_time: Начальное время
            end_time: Конечное время
            limit: Лимит результатов
        
        Returns:
            Список записей данных
        """
        candidate_ids = set()
        
        # Фильтрация по типу
        if data_type:
            if data_type.value in self._indexes["by_type"]:
                candidate_ids.update(self._indexes["by_type"][data_type.value])
        else:
            for ids in self._indexes["by_type"].values():
                candidate_ids.update(ids)
        
        # Фильтрация по символу
        if symbol:
            if symbol in self._indexes["by_symbol"]:
                candidate_ids.intersection_update(self._indexes["by_symbol"][symbol])
        
        # Фильтрация по времени
        if start_time or end_time:
            filtered_ids = []
            for data_id in candidate_ids:
                entry = self._memory_data.get(data_id)
                if entry:
                    if start_time and entry.timestamp < start_time:
                        continue
                    if end_time and entry.timestamp > end_time:
                        continue
                    filtered_ids.append(data_id)
            candidate_ids = set(filtered_ids)
        
        # Получить записи
        results = []
        for data_id in list(candidate_ids)[:limit]:
            entry = self.retrieve(data_id)
            if entry:
                results.append(entry)
        
        # Сортировать по времени
        results.sort(key=lambda x: x.timestamp, reverse=True)
        
        return results
    
    def create_version(self, data_id: str, changes: list[str], author: str = "") -> VersionInfo:
        """
        Создать новую версию данных.
        
        Args:
            data_id: ID данных
            changes: Изменения
            author: Автор
        
        Returns:
            Информация о версии
        """
        entry = self._memory_data.get(data_id)
        if not entry:
            raise ValueError(f"Data with ID {data_id} not found")
        
        # Увеличить версию
        version_parts = entry.version.split(".")
        if len(version_parts) == 2:
            major = int(version_parts[0])
            minor = int(version_parts[1]) + 1
            new_version = f"{major}.{minor}"
        else:
            new_version = "1.1"
        
        version_info = VersionInfo(
            version=new_version,
            timestamp=datetime.now(timezone.utc),
            changes=changes,
            author=author,
        )
        
        # Сохранить версию
        if data_id not in self._versions:
            self._versions[data_id] = []
        self._versions[data_id].append(version_info)
        
        # Обновить запись
        entry.version = new_version
        
        return version_info
    
    def get_versions(self, data_id: str) -> list[VersionInfo]:
        """
        Получить версии данных.
        
        Args:
            data_id: ID данных
        
        Returns:
            Список версий
        """
        return self._versions.get(data_id, [])
    
    def backup(self, storage_type: StorageType = StorageType.DISK, location: str = "") -> BackupInfo:
        """
        Создать резервную копию.
        
        Args:
            storage_type: Тип хранилища
            location: Местоположение
        
        Returns:
            Информация о резервной копии
        """
        backup_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc)
        
        # Создать архив
        if storage_type == StorageType.DISK:
            backup_path = self.storage_path / "backups" / f"{backup_id}.zip"
            
            try:
                import zipfile
                with zipfile.ZipFile(backup_path, 'w') as zipf:
                    for data_type in DataType:
                        type_dir = self.storage_path / data_type.value
                        if type_dir.exists():
                            for file in type_dir.glob("*.bin"):
                                zipf.write(file, file.relative_to(self.storage_path))
                
                size_bytes = backup_path.stat().st_size
                
                backup_info = BackupInfo(
                    backup_id=backup_id,
                    timestamp=timestamp,
                    storage_type=storage_type,
                    location=str(backup_path),
                    size_bytes=size_bytes,
                )
                
                self._backups.append(backup_info)
                
                return backup_info
            except Exception as e:
                logger.error(f"Error creating backup: {e}")
                return BackupInfo(
                    backup_id=backup_id,
                    timestamp=timestamp,
                    storage_type=storage_type,
                    location="",
                )
        
        return BackupInfo(
            backup_id=backup_id,
            timestamp=timestamp,
            storage_type=storage_type,
            location=location,
        )
    
    def restore(self, backup_id: str) -> bool:
        """
        Восстановить из резервной копии.
        
        Args:
            backup_id: ID резервной копии
        
        Returns:
            Успешность восстановления
        """
        backup = next((b for b in self._backups if b.backup_id == backup_id), None)
        if not backup:
            return False
        
        if backup.storage_type == StorageType.DISK:
            backup_path = Path(backup.location)
            if not backup_path.exists():
                return False
            
            try:
                import zipfile
                with zipfile.ZipFile(backup_path, 'r') as zipf:
                    zipf.extractall(self.storage_path)
                
                # Перезагрузить индексы
                self._rebuild_indexes()
                
                return True
            except Exception as e:
                logger.error(f"Error restoring backup: {e}")
                return False
        
        return False
    
    def _rebuild_indexes(self):
        """Перестроить индексы"""
        self._indexes = {
            "by_type": {},
            "by_symbol": {},
            "by_timestamp": {},
        }
        
        # Переиндексировать данные в памяти
        for entry in self._memory_data.values():
            self._update_indexes(entry)
    
    def _auto_cleanup(self):
        """Автоматическая очистка"""
        # Удалить старые данные
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.thresholds["auto_cleanup_days"])
        
        old_data_ids = []
        for data_id, entry in self._memory_data.items():
            if entry.timestamp < cutoff_date:
                old_data_ids.append(data_id)
        
        for data_id in old_data_ids:
            del self._memory_data[data_id]
            self._remove_from_indexes(data_id)
        
        # Очистить индексы по времени
        old_dates = []
        for date_str in self._indexes["by_timestamp"]:
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                if date < cutoff_date:
                    old_dates.append(date_str)
            except:
                pass
        
        for date_str in old_dates:
            del self._indexes["by_timestamp"][date_str]
    
    def _remove_from_indexes(self, data_id: str):
        """Удалить из индексов"""
        entry = self._memory_data.get(data_id)
        if not entry:
            return
        
        # По типу
        if entry.data_type.value in self._indexes["by_type"]:
            if data_id in self._indexes["by_type"][entry.data_type.value]:
                self._indexes["by_type"][entry.data_type.value].remove(data_id)
        
        # По символу
        if entry.symbol and entry.symbol in self._indexes["by_symbol"]:
            if data_id in self._indexes["by_symbol"][entry.symbol]:
                self._indexes["by_symbol"][entry.symbol].remove(data_id)
        
        # По временной метке
        date_str = entry.timestamp.strftime("%Y-%m-%d")
        if date_str in self._indexes["by_timestamp"]:
            if data_id in self._indexes["by_timestamp"][date_str]:
                self._indexes["by_timestamp"][date_str].remove(data_id)
    
    def get_statistics(self) -> dict[str, Any]:
        """
        Получить статистику системы.
        
        Returns:
            Статистика
        """
        total_entries = len(self._memory_data)
        total_backups = len(self._backups)
        
        # Размер данных
        total_size = sum(e.size_bytes for e in self._memory_data.values())
        
        # По типам
        by_type = {}
        for data_type in DataType:
            count = len(self._indexes["by_type"].get(data_type.value, []))
            if count > 0:
                by_type[data_type.value] = count
        
        # По символам
        by_symbol = {}
        for symbol, ids in self._indexes["by_symbol"].items():
            by_symbol[symbol] = len(ids)
        
        return {
            "total_entries": total_entries,
            "total_size_bytes": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "total_backups": total_backups,
            "by_type": by_type,
            "by_symbol": by_symbol,
        }


# Глобальный экземпляр
_memory_system: MemorySystem | None = None


def get_memory_system() -> MemorySystem:
    """Получить глобальную Memory System"""
    global _memory_system
    if _memory_system is None:
        _memory_system = MemorySystem()
    return _memory_system


def reset_memory_system():
    """Сбросить Memory System (для тестов)"""
    global _memory_system
    _memory_system = MemorySystem()
