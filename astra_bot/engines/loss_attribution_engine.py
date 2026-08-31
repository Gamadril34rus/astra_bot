"""
ASTRA BOT — Loss Attribution Engine

Движок классификации убыточных сделок (Master Specification v2, Section 27)

Каждую убыточную сделку классифицировать:
- BAD_SIGNAL
- BAD_REGIME
- BAD_ENTRY
- BAD_EXIT
- EXECUTION_ERROR
- SLIPPAGE
- FEES
- FUNDING
- NEWS_SHOCK
- MODEL_ERROR
- DATA_ERROR
- RISK_ERROR
- UNKNOWN

Создать статистику: losses_by_cause
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class LossCause(str, Enum):
    """Причины убытков"""
    BAD_SIGNAL = "BAD_SIGNAL"
    BAD_REGIME = "BAD_REGIME"
    BAD_ENTRY = "BAD_ENTRY"
    BAD_EXIT = "BAD_EXIT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    SLIPPAGE = "SLIPPAGE"
    FEES = "FEES"
    FUNDING = "FUNDING"
    NEWS_SHOCK = "NEWS_SHOCK"
    MODEL_ERROR = "MODEL_ERROR"
    DATA_ERROR = "DATA_ERROR"
    RISK_ERROR = "RISK_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class LossAttribution:
    """Классификация убытка"""
    trade_id: str
    symbol: str
    pnl: float
    
    # Причины
    primary_cause: LossCause
    secondary_causes: list[LossCause] = field(default_factory=list)
    
    # Детали
    details: dict[str, Any] = field(default_factory=dict)
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "pnl": self.pnl,
            "primary_cause": self.primary_cause.value,
            "secondary_causes": [c.value for c in self.secondary_causes],
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class TradeContext:
    """Контекст сделки для анализа"""
    trade_id: str
    symbol: str
    direction: str  # long/short
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    volatility: float
    spread: float
    volume: float
    signal_strength: float
    signal_confidence: float
    regime: str
    regime_confidence: float
    execution_type: str
    stop_loss: float | None = None
    take_profit: float | None = None
    slippage: float | None = None
    fees: float = 0.0
    funding: float = 0.0
    news_score: float = 0.0
    model_version: str = ""
    data_quality: str = "good"
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "volatility": self.volatility,
            "spread": self.spread,
            "volume": self.volume,
            "signal_strength": self.signal_strength,
            "signal_confidence": self.signal_confidence,
            "regime": self.regime,
            "regime_confidence": self.regime_confidence,
            "execution_type": self.execution_type,
            "slippage": self.slippage,
            "fees": self.fees,
            "funding": self.funding,
            "news_score": self.news_score,
            "model_version": self.model_version,
            "data_quality": self.data_quality,
        }
        
        if self.stop_loss is not None:
            result["stop_loss"] = self.stop_loss
        if self.take_profit is not None:
            result["take_profit"] = self.take_profit
        
        return result


class LossAttributionEngine:
    """
    Движок классификации убыточных сделок.
    
    Анализирует каждую убыточную сделку и определяет её основную причину.
    """
    
    def __init__(self):
        # Статистика по причинам
        self.losses_by_cause: dict[LossCause, int] = {}
        for cause in LossCause:
            self.losses_by_cause[cause] = 0
        
        # Хранение классификаций
        self.attributions: dict[str, LossAttribution] = {}
        
        # Пороги для классификации
        self.thresholds = {
            "high_volatility": 0.05,
            "high_spread": 0.01,
            "low_volume": 1000,
            "low_confidence": 0.5,
            "high_slippage": 0.005,
            "high_fees": 0.002,
            "high_news": 70,
            "bad_regime_confidence": 0.6,
        }
    
    def classify_loss(self, context: TradeContext) -> LossAttribution:
        """
        Классифицировать убыточную сделку.
        
        Args:
            context: Контекст сделки
        
        Returns:
            LossAttribution
        """
        pnl = context.exit_price - context.entry_price
        if context.direction == "short":
            pnl = context.entry_price - context.exit_price
        
        # Собрать все возможные причины
        causes = self._identify_causes(context, pnl)
        
        # Определить основную причину
        primary_cause = self._determine_primary_cause(causes)
        
        # Создать классификацию
        attribution = LossAttribution(
            trade_id=context.trade_id,
            symbol=context.symbol,
            pnl=pnl,
            primary_cause=primary_cause,
            secondary_causes=causes,
            details=self._get_details(context, pnl)
        )
        
        # Обновить статистику
        self.losses_by_cause[primary_cause] += 1
        
        # Сохранить классификацию
        self.attributions[context.trade_id] = attribution
        
        return attribution
    
    def _identify_causes(
        self, 
        context: TradeContext, 
        pnl: float
    ) -> list[LossCause]:
        """
        Определить все возможные причины убытка.
        
        Args:
            context: Контекст сделки
            pnl: Фактический PnL
        
        Returns:
            Список возможных причин
        """
        causes = []
        
        # 1. BAD_SIGNAL
        if context.signal_strength < 0 or context.signal_confidence < self.thresholds["low_confidence"]:
            causes.append(LossCause.BAD_SIGNAL)
        
        # 2. BAD_REGIME
        if context.regime_confidence < self.thresholds["bad_regime_confidence"]:
            causes.append(LossCause.BAD_REGIME)
        
        # 3. BAD_ENTRY
        # Если цена быстро пошла против позиции после входа
        # (проверяем по волатильности и времени удержания)
        holding_time = (context.exit_time - context.entry_time).total_seconds()
        if holding_time < 300:  # Менее 5 минут
            price_change = abs(context.exit_price - context.entry_price) / context.entry_price
            if price_change > 0.01:  # Более 1% движения
                causes.append(LossCause.BAD_ENTRY)
        
        # 4. BAD_EXIT
        # Если позиция была закрыта в убыток, но потом рынок пошёл в нужном направлении
        # (пока не проверяем, так как нет данных после выхода)
        
        # 5. EXECUTION_ERROR
        if context.execution_type == "MARKET" and context.slippage and context.slippage > self.thresholds["high_slippage"]:
            causes.append(LossCause.EXECUTION_ERROR)
        
        # 6. SLIPPAGE
        if context.slippage and context.slippage > self.thresholds["high_slippage"]:
            causes.append(LossCause.SLIPPAGE)
        
        # 7. FEES
        if context.fees > self.thresholds["high_fees"]:
            causes.append(LossCause.FEES)
        
        # 8. FUNDING
        if abs(context.funding) > 0.001:  # Более 0.1% funding
            causes.append(LossCause.FUNDING)
        
        # 9. NEWS_SHOCK
        if context.news_score >= self.thresholds["high_news"]:
            causes.append(LossCause.NEWS_SHOCK)
        
        # 10. MODEL_ERROR
        # Если модель давала неправильные предсказания
        # (пока не проверяем)
        
        # 11. DATA_ERROR
        if context.data_quality != "good":
            causes.append(LossCause.DATA_ERROR)
        
        # 12. RISK_ERROR
        # Если сделка была слишком рискованной
        # (пока не проверяем)
        
        # Если не нашли явных причин
        if not causes:
            causes.append(LossCause.UNKNOWN)
        
        return causes
    
    def _determine_primary_cause(self, causes: list[LossCause]) -> LossCause:
        """
        Определить основную причину из списка.
        
        Args:
            causes: Список возможных причин
        
        Returns:
            Основная причина
        """
        # Приоритет причин (по важности)
        priority_order = [
            LossCause.NEWS_SHOCK,
            LossCause.DATA_ERROR,
            LossCause.MODEL_ERROR,
            LossCause.BAD_REGIME,
            LossCause.BAD_SIGNAL,
            LossCause.EXECUTION_ERROR,
            LossCause.SLIPPAGE,
            LossCause.BAD_ENTRY,
            LossCause.BAD_EXIT,
            LossCause.FEES,
            LossCause.FUNDING,
            LossCause.RISK_ERROR,
            LossCause.UNKNOWN,
        ]
        
        for cause in priority_order:
            if cause in causes:
                return cause
        
        return LossCause.UNKNOWN
    
    def _get_details(
        self, 
        context: TradeContext, 
        pnl: float
    ) -> dict[str, Any]:
        """
        Получить детали для классификации.
        
        Args:
            context: Контекст сделки
            pnl: Фактический PnL
        
        Returns:
            Словарь с деталями
        """
        details = {
            "pnl": pnl,
            "holding_time_seconds": (context.exit_time - context.entry_time).total_seconds(),
            "price_change_pct": abs(context.exit_price - context.entry_price) / context.entry_price * 100,
            "volatility": context.volatility,
            "spread_pct": context.spread,
            "volume": context.volume,
            "signal_strength": context.signal_strength,
            "signal_confidence": context.signal_confidence,
            "regime": context.regime,
            "regime_confidence": context.regime_confidence,
            "execution_type": context.execution_type,
            "slippage_pct": context.slippage,
            "fees_pct": context.fees,
            "funding_pct": context.funding,
            "news_score": context.news_score,
        }
        
        if context.stop_loss is not None:
            details["stop_loss"] = context.stop_loss
            details["stop_distance_pct"] = abs(context.stop_loss - context.entry_price) / context.entry_price * 100
        
        if context.take_profit is not None:
            details["take_profit"] = context.take_profit
            details["rr_ratio"] = abs(context.take_profit - context.entry_price) / abs(context.stop_loss - context.entry_price) if context.stop_loss else None
        
        return details
    
    def get_loss_statistics(self) -> dict[str, Any]:
        """
        Получить статистику по убыткам.
        
        Returns:
            Статистика по причинам
        """
        total_losses = sum(self.losses_by_cause.values())
        
        statistics = {
            "total_losses": total_losses,
            "losses_by_cause": {k.value: v for k, v in self.losses_by_cause.items()},
        }
        
        # Рассчитать проценты
        if total_losses > 0:
            statistics["losses_by_cause_pct"] = {
                k.value: (v / total_losses) * 100 
                for k, v in self.losses_by_cause.items()
            }
        
        return statistics
    
    def get_top_loss_causes(self, n: int = 5) -> list[tuple[LossCause, int]]:
        """
        Получить топ N причин убытков.
        
        Args:
            n: Количество причин
        
        Returns:
            Список топ причин
        """
        sorted_causes = sorted(
            self.losses_by_cause.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        return sorted_causes[:n]
    
    def get_loss_trends(self) -> dict[str, Any]:
        """
        Получить тренды убытков по времени.
        
        Returns:
            Тренды по времени
        """
        # Сгруппировать по дням
        daily_losses: dict[str, dict[LossCause, int]] = {}
        
        for trade_id, attribution in self.attributions.items():
            date_str = attribution.timestamp.strftime("%Y-%m-%d")
            if date_str not in daily_losses:
                daily_losses[date_str] = {cause: 0 for cause in LossCause}
            
            daily_losses[date_str][attribution.primary_cause] += 1
        
        return daily_losses
    
    def cleanup_old_attributions(self, max_age_days: int = 30) -> int:
        """
        Очистить старые классификации.
        
        Args:
            max_age_days: Максимальный возраст в днях
        
        Returns:
            Количество удалённых классификаций
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        attributions_to_remove = [
            key for key, attribution in self.attributions.items()
            if attribution.timestamp < cutoff
        ]
        
        for key in attributions_to_remove:
            del self.attributions[key]
        
        return len(attributions_to_remove)


# Глобальный экземпляр Loss Attribution Engine
_loss_attribution_engine: LossAttributionEngine | None = None


def get_loss_attribution_engine() -> LossAttributionEngine:
    """Получить глобальный Loss Attribution Engine"""
    global _loss_attribution_engine
    if _loss_attribution_engine is None:
        _loss_attribution_engine = LossAttributionEngine()
    return _loss_attribution_engine


def reset_loss_attribution_engine():
    """Сбросить Loss Attribution Engine (для тестов)"""
    global _loss_attribution_engine
    _loss_attribution_engine = LossAttributionEngine()
