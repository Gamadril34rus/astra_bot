"""
ASTRA BOT - Data Quality Engine

Движок контроля качества данных (ТЗ Пункт 2)

Перед использованием любых данных проверять:
- timestamp
- timezone
- последовательность свечей
- дубликаты
- пропуски
- abnormal OHLC (high < low, close вне диапазона)
- volume anomalies
- zero volume
- резкие аномальные изменения
- stale candles
- рассинхронизацию разных timeframes
- несоответствие timestamp разных источников

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

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

from ...core import models

logger = logging.getLogger(__name__)


class QualityCheckType(str, Enum):
    """Типы проверок качества данных"""
    TIMESTAMP = "timestamp"
    TIMEZONE = "timezone"
    SEQUENCE = "sequence"
    DUPLICATES = "duplicates"
    GAPS = "gaps"
    OHLC_VALUES = "ohlc_values"
    VOLUME = "volume"
    ANOMALIES = "anomalies"
    STALENESS = "staleness"
    SYNCHRONIZATION = "synchronization"
    SOURCE_CONSISTENCY = "source_consistency"


class QualityLevel(str, Enum):
    """Уровни качества данных"""
    EXCELLENT = "excellent"  # 0.9-1.0
    GOOD = "good"  # 0.7-0.89
    FAIR = "fair"  # 0.5-0.69
    POOR = "poor"  # 0.3-0.49
    BAD = "bad"  # 0.0-0.29
    CRITICAL = "critical"  # Непригоден для использования


@dataclass
class DataQualityWarning:
    """Предупреждение о качестве данных"""
    check_type: QualityCheckType
    message: str
    severity: str = "warning"
    dataset_id: str = ""
    symbol: str = ""
    timeframe: str = ""
    timestamp: datetime | None = None
    details: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "check_type": self.check_type.value,
            "severity": self.severity,
            "message": self.message,
            "dataset_id": self.dataset_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "details": self.details,
        }


@dataclass
class DataQualityError:
    """Критическая ошибка качества данных"""
    check_type: QualityCheckType
    message: str
    severity: str = "error"
    dataset_id: str = ""
    symbol: str = ""
    timeframe: str = ""
    timestamp: datetime | None = None
    details: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "check_type": self.check_type.value,
            "severity": self.severity,
            "message": self.message,
            "dataset_id": self.dataset_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "details": self.details,
        }


@dataclass
class DataQualityCheck:
    """Результат проверки качества"""
    check_type: QualityCheckType
    passed: bool
    score: float  # 0-1
    warnings: list[DataQualityWarning] = field(default_factory=list)
    errors: list[DataQualityError] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "check_type": self.check_type.value,
            "passed": self.passed,
            "score": self.score,
            "warnings": [w.to_dict() for w in self.warnings],
            "errors": [e.to_dict() for e in self.errors],
            "details": self.details,
        }


@dataclass
class DataQualityResult:
    """Полный результат проверки качества данных"""
    dataset_id: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    total_checks: int = 0
    passed_checks: int = 0
    warnings_count: int = 0
    errors_count: int = 0
    quality_score: float = 0.0
    quality_level: QualityLevel = QualityLevel.POOR
    checks: dict[QualityCheckType, DataQualityCheck] = field(default_factory=dict)
    warnings: list[DataQualityWarning] = field(default_factory=list)
    errors: list[DataQualityError] = field(default_factory=list)
    checksum: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "warnings_count": self.warnings_count,
            "errors_count": self.errors_count,
            "quality_score": self.quality_score,
            "quality_level": self.quality_level.value,
            "checks": {k.value: v.to_dict() for k, v in self.checks.items()},
            "warnings": [w.to_dict() for w in self.warnings],
            "errors": [e.to_dict() for e in self.errors],
            "checksum": self.checksum,
            "timestamp": self.timestamp.isoformat(),
        }
    
    @property
    def is_usable(self) -> bool:
        """Можно ли использовать данные"""
        return self.quality_level not in [QualityLevel.BAD, QualityLevel.CRITICAL]
    
    @property
    def has_errors(self) -> bool:
        """Есть ли крические ошибки"""
        return self.errors_count > 0
    
    @property
    def has_warnings(self) -> bool:
        """Есть ли предупреждения"""
        return self.warnings_count > 0


@dataclass
class DatasetMetadata:
    """Метаданные dataset (ТЗ Пункт 2)"""
    dataset_id: str
    source: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    download_time: datetime
    version: str = "1.0"
    checksum: str = ""
    quality_score: float = 0.0
    quality_level: QualityLevel = QualityLevel.POOR
    num_candles: int = 0
    num_gaps: int = 0
    num_duplicates: int = 0
    anomalies: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "source": self.source,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "download_time": self.download_time.isoformat(),
            "version": self.version,
            "checksum": self.checksum,
            "quality_score": self.quality_score,
            "quality_level": self.quality_level.value,
            "num_candles": self.num_candles,
            "num_gaps": self.num_gaps,
            "num_duplicates": self.num_duplicates,
            "anomalies": self.anomalies,
        }


class DataQualityEngine:
    """
    Движок контроля качества данных.
    
    Проверяет все аспекты качества данных перед их использованием.
    """
    
    def __init__(self):
        # Веса проверок
        self.check_weights = {
            QualityCheckType.TIMESTAMP: 0.10,
            QualityCheckType.TIMEZONE: 0.10,
            QualityCheckType.SEQUENCE: 0.15,
            QualityCheckType.DUPLICATES: 0.10,
            QualityCheckType.GAPS: 0.10,
            QualityCheckType.OHLC_VALUES: 0.15,
            QualityCheckType.VOLUME: 0.10,
            QualityCheckType.ANOMALIES: 0.10,
            QualityCheckType.STALENESS: 0.05,
            QualityCheckType.SYNCHRONIZATION: 0.05,
        }
        
        # Пороги качества
        self.quality_thresholds = {
            QualityLevel.EXCELLENT: 0.90,
            QualityLevel.GOOD: 0.70,
            QualityLevel.FAIR: 0.50,
            QualityLevel.POOR: 0.30,
            QualityLevel.BAD: 0.10,
        }
    
    def check_timestamp_sequence(
        self,
        candles: list[models.Candle],
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = ""
    ) -> DataQualityCheck:
        """
        Проверить последовательность timestamp.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Результат проверки
        """
        if not candles:
            return DataQualityCheck(
                check_type=QualityCheckType.TIMESTAMP,
                passed=False,
                score=0.0,
                warnings=[],
                errors=[DataQualityError(
                    check_type=QualityCheckType.TIMESTAMP,
                    message="Empty candle list",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                )],
            )
        
        warnings = []
        errors = []
        
        # Проверка, что timestamp есть у всех свечей
        for i, candle in enumerate(candles):
            if not candle.timestamp:
                errors.append(DataQualityError(
                    check_type=QualityCheckType.TIMESTAMP,
                    message=f"Missing timestamp at index {i}",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=candle.timestamp if hasattr(candle, 'timestamp') else None,
                    details={"index": i},
                ))
        
        if errors:
            return DataQualityCheck(
                check_type=QualityCheckType.TIMESTAMP,
                passed=False,
                score=0.0,
                warnings=warnings,
                errors=errors,
            )
        
        # Проверка последовательности
        timestamps = [c.timestamp for c in candles]
        for i in range(1, len(timestamps)):
            if timestamps[i] <= timestamps[i-1]:
                errors.append(DataQualityError(
                    check_type=QualityCheckType.TIMESTAMP,
                    message=f"Timestamp not increasing: {timestamps[i-1]} -> {timestamps[i]}",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamps[i],
                    details={"index": i, "prev": timestamps[i-1], "curr": timestamps[i]},
                ))
        
        if errors:
            return DataQualityCheck(
                check_type=QualityCheckType.TIMESTAMP,
                passed=False,
                score=0.0,
                warnings=warnings,
                errors=errors,
            )
        
        # Проверка равномерности интервалов
        if len(timestamps) > 1:
            intervals = [(timestamps[i] - timestamps[i-1]).total_seconds() 
                        for i in range(1, len(timestamps))]
            expected_interval = self._get_expected_interval_seconds(timeframe)
            
            if expected_interval:
                for i, interval in enumerate(intervals):
                    if abs(interval - expected_interval) > expected_interval * 0.1:  # 10% отклонение
                        warnings.append(DataQualityWarning(
                            check_type=QualityCheckType.TIMESTAMP,
                            message=f"Irregular interval: {interval}s (expected ~{expected_interval}s)",
                            dataset_id=dataset_id,
                            symbol=symbol,
                            timeframe=timeframe,
                            timestamp=timestamps[i],
                            details={"index": i, "interval": interval, "expected": expected_interval},
                        ))
        
        score = 1.0 if not errors else 0.0
        if warnings and not errors:
            score = 0.8
        
        return DataQualityCheck(
            check_type=QualityCheckType.TIMESTAMP,
            passed=not errors,
            score=score,
            warnings=warnings,
            errors=errors,
        )
    
    def check_timezone_consistency(
        self,
        candles: list[models.Candle],
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = ""
    ) -> DataQualityCheck:
        """
        Проверить согласованность timezone.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Результат проверки
        """
        if not candles:
            return DataQualityCheck(
                check_type=QualityCheckType.TIMEZONE,
                passed=True,
                score=1.0,
            )
        
        warnings = []
        errors = []
        
        # Проверка timezone
        timezones = set()
        for candle in candles:
            if hasattr(candle, 'timestamp') and candle.timestamp:
                tz = candle.timestamp.tzinfo
                if tz:
                    timezones.add(str(tz))
        
        if len(timezones) > 1:
            errors.append(DataQualityError(
                check_type=QualityCheckType.TIMEZONE,
                message=f"Inconsistent timezones: {timezones}",
                dataset_id=dataset_id,
                symbol=symbol,
                timeframe=timeframe,
            ))
            return DataQualityCheck(
                check_type=QualityCheckType.TIMEZONE,
                passed=False,
                score=0.0,
                errors=errors,
            )
        
        # Проверка, что timezone указан
        if not timezones:
            warnings.append(DataQualityWarning(
                check_type=QualityCheckType.TIMEZONE,
                message="No timezone information in timestamps",
                dataset_id=dataset_id,
                symbol=symbol,
                timeframe=timeframe,
            ))
            return DataQualityCheck(
                check_type=QualityCheckType.TIMEZONE,
                passed=True,
                score=0.8,
                warnings=warnings,
            )
        
        return DataQualityCheck(
            check_type=QualityCheckType.TIMEZONE,
            passed=True,
            score=1.0,
        )
    
    def check_ohlc_values(
        self,
        candles: list[models.Candle],
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = ""
    ) -> DataQualityCheck:
        """
        Проверить OHLC значения на аномалии.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Результат проверки
        """
        if not candles:
            return DataQualityCheck(
                check_type=QualityCheckType.OHLC_VALUES,
                passed=True,
                score=1.0,
            )
        
        warnings = []
        errors = []
        
        for i, candle in enumerate(candles):
            # Проверка high >= low
            if candle.high < candle.low:
                errors.append(DataQualityError(
                    check_type=QualityCheckType.OHLC_VALUES,
                    message=f"High < Low at index {i}",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=candle.timestamp if hasattr(candle, 'timestamp') else None,
                    details={
                        "index": i,
                        "high": float(candle.high),
                        "low": float(candle.low),
                    },
                ))
            
            # Проверка close в диапазоне [low, high]
            if candle.close < candle.low or candle.close > candle.high:
                errors.append(DataQualityError(
                    check_type=QualityCheckType.OHLC_VALUES,
                    message=f"Close outside [Low, High] range at index {i}",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=candle.timestamp if hasattr(candle, 'timestamp') else None,
                    details={
                        "index": i,
                        "close": float(candle.close),
                        "low": float(candle.low),
                        "high": float(candle.high),
                    },
                ))
            
            # Проверка open в диапазоне [low, high]
            if candle.open < candle.low or candle.open > candle.high:
                errors.append(DataQualityError(
                    check_type=QualityCheckType.OHLC_VALUES,
                    message=f"Open outside [Low, High] range at index {i}",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=candle.timestamp if hasattr(candle, 'timestamp') else None,
                    details={
                        "index": i,
                        "open": float(candle.open),
                        "low": float(candle.low),
                        "high": float(candle.high),
                    },
                ))
        
        if errors:
            return DataQualityCheck(
                check_type=QualityCheckType.OHLC_VALUES,
                passed=False,
                score=0.0,
                errors=errors,
            )
        
        return DataQualityCheck(
            check_type=QualityCheckType.OHLC_VALUES,
            passed=True,
            score=1.0,
        )
    
    def check_duplicates(
        self,
        candles: list[models.Candle],
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = ""
    ) -> DataQualityCheck:
        """
        Проверить дубликаты свечей.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Результат проверки
        """
        if not candles:
            return DataQualityCheck(
                check_type=QualityCheckType.DUPLICATES,
                passed=True,
                score=1.0,
            )
        
        warnings = []
        errors = []
        
        # Создать словарь timestamp -> индексы
        timestamp_map = {}
        for i, candle in enumerate(candles):
            if hasattr(candle, 'timestamp') and candle.timestamp:
                ts = candle.timestamp
                if ts in timestamp_map:
                    timestamp_map[ts].append(i)
                else:
                    timestamp_map[ts] = [i]
        
        # Найти дубликаты
        duplicates = {ts: indices for ts, indices in timestamp_map.items() if len(indices) > 1}
        
        if duplicates:
            for ts, indices in duplicates.items():
                errors.append(DataQualityError(
                    check_type=QualityCheckType.DUPLICATES,
                    message=f"Duplicate timestamp {ts} at indices {indices}",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    details={"indices": indices},
                ))
            
            return DataQualityCheck(
                check_type=QualityCheckType.DUPLICATES,
                passed=False,
                score=0.0,
                errors=errors,
            )
        
        return DataQualityCheck(
            check_type=QualityCheckType.DUPLICATES,
            passed=True,
            score=1.0,
        )
    
    def check_gaps(
        self,
        candles: list[models.Candle],
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = ""
    ) -> DataQualityCheck:
        """
        Проверить пропуски в данных.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Результат проверки
        """
        if len(candles) < 2:
            return DataQualityCheck(
                check_type=QualityCheckType.GAPS,
                passed=True,
                score=1.0,
            )
        
        warnings = []
        errors = []
        
        timestamps = [c.timestamp for c in candles if hasattr(c, 'timestamp') and c.timestamp]
        
        if len(timestamps) < 2:
            return DataQualityCheck(
                check_type=QualityCheckType.GAPS,
                passed=True,
                score=1.0,
            )
        
        expected_interval = self._get_expected_interval_seconds(timeframe)
        
        if not expected_interval:
            return DataQualityCheck(
                check_type=QualityCheckType.GAPS,
                passed=True,
                score=1.0,
                warnings=[DataQualityWarning(
                    check_type=QualityCheckType.GAPS,
                    message=f"Unknown timeframe: {timeframe}",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                )],
            )
        
        # Проверка пропусков
        gaps = []
        for i in range(1, len(timestamps)):
            actual_interval = (timestamps[i] - timestamps[i-1]).total_seconds()
            if actual_interval > expected_interval * 1.5:  # Более 50% ожидаемого интервала
                gap_size = actual_interval - expected_interval
                gaps.append({
                    "index": i,
                    "gap_seconds": gap_size,
                    "expected": expected_interval,
                    "actual": actual_interval,
                })
        
        if gaps:
            for gap in gaps:
                if gap["gap_seconds"] > expected_interval * 5:  # Большой пропуск
                    errors.append(DataQualityError(
                        check_type=QualityCheckType.GAPS,
                        message=f"Large gap: {gap['gap_seconds']}s at index {gap['index']}",
                        dataset_id=dataset_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=timestamps[gap["index"]],
                        details=gap,
                    ))
                else:
                    warnings.append(DataQualityWarning(
                        check_type=QualityCheckType.GAPS,
                        message=f"Gap: {gap['gap_seconds']}s at index {gap['index']}",
                        dataset_id=dataset_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=timestamps[gap["index"]],
                        details=gap,
                    ))
        
        score = 1.0
        if errors:
            score = 0.0
        elif warnings:
            score = 0.8 - min(0.5, len(warnings) * 0.05)
        
        return DataQualityCheck(
            check_type=QualityCheckType.GAPS,
            passed=not errors,
            score=score,
            warnings=warnings,
            errors=errors,
        )
    
    def check_volume_anomalies(
        self,
        candles: list[models.Candle],
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = ""
    ) -> DataQualityCheck:
        """
        Проверить аномалии объема.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Результат проверки
        """
        if not candles:
            return DataQualityCheck(
                check_type=QualityCheckType.VOLUME,
                passed=True,
                score=1.0,
            )
        
        warnings = []
        errors = []
        
        volumes = [float(c.volume) for c in candles if hasattr(c, 'volume') and c.volume is not None]
        
        if not volumes:
            warnings.append(DataQualityWarning(
                check_type=QualityCheckType.VOLUME,
                message="No volume data available",
                dataset_id=dataset_id,
                symbol=symbol,
                timeframe=timeframe,
            ))
            return DataQualityCheck(
                check_type=QualityCheckType.VOLUME,
                passed=True,
                score=0.8,
                warnings=warnings,
            )
        
        # Проверка нулевого объема
        zero_volume_indices = [i for i, v in enumerate(volumes) if v == 0]
        if zero_volume_indices:
            warnings.append(DataQualityWarning(
                check_type=QualityCheckType.VOLUME,
                message=f"Zero volume at indices: {zero_volume_indices}",
                dataset_id=dataset_id,
                symbol=symbol,
                timeframe=timeframe,
                details={"zero_volume_count": len(zero_volume_indices)},
            ))
        
        # Проверка аномальных значений (выбросов)
        if len(volumes) > 10:
            median_volume = np.median(volumes)
            std_volume = np.std(volumes)
            
            anomalies = []
            for i, v in enumerate(volumes):
                if std_volume > 0:
                    z_score = abs(v - median_volume) / std_volume
                    if z_score > 5:  # Более 5 стандартных отклонений
                        anomalies.append(i)
            
            if anomalies:
                warnings.append(DataQualityWarning(
                    check_type=QualityCheckType.VOLUME,
                    message=f"Volume anomalies (z-score > 5) at indices: {anomalies}",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    details={
                        "anomaly_count": len(anomalies),
                        "median_volume": float(median_volume),
                        "std_volume": float(std_volume),
                    },
                ))
        
        score = 1.0
        if warnings:
            score = 0.9 - min(0.4, len(warnings) * 0.1)
        
        return DataQualityCheck(
            check_type=QualityCheckType.VOLUME,
            passed=True,
            score=score,
            warnings=warnings,
        )
    
    def check_anomalies(
        self,
        candles: list[models.Candle],
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = ""
    ) -> DataQualityCheck:
        """
        Проверить резкие аномальные изменения цены.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Результат проверки
        """
        if len(candles) < 2:
            return DataQualityCheck(
                check_type=QualityCheckType.ANOMALIES,
                passed=True,
                score=1.0,
            )
        
        warnings = []
        errors = []
        
        closes = [float(c.close) for c in candles if hasattr(c, 'close') and c.close is not None]
        
        if len(closes) < 2:
            return DataQualityCheck(
                check_type=QualityCheckType.ANOMALIES,
                passed=True,
                score=1.0,
            )
        
        # Рассчитать возвраты
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        
        # Рассчитать статистику
        median_return = np.median(returns)
        std_return = np.std(returns)
        
        # Проверка аномальных возвратов
        if std_return > 0:
            anomalies = []
            for i, r in enumerate(returns):
                z_score = abs(r - median_return) / std_return
                if z_score > 10:  # Более 10 стандартных отклонений
                    anomalies.append({
                        "index": i + 1,
                        "return": r,
                        "z_score": z_score,
                    })
            
            if anomalies:
                for anomaly in anomalies:
                    errors.append(DataQualityError(
                        check_type=QualityCheckType.ANOMALIES,
                        message=f"Extreme return anomaly: {anomaly['return']:.4%} (z-score: {anomaly['z_score']:.2f})",
                        dataset_id=dataset_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=candles[anomaly["index"]].timestamp if hasattr(candles[anomaly["index"]], 'timestamp') else None,
                        details=anomaly,
                    ))
        
        score = 1.0 if not errors else 0.0
        
        return DataQualityCheck(
            check_type=QualityCheckType.ANOMALIES,
            passed=not errors,
            score=score,
            warnings=warnings,
            errors=errors,
        )
    
    def check_staleness(
        self,
        candles: list[models.Candle],
        current_time: datetime | None = None,
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = ""
    ) -> DataQualityCheck:
        """
        Проверить устарелость данных.
        
        Args:
            candles: Список свечей
            current_time: Текущее время
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
        
        Returns:
            Результат проверки
        """
        if not candles:
            return DataQualityCheck(
                check_type=QualityCheckType.STALENESS,
                passed=True,
                score=1.0,
            )
        
        warnings = []
        errors = []
        
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        
        # Последний timestamp
        last_timestamp = candles[-1].timestamp if hasattr(candles[-1], 'timestamp') and candles[-1].timestamp else None
        
        if not last_timestamp:
            warnings.append(DataQualityWarning(
                check_type=QualityCheckType.STALENESS,
                message="Cannot determine last timestamp",
                dataset_id=dataset_id,
                symbol=symbol,
                timeframe=timeframe,
            ))
            return DataQualityCheck(
                check_type=QualityCheckType.STALENESS,
                passed=True,
                score=0.8,
                warnings=warnings,
            )
        
        # Рассчитать время с последней свечи
        time_since_last = (current_time - last_timestamp).total_seconds()
        expected_interval = self._get_expected_interval_seconds(timeframe)
        
        if expected_interval:
            # Если прошло больше 2 интервалов
            if time_since_last > expected_interval * 2:
                warnings.append(DataQualityWarning(
                    check_type=QualityCheckType.STALENESS,
                    message=f"Stale data: {time_since_last}s since last candle (expected ~{expected_interval}s)",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=last_timestamp,
                    details={
                        "time_since_last_seconds": time_since_last,
                        "expected_interval": expected_interval,
                    },
                ))
            
            # Если прошло больше 10 интервалов - критическая ошибка
            if time_since_last > expected_interval * 10:
                errors.append(DataQualityError(
                    check_type=QualityCheckType.STALENESS,
                    message=f"Critically stale data: {time_since_last}s since last candle",
                    dataset_id=dataset_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=last_timestamp,
                    details={
                        "time_since_last_seconds": time_since_last,
                        "expected_interval": expected_interval,
                    },
                ))
        
        score = 1.0
        if errors:
            score = 0.0
        elif warnings:
            score = 0.7
        
        return DataQualityCheck(
            check_type=QualityCheckType.STALENESS,
            passed=not errors,
            score=score,
            warnings=warnings,
            errors=errors,
        )
    
    def _get_expected_interval_seconds(self, timeframe: str) -> float | None:
        """Получить ожидаемый интервал в секундах для таймфрейма"""
        timeframe_map = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
            "1D": 86400,
        }
        return timeframe_map.get(timeframe.lower())
    
    def calculate_checksum(self, data: Any) -> str:
        """Рассчитать checksum для данных"""
        import json
        data_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(data_str.encode()).hexdigest()
    
    def assess_data_quality(
        self,
        candles: list[models.Candle],
        dataset_id: str = "",
        symbol: str = "",
        timeframe: str = "",
        source: str = "",
        current_time: datetime | None = None,
    ) -> DataQualityResult:
        """
        Полная оценка качества данных.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            symbol: Символ
            timeframe: Таймфрейм
            source: Источник
            current_time: Текущее время
        
        Returns:
            Полный результат проверки
        """
        checks = {}
        all_warnings = []
        all_errors = []
        total_weight = 0.0
        weighted_score = 0.0
        
        # Выполнить все проверки
        checks[QualityCheckType.TIMESTAMP] = self.check_timestamp_sequence(
            candles, dataset_id, symbol, timeframe
        )
        
        checks[QualityCheckType.TIMEZONE] = self.check_timezone_consistency(
            candles, dataset_id, symbol, timeframe
        )
        
        checks[QualityCheckType.DUPLICATES] = self.check_duplicates(
            candles, dataset_id, symbol, timeframe
        )
        
        checks[QualityCheckType.GAPS] = self.check_gaps(
            candles, dataset_id, symbol, timeframe
        )
        
        checks[QualityCheckType.OHLC_VALUES] = self.check_ohlc_values(
            candles, dataset_id, symbol, timeframe
        )
        
        checks[QualityCheckType.VOLUME] = self.check_volume_anomalies(
            candles, dataset_id, symbol, timeframe
        )
        
        checks[QualityCheckType.ANOMALIES] = self.check_anomalies(
            candles, dataset_id, symbol, timeframe
        )
        
        checks[QualityCheckType.STALENESS] = self.check_staleness(
            candles, current_time, dataset_id, symbol, timeframe
        )
        
        # Собрать все предупреждения и ошибки
        for check in checks.values():
            all_warnings.extend(check.warnings)
            all_errors.extend(check.errors)
            weight = self.check_weights.get(check.check_type, 0.0)
            if weight > 0:
                weighted_score += check.score * weight
                total_weight += weight
        
        # Рассчитать итоговый score
        quality_score = weighted_score / total_weight if total_weight > 0 else 0.0
        
        # Определить уровень качества
        if quality_score >= self.quality_thresholds[QualityLevel.EXCELLENT]:
            quality_level = QualityLevel.EXCELLENT
        elif quality_score >= self.quality_thresholds[QualityLevel.GOOD]:
            quality_level = QualityLevel.GOOD
        elif quality_score >= self.quality_thresholds[QualityLevel.FAIR]:
            quality_level = QualityLevel.FAIR
        elif quality_score >= self.quality_thresholds[QualityLevel.POOR]:
            quality_level = QualityLevel.POOR
        else:
            quality_level = QualityLevel.BAD
        
        # Если есть критические ошибки - уровень CRITICAL
        if all_errors:
            quality_level = QualityLevel.CRITICAL
        
        # Создать checksum
        checksum = self.calculate_checksum({
            "dataset_id": dataset_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [
                {
                    "timestamp": c.timestamp.isoformat() if hasattr(c, 'timestamp') and c.timestamp else None,
                    "open": float(c.open) if hasattr(c, 'open') else None,
                    "high": float(c.high) if hasattr(c, 'high') else None,
                    "low": float(c.low) if hasattr(c, 'low') else None,
                    "close": float(c.close) if hasattr(c, 'close') else None,
                    "volume": float(c.volume) if hasattr(c, 'volume') else None,
                }
                for c in candles
            ],
        })
        
        # Создать start и end
        start = candles[0].timestamp if candles and hasattr(candles[0], 'timestamp') else datetime.now(timezone.utc)
        end = candles[-1].timestamp if candles and hasattr(candles[-1], 'timestamp') else datetime.now(timezone.utc)
        
        return DataQualityResult(
            dataset_id=dataset_id,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            total_checks=len(checks),
            passed_checks=sum(1 for c in checks.values() if c.passed),
            warnings_count=len(all_warnings),
            errors_count=len(all_errors),
            quality_score=quality_score,
            quality_level=quality_level,
            checks=checks,
            warnings=all_warnings,
            errors=all_errors,
            checksum=checksum,
        )
    
    def create_dataset_metadata(
        self,
        candles: list[models.Candle],
        dataset_id: str,
        source: str,
        symbol: str,
        timeframe: str,
        download_time: datetime | None = None,
    ) -> DatasetMetadata:
        """
        Создать метаданные dataset.
        
        Args:
            candles: Список свечей
            dataset_id: ID dataset
            source: Источник
            symbol: Символ
            timeframe: Таймфрейм
            download_time: Время загрузки
        
        Returns:
            Метаданные dataset
        """
        if download_time is None:
            download_time = datetime.now(timezone.utc)
        
        # Выполнить проверку качества
        quality_result = self.assess_data_quality(
            candles, dataset_id, symbol, timeframe, source
        )
        
        # Рассчитать checksum
        checksum = self.calculate_checksum(candles)
        
        # Создать метаданные
        return DatasetMetadata(
            dataset_id=dataset_id,
            source=source,
            symbol=symbol,
            timeframe=timeframe,
            start=quality_result.start,
            end=quality_result.end,
            download_time=download_time,
            version="1.0",
            checksum=checksum,
            quality_score=quality_result.quality_score,
            quality_level=quality_result.quality_level,
            num_candles=len(candles),
            num_gaps=quality_result.details.get("num_gaps", 0),
            num_duplicates=sum(1 for c in quality_result.checks.values() 
                              if c.check_type == QualityCheckType.DUPLICATES and not c.passed),
            anomalies=quality_result.details.get("anomalies", []),
        )


# Глобальный экземпляр
_data_quality_engine: DataQualityEngine | None = None


def get_data_quality_engine() -> DataQualityEngine:
    """Получить глобальный Data Quality Engine"""
    global _data_quality_engine
    if _data_quality_engine is None:
        _data_quality_engine = DataQualityEngine()
    return _data_quality_engine


def reset_data_quality_engine():
    """Сбросить Data Quality Engine (для тестов)"""
    global _data_quality_engine
    _data_quality_engine = DataQualityEngine()
