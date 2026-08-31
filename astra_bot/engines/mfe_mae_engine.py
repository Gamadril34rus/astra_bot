"""
ASTRA BOT — MFE/MAE Engine

Движок отслеживания MFE и MAE (Master Specification v2, Section 19)

Для каждой позиции рассчитывает:
- MFE (Maximum Favorable Excursion)
- MAE (Maximum Adverse Excursion)

по отношению к:
- entry
- stop
- target
- exit

Это позволяет определить:
- entry quality
- exit quality
- stop quality
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ExcursionPoint:
    """Точка экскурсии (цена и время)"""
    price: float
    timestamp: datetime
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class MFEMAEResult:
    """Результаты MFE/MAE для позиции"""
    position_id: str
    symbol: str
    direction: str  # long/short
    
    # Цены
    entry_price: float
    stop_price: float | None = None
    target_price: float | None = None
    exit_price: float | None = None
    
    # MFE/MAE относительно entry
    MFE_from_entry: float | None = None  # Максимальная прибыль от точки входа
    MAE_from_entry: float | None = None  # Максимальный убыток от точки входа
    MFE_price_from_entry: float | None = None
    MAE_price_from_entry: float | None = None
    MFE_time_from_entry: datetime | None = None
    MAE_time_from_entry: datetime | None = None
    
    # MFE/MAE относительно stop
    MFE_from_stop: float | None = None
    MAE_from_stop: float | None = None
    
    # MFE/MAE относительно target
    MFE_from_target: float | None = None
    MAE_from_target: float | None = None
    
    # MFE/MAE относительно exit
    MFE_from_exit: float | None = None
    MAE_from_exit: float | None = None
    
    # Качество
    entry_quality: float | None = None  # Качество входа
    exit_quality: float | None = None  # Качество выхода
    stop_quality: float | None = None  # Качество стопа
    
    # Временные метки
    created_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "exit_price": self.exit_price,
            "MFE_from_entry": self.MFE_from_entry,
            "MAE_from_entry": self.MAE_from_entry,
            "MFE_price_from_entry": self.MFE_price_from_entry,
            "MAE_price_from_entry": self.MAE_price_from_entry,
            "MFE_from_stop": self.MFE_from_stop,
            "MAE_from_stop": self.MAE_from_stop,
            "MFE_from_target": self.MFE_from_target,
            "MAE_from_target": self.MAE_from_target,
            "MFE_from_exit": self.MFE_from_exit,
            "MAE_from_exit": self.MAE_from_exit,
            "entry_quality": self.entry_quality,
            "exit_quality": self.exit_quality,
            "stop_quality": self.stop_quality,
            "created_at": self.created_at.isoformat(),
        }
        
        if self.MFE_time_from_entry:
            result["MFE_time_from_entry"] = self.MFE_time_from_entry.isoformat()
        if self.MAE_time_from_entry:
            result["MAE_time_from_entry"] = self.MAE_time_from_entry.isoformat()
        
        return result


@dataclass
class PricePoint:
    """Точка цены во времени"""
    price: float
    timestamp: datetime


class MFEMAEEngine:
    """
    Движок отслеживания MFE и MAE.
    
    Рассчитывает максимальные благоприятные и неблагоприятные экскурсии
    для каждой позиции относительно различных точек отсчёта.
    """
    
    def __init__(self):
        # Хранение результатов
        self.results: dict[str, MFEMAEResult] = {}
        
        # Текущие позиции для отслеживания
        self.active_positions: dict[str, list[PricePoint]] = {}
    
    def add_price_point(
        self,
        position_id: str,
        price: float,
        timestamp: datetime
    ) -> None:
        """
        Добавить точку цены для отслеживания позиции.
        
        Args:
            position_id: Идентификатор позиции
            price: Текущая цена
            timestamp: Временная метка
        """
        if position_id not in self.active_positions:
            self.active_positions[position_id] = []
        
        self.active_positions[position_id].append(PricePoint(
            price=price,
            timestamp=timestamp
        ))
    
    def calculate_MFE_MAE_from_reference(
        self,
        price_points: list[PricePoint],
        reference_price: float,
        direction: str
    ) -> tuple[float, float, float, float, datetime, datetime]:
        """
        Рассчитать MFE и MAE относительно опорной цены.
        
        Args:
            price_points: Список точек цены
            reference_price: Опорная цена
            direction: Направление позиции (long/short)
        
        Returns:
            (MFE, MAE, MFE_price, MAE_price, MFE_time, MAE_time)
        """
        if not price_points:
            return 0.0, 0.0, reference_price, reference_price, datetime.now(), datetime.now()
        
        if direction == "long":
            # Для лонга: MFE = max(price - reference), MAE = max(reference - price)
            price_diffs = [p.price - reference_price for p in price_points]
            MFE = max(price_diffs) if price_diffs else 0.0
            MAE = max([-d for d in price_diffs if d < 0] + [0.0])
            
            # Найти цены и времена
            MFE_index = price_diffs.index(MFE) if MFE in price_diffs else 0
            MAE_index = price_diffs.index(-MAE) if -MAE in price_diffs else 0
            
            MFE_price = price_points[MFE_index].price
            MAE_price = price_points[MAE_index].price
            MFE_time = price_points[MFE_index].timestamp
            MAE_time = price_points[MAE_index].timestamp
        else:  # short
            # Для шорта: MFE = max(reference - price), MAE = max(price - reference)
            price_diffs = [reference_price - p.price for p in price_points]
            MFE = max(price_diffs) if price_diffs else 0.0
            MAE = max([-d for d in price_diffs if d < 0] + [0.0])
            
            # Найти цены и времена
            MFE_index = price_diffs.index(MFE) if MFE in price_diffs else 0
            MAE_index = price_diffs.index(-MAE) if -MAE in price_diffs else 0
            
            MFE_price = price_points[MFE_index].price
            MAE_price = price_points[MAE_index].price
            MFE_time = price_points[MFE_index].timestamp
            MAE_time = price_points[MAE_index].timestamp
        
        return MFE, MAE, MFE_price, MAE_price, MFE_time, MAE_time
    
    def calculate_position_MFE_MAE(
        self,
        position_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_price: float | None = None,
        target_price: float | None = None,
        exit_price: float | None = None
    ) -> MFEMAEResult:
        """
        Рассчитать MFE/MAE для позиции.
        
        Args:
            position_id: Идентификатор позиции
            symbol: Символ инструмента
            direction: Направление (long/short)
            entry_price: Цена входа
            stop_price: Цена стопа
            target_price: Цена тейк-профита
            exit_price: Цена выхода
        
        Returns:
            MFEMAEResult
        """
        price_points = self.active_positions.get(position_id, [])
        
        # Добавить точку входа
        if price_points:
            entry_point = PricePoint(price=entry_price, timestamp=price_points[0].timestamp)
            price_points = [entry_point] + price_points
        else:
            price_points = [PricePoint(price=entry_price, timestamp=datetime.now())]
        
        # Добавить точку выхода
        if exit_price is not None:
            exit_point = PricePoint(price=exit_price, timestamp=datetime.now())
            price_points.append(exit_point)
        
        # Рассчитать MFE/MAE относительно entry
        MFE_entry, MAE_entry, MFE_price_entry, MAE_price_entry, MFE_time_entry, MAE_time_entry = (
            self.calculate_MFE_MAE_from_reference(price_points, entry_price, direction)
        )
        
        # Рассчитать MFE/MAE относительно stop
        MFE_stop, MAE_stop = 0.0, 0.0
        if stop_price is not None:
            MFE_stop, MAE_stop, _, _, _, _ = (
                self.calculate_MFE_MAE_from_reference(price_points, stop_price, direction)
            )
        
        # Рассчитать MFE/MAE относительно target
        MFE_target, MAE_target = 0.0, 0.0
        if target_price is not None:
            MFE_target, MAE_target, _, _, _, _ = (
                self.calculate_MFE_MAE_from_reference(price_points, target_price, direction)
            )
        
        # Рассчитать MFE/MAE относительно exit
        MFE_exit, MAE_exit = 0.0, 0.0
        if exit_price is not None:
            MFE_exit, MAE_exit, _, _, _, _ = (
                self.calculate_MFE_MAE_from_reference(price_points, exit_price, direction)
            )
        
        # Рассчитать качество входа/выхода/стопа
        entry_quality = self._calculate_entry_quality(
            MFE_entry, MAE_entry, entry_price, direction
        )
        
        exit_quality = self._calculate_exit_quality(
            MFE_entry, MAE_entry, exit_price, direction
        )
        
        stop_quality = self._calculate_stop_quality(
            MFE_entry, MAE_entry, stop_price, direction
        )
        
        result = MFEMAEResult(
            position_id=position_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            exit_price=exit_price,
            MFE_from_entry=MFE_entry,
            MAE_from_entry=MAE_entry,
            MFE_price_from_entry=MFE_price_entry,
            MAE_price_from_entry=MAE_price_entry,
            MFE_time_from_entry=MFE_time_entry,
            MAE_time_from_entry=MAE_time_entry,
            MFE_from_stop=MFE_stop,
            MAE_from_stop=MAE_stop,
            MFE_from_target=MFE_target,
            MAE_from_target=MAE_target,
            MFE_from_exit=MFE_exit,
            MAE_from_exit=MAE_exit,
            entry_quality=entry_quality,
            exit_quality=exit_quality,
            stop_quality=stop_quality
        )
        
        self.results[position_id] = result
        
        # Удалить позицию из активных
        if position_id in self.active_positions:
            del self.active_positions[position_id]
        
        return result
    
    def _calculate_entry_quality(
        self,
        MFE: float,
        MAE: float,
        entry_price: float,
        direction: str
    ) -> float:
        """
        Рассчитать качество входа.
        
        Args:
            MFE: Максимальная благоприятная экскурсия
            MAE: Максимальная неблагоприятная экскурсия
            entry_price: Цена входа
            direction: Направление
        
        Returns:
            Качество входа (0-1)
        """
        if MFE <= 0 and MAE <= 0:
            return 0.5  # Нейтральный вход
        
        # Качество входа зависит от соотношения MFE к MAE
        if MAE == 0:
            return 1.0  # Отличный вход
        
        ratio = MFE / (MFE + MAE)
        return ratio
    
    def _calculate_exit_quality(
        self,
        MFE: float,
        MAE: float,
        exit_price: float | None,
        direction: str
    ) -> float | None:
        """
        Рассчитать качество выхода.
        
        Args:
            MFE: Максимальная благоприятная экскурсия
            MAE: Максимальная неблагоприятная экскурсия
            exit_price: Цена выхода
            direction: Направление
        
        Returns:
            Качество выхода (0-1) или None
        """
        if exit_price is None:
            return None
        
        if MFE <= 0:
            return 0.5
        
        # Качество выхода = насколько близко к MFE был выход
        if direction == "long":
            exit_from_entry = exit_price - (exit_price - MFE)
            quality = exit_from_entry / MFE if MFE > 0 else 0.0
        else:
            exit_from_entry = (exit_price - MFE) - exit_price
            quality = abs(exit_from_entry) / abs(MFE) if MFE != 0 else 0.0
        
        return min(1.0, max(0.0, quality))
    
    def _calculate_stop_quality(
        self,
        MFE: float,
        MAE: float,
        stop_price: float | None,
        direction: str
    ) -> float | None:
        """
        Рассчитать качество стопа.
        
        Args:
            MFE: Максимальная благоприятная экскурсия
            MAE: Максимальная неблагоприятная экскурсия
            stop_price: Цена стопа
            direction: Направление
        
        Returns:
            Качество стопа (0-1) или None
        """
        if stop_price is None:
            return None
        
        if MAE <= 0:
            return 1.0  # Стоп не срабатывал
        
        # Качество стопа = насколько далеко от MAE был стоп
        # Хороший стоп не даёт позиции дойти до MAE
        if direction == "long":
            stop_distance = stop_price - (stop_price - MAE)
            quality = 1.0 - (abs(stop_distance) / MAE) if MAE > 0 else 1.0
        else:
            stop_distance = (MAE - stop_price) - MAE
            quality = 1.0 - (abs(stop_distance) / MAE) if MAE > 0 else 1.0
        
        return min(1.0, max(0.0, quality))
    
    def classify_trade_outcome(
        self,
        result: MFEMAEResult
    ) -> str:
        """
        Классифицировать исход сделки (Section 20).
        
        Разделить:
        - GOOD ENTRY + BAD EXIT
        - GOOD ENTRY + GOOD EXIT
        - BAD ENTRY + GOOD EXIT
        - BAD ENTRY + BAD EXIT
        
        Args:
            result: Результат MFE/MAE
        
        Returns:
            Классификация исхода
        """
        if result.entry_quality is None or result.exit_quality is None:
            return "UNKNOWN"
        
        entry_threshold = 0.6
        exit_threshold = 0.6
        
        if result.entry_quality >= entry_threshold:
            if result.exit_quality >= exit_threshold:
                return "GOOD_ENTRY_GOOD_EXIT"
            else:
                return "GOOD_ENTRY_BAD_EXIT"
        else:
            if result.exit_quality >= exit_threshold:
                return "BAD_ENTRY_GOOD_EXIT"
            else:
                return "BAD_ENTRY_BAD_EXIT"
    
    def get_trade_statistics(
        self,
        results: list[MFEMAEResult]
    ) -> dict[str, Any]:
        """
        Получить статистику по сделкам.
        
        Args:
            results: Список результатов MFE/MAE
        
        Returns:
            Статистика по сделкам
        """
        if not results:
            return {}
        
        # Рассчитать средние значения
        avg_MFE = np.mean([r.MFE_from_entry for r in results if r.MFE_from_entry is not None])
        avg_MAE = np.mean([r.MAE_from_entry for r in results if r.MAE_from_entry is not None])
        avg_entry_quality = np.mean([r.entry_quality for r in results if r.entry_quality is not None])
        avg_exit_quality = np.mean([r.exit_quality for r in results if r.exit_quality is not None])
        avg_stop_quality = np.mean([r.stop_quality for r in results if r.stop_quality is not None])
        
        # Классифицировать исходы
        outcomes = [self.classify_trade_outcome(r) for r in results]
        outcome_counts = {}
        for outcome in outcomes:
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        
        return {
            "avg_MFE": avg_MFE,
            "avg_MAE": avg_MAE,
            "avg_entry_quality": avg_entry_quality,
            "avg_exit_quality": avg_exit_quality,
            "avg_stop_quality": avg_stop_quality,
            "outcome_counts": outcome_counts,
            "total_trades": len(results),
        }
    
    def cleanup_old_results(self, max_age_days: int = 30) -> int:
        """
        Очистить старые результаты.
        
        Args:
            max_age_days: Максимальный возраст результатов в днях
        
        Returns:
            Количество удалённых результатов
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        results_to_remove = [
            key for key, result in self.results.items()
            if result.created_at < cutoff
        ]
        
        for key in results_to_remove:
            del self.results[key]
        
        return len(results_to_remove)


# Глобальный экземпляр MFE/MAE Engine
_mfe_mae_engine: MFEMAEEngine | None = None


def get_mfe_mae_engine() -> MFEMAEEngine:
    """Получить глобальный MFE/MAE Engine"""
    global _mfe_mae_engine
    if _mfe_mae_engine is None:
        _mfe_mae_engine = MFEMAEEngine()
    return _mfe_mae_engine


def reset_mfe_mae_engine():
    """Сбросить MFE/MAE Engine (для тестов)"""
    global _mfe_mae_engine
    _mfe_mae_engine = MFEMAEEngine()
