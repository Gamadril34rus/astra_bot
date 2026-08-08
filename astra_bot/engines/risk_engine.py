"""
ASTRA BOT — Risk Engine
Движок управления рисками
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from ..core import events, models
from ..core.state import get_system_state, RiskState, TradingState
from ..core.utils import safe_decimal
from ..adapters.base import Instrument

logger = logging.getLogger(__name__)


class RiskDecision(Enum):
    """Решение риск-движка"""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REDUCED = "REDUCED"


@dataclass
class RiskConfig:
    """Конфигурация риска"""
    # Риск на сделку
    risk_per_trade: Decimal = Decimal("0.004")  # 0.4%
    
    # Дневные лимиты
    daily_loss_limit: Decimal = Decimal("0.02")  # 2%
    weekly_loss_limit: Decimal = Decimal("0.04")  # 4%
    
    # Просадки
    soft_drawdown: Decimal = Decimal("0.05")  # 5%
    hard_drawdown: Decimal = Decimal("0.08")  # 8%
    emergency_drawdown: Decimal = Decimal("0.10")  # 10%
    
    # Экспозиция
    max_exposure_pct: Decimal = Decimal("0.30")  # 30%
    max_open_positions: int = 5
    
    # Волатильность
    high_volatility_multiplier: Decimal = Decimal("0.5")
    extreme_volatility_threshold: Decimal = Decimal("0.15")
    volatility_lookback: int = 20
    
    # Корреляция
    correlation_limit: Decimal = Decimal("0.7")
    
    # Инкременты риска по просадке
    drawdown_adaptation: List[Dict] = field(default_factory=lambda: [
        {"drawdown": Decimal("0"), "risk_multiplier": Decimal("1.0")},
        {"drawdown": Decimal("0.03"), "risk_multiplier": Decimal("0.75")},
        {"drawdown": Decimal("0.05"), "risk_multiplier": Decimal("0.5")},
        {"drawdown": Decimal("0.08"), "risk_multiplier": Decimal("0.0")},
    ])


@dataclass
class PositionSizeResult:
    """Результат расчёта размера позиции"""
    accepted: bool
    quantity: Optional[Decimal] = None
    risk_amount: Optional[Decimal] = None
    risk_state: Optional[str] = None
    stop_distance: Optional[Decimal] = None
    reason: Optional[str] = None
    adjusted_size: Optional[Decimal] = None


@dataclass
class RiskCheckResult:
    """Результат проверки риска"""
    approved: bool
    risk_state: str
    daily_loss_used: Decimal
    weekly_loss_used: Decimal
    current_drawdown: Decimal
    max_allowed_loss: Decimal
    reason: Optional[str] = None
    details: Dict = field(default_factory=dict)


class RiskEngine:
    """
    Движок управления рисками.
    
    Проверяет все сделки на соответствие риск-параметрам.
    Содержит критические лимиты, которые нельзя обойти.
    """
    
    def __init__(self, config: RiskConfig = None):
        self.config = config or RiskConfig()
        
        # Текущие значения (обновляются из состояния)
        self._daily_pnl: Decimal = Decimal("0")
        self._weekly_pnl: Decimal = Decimal("0")
        self._current_equity: Decimal = Decimal("0")
        self._initial_capital: Decimal = Decimal("0")
        self._high_water_mark: Decimal = Decimal("0")
        
        # Открытые позиции
        self._open_positions: Dict[str, models.Position] = {}
        
        # История сделок за день/неделю
        self._today_trades: List[Dict] = []
        self._week_trades: List[Dict] = []
        
        # Статистика
        self._total_trades: int = 0
        self._total_wins: int = 0
        self._total_losses: int = 0
        
        # Режим
        self.risk_state = RiskState.NORMAL
        self.trading_enabled = True
    
    def set_capital(self, equity: Decimal, initial_capital: Decimal):
        """Установить текущий капитал"""
        self._current_equity = equity
        self._initial_capital = initial_capital
        self._update_high_water_mark()
    
    def _update_high_water_mark(self):
        """Обновить high water mark"""
        if self._current_equity > self._high_water_mark:
            self._high_water_mark = self._current_equity
    
    @property
    def current_drawdown(self) -> Decimal:
        """Текущая просадка в %"""
        if self._high_water_mark <= 0:
            return Decimal("0")
        return (self._high_water_mark - self._current_equity) / self._high_water_mark * Decimal("100")
    
    @property
    def daily_loss_pct(self) -> Decimal:
        """Потери за день в % от начального капитала"""
        if self._initial_capital <= 0:
            return Decimal("0")
        daily_limit = self._initial_capital * self.config.daily_loss_limit
        return abs(self._daily_pnl) / self._initial_capital * Decimal("100")
    
    @property
    def weekly_loss_pct(self) -> Decimal:
        """Потери за неделю в %"""
        if self._initial_capital <= 0:
            return Decimal("0")
        return abs(self._weekly_pnl) / self._initial_capital * Decimal("100")
    
    def _get_risk_multiplier(self) -> Decimal:
        """Получить множитель риска на основе просадки"""
        dd = self.current_drawdown
        
        for tier in self.config.drawdown_adaptation:
            if dd < tier["drawdown"]:
                return tier["risk_multiplier"]
        
        return Decimal("0")
    
    def check_trade(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        proposed_size: Decimal,
        strategy_name: str = "",
    ) -> RiskCheckResult:
        """
        Проверить сделку на соответствие риск-параметрам.
        
        Returns:
            RiskCheckResult с решением
        """
        # 1. Проверка состояния риска
        if not self.trading_enabled:
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=self._daily_pnl,
                weekly_loss_used=self._weekly_pnl,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=Decimal("0"),
                reason="Trading is disabled",
            )
        
        # 2. Проверка просадки
        if self.current_drawdown >= float(self.config.hard_drawdown) * 100:
            self.risk_state = RiskState.STOP
            self.trading_enabled = False
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=self._daily_pnl,
                weekly_loss_used=self._weekly_pnl,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=Decimal("0"),
                reason=f"Hard drawdown reached: {self.current_drawdown:.2f}%",
            )
        
        # 3. Проверка дневных потерь
        daily_loss = abs(self._daily_pnl)
        max_daily_loss = self._initial_capital * self.config.daily_loss_limit
        
        if daily_loss >= max_daily_loss:
            return RiskCheckResult(
                approved=False,
                risk_state=RiskState.REDUCED.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=self._weekly_pnl,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=Decimal("0"),
                reason=f"Daily loss limit reached: {daily_loss:.2f} / {max_daily_loss:.2f}",
            )
        
        # 4. Проверка недельных потерь
        weekly_loss = abs(self._weekly_pnl)
        max_weekly_loss = self._initial_capital * self.config.weekly_loss_limit
        
        if weekly_loss >= max_weekly_loss:
            return RiskCheckResult(
                approved=False,
                risk_state=RiskState.DEFENSIVE.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=Decimal("0"),
                reason=f"Weekly loss limit reached: {weekly_loss:.2f} / {max_weekly_loss:.2f}",
            )
        
        # 5. Расчёт риска по сделке
        risk_multiplier = self._get_risk_multiplier()
        max_risk_per_trade = (
            self._current_equity
            * self.config.risk_per_trade
            * risk_multiplier
        )
        
        # 6. Расчёт фактического риска
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason="Invalid stop loss distance",
            )
        
        actual_risk = proposed_size * stop_distance
        
        if actual_risk > max_risk_per_trade:
            # Можно уменьшить размер
            adjusted_size = max_risk_per_trade / stop_distance
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason=f"Risk {actual_risk:.2f} exceeds max {max_risk_per_trade:.2f}. Adjusted size: {adjusted_size:.4f}",
                details={"adjusted_size": adjusted_size},
            )
        
        # 7. Проверка максимального количества позиций
        if len(self._open_positions) >= self.config.max_open_positions:
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason=f"Max positions reached: {len(self._open_positions)}",
            )
        
        # 8. Проверка экспозиции
        current_exposure = sum(
            abs(p.quantity * p.entry_price)
            for p in self._open_positions.values()
        )
        new_exposure = current_exposure + proposed_size * entry_price
        max_exposure = self._current_equity * self.config.max_exposure_pct
        
        if new_exposure > max_exposure:
            allowed_exposure = max_exposure - current_exposure
            if allowed_exposure <= 0:
                return RiskCheckResult(
                    approved=False,
                    risk_state=self.risk_state.value,
                    daily_loss_used=daily_loss,
                    weekly_loss_used=weekly_loss,
                    current_drawdown=self.current_drawdown,
                    max_allowed_loss=max_risk_per_trade,
                    reason=f"Max exposure reached: {current_exposure:.2f} / {max_exposure:.2f}",
                )
            
            adjusted_size = allowed_exposure / entry_price
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason=f"Exposure limit would be exceeded. Adjusted size: {adjusted_size:.4f}",
                details={"adjusted_size": adjusted_size},
            )
        
        # Все проверки пройдены
        return RiskCheckResult(
            approved=True,
            risk_state=self.risk_state.value,
            daily_loss_used=daily_loss,
            weekly_loss_used=weekly_loss,
            current_drawdown=self.current_drawdown,
            max_allowed_loss=max_risk_per_trade,
            reason=None,
        )
    
    def calculate_position_size(
        self,
        symbol: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        instrument: Optional[Instrument] = None,
    ) -> PositionSizeResult:
        """
        Рассчитать оптимальный размер позиции.
        
        Возвращает размер позиции или причину отказа.
        """
        # Расчёт допустимого риска
        risk_multiplier = self._get_risk_multiplier()
        allowed_risk = (
            self._current_equity
            * self.config.risk_per_trade
            * risk_multiplier
        )
        
        # Расчёт расстояния до стопа
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return PositionSizeResult(
                accepted=False,
                reason="Stop loss distance must be positive",
            )
        
        # Расчёт теоретического размера
        theoretical_size = allowed_risk / stop_distance
        
        # Проверка минимальных требований биржи
        if instrument:
            min_notional = instrument.min_notional
            min_qty = instrument.min_quantity
            
            # Проверка минимального номинала
            notional = theoretical_size * entry_price
            if notional < min_notional:
                return PositionSizeResult(
                    accepted=False,
                    reason=(
                        f"Position notional {notional:.2f} "
                        f"below minimum {min_notional:.2f}"
                    ),
                )
            
            # Проверка минимального количества
            if theoretical_size < min_qty:
                return PositionSizeResult(
                    accepted=False,
                    reason=(
                        f"Position size {theoretical_size:.6f} "
                        f"below minimum quantity {min_qty:.6f}"
                    ),
                )
        
        # Проверка максимального количества позиций
        if len(self._open_positions) >= self.config.max_open_positions:
            return PositionSizeResult(
                accepted=False,
                reason=f"Max positions reached: {len(self._open_positions)}",
            )
        
        # Проверка экспозиции
        current_exposure = sum(
            abs(p.quantity * p.entry_price)
            for p in self._open_positions.values()
        )
        new_exposure = current_exposure + theoretical_size * entry_price
        max_exposure = self._current_equity * self.config.max_exposure_pct
        
        if new_exposure > max_exposure:
            allowed_exposure = max_exposure - current_exposure
            if allowed_exposure <= 0:
                return PositionSizeResult(
                    accepted=False,
                    reason="Maximum exposure reached",
                )
            adjusted_size = allowed_exposure / entry_price
            return PositionSizeResult(
                accepted=False,
                reason="Exposure limit exceeded",
                adjusted_size=adjusted_size,
            )
        
        return PositionSizeResult(
            accepted=True,
            quantity=theoretical_size,
            risk_amount=allowed_risk,
            risk_state=self.risk_state.value,
            stop_distance=stop_distance,
        )
    
    def record_trade(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        quantity: Decimal,
        pnl: Decimal,
        won: bool,
    ):
        """Записать сделку"""
        trade_data = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "quantity": quantity,
            "pnl": pnl,
            "won": won,
            "timestamp": datetime.utcnow(),
        }
        
        # Обновление статистики
        self._total_trades += 1
        if won:
            self._total_wins += 1
        else:
            self._total_losses += 1
        
        # Обновление P&L
        self._daily_pnl += pnl
        self._weekly_pnl += pnl
        
        # Обновление капитала
        self._current_equity += pnl
        self._update_high_water_mark()
        
        # Проверка режимов
        self._check_drawdown_state()
        
        # Добавление в историю
        self._today_trades.append(trade_data)
        self._week_trades.append(trade_data)
        
        # Очистка старых записей
        self._cleanup_old_trades()
        
        logger.info(
            f"Trade recorded: {symbol} {side} "
            f"PnL={pnl:.2f}, Won={won}, "
            f"Total PnL={self._daily_pnl:.2f}"
        )
    
    def _check_drawdown_state(self):
        """Проверить состояние просадки"""
        dd = self.current_drawdown
        config = self.config
        
        if dd >= float(config.emergency_drawdown) * 100:
            self.risk_state = RiskState.EMERGENCY
            self.trading_enabled = False
            logger.critical(f"EMERGENCY STOP: Drawdown {dd:.2f}%")
            
            events.emit_async(events.EventType.EMERGENCY_STOP, {
                "reason": f"Emergency drawdown: {dd:.2f}%",
                "drawdown": str(dd),
                "risk_state": RiskState.EMERGENCY.value,
            })
        
        elif dd >= float(config.hard_drawdown) * 100:
            self.risk_state = RiskState.STOP
            self.trading_enabled = False
            logger.warning(f"STOP: Drawdown {dd:.2f}%")
            
            events.emit_async(events.EventType.RISK_LIMIT_HIT, {
                "event_type": "DRAWDOWN_THRESHOLD",
                "drawdown": str(dd),
                "limit": str(config.hard_drawdown),
                "action": "TRADING_STOPPED",
            })
        
        elif dd >= float(config.soft_drawdown) * 100:
            self.risk_state = RiskState.REDUCED
            logger.warning(f"Reduced risk: Drawdown {dd:.2f}%")
        
        else:
            if self.risk_state in [RiskState.REDUCED, RiskState.DEFENSIVE]:
                if dd < float(config.soft_drawdown) * 50:
                    self.risk_state = RiskState.NORMAL
                    logger.info("Risk state normalized")
    
    def _cleanup_old_trades(self):
        """Очистить старые записи сделок"""
        now = datetime.utcnow()
        
        # Дневные сделки — оставляем за последние 24 часа
        day_ago = now - timedelta(hours=24)
        self._today_trades = [
            t for t in self._today_trades
            if t["timestamp"] > day_ago
        ]
        
        # Недельные сделки — оставляем за последние 7 дней
        week_ago = now - timedelta(days=7)
        self._week_trades = [
            t for t in self._week_trades
            if t["timestamp"] > week_ago
        ]
    
    def add_position(self, position: models.Position):
        """Добавить позицию"""
        self._open_positions[position.id] = position
    
    def remove_position(self, position_id: str):
        """Удалить позицию"""
        self._open_positions.pop(position_id, None)
    
    def get_open_positions(self) -> Dict[str, models.Position]:
        """Получить открытые позиции"""
        return self._open_positions.copy()
    
    def update_position_price(self, position_id: str, current_price: Decimal):
        """Обновить цену позиции"""
        if position_id in self._open_positions:
            pos = self._open_positions[position_id]
            pos.current_price = current_price
            if side == models.Side.LONG:
                pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
            else:
                pos.unrealized_pnl = (pos.entry_price - current_price) * pos.quantity
    
    def to_dict(self) -> dict:
        """Сериализовать состояние"""
        return {
            "risk_state": self.risk_state.value,
            "trading_enabled": self.trading_enabled,
            "current_equity": str(self._current_equity),
            "initial_capital": str(self._initial_capital),
            "high_water_mark": str(self._high_water_mark),
            "current_drawdown": str(self.current_drawdown),
            "daily_pnl": str(self._daily_pnl),
            "daily_loss_pct": str(self.daily_loss_pct),
            "weekly_pnl": str(self._weekly_pnl),
            "risk_multiplier": str(self._get_risk_multiplier()),
            "open_positions": len(self._open_positions),
            "total_trades": self._total_trades,
            "total_wins": self._total_wins,
            "total_losses": self._total_losses,
            "win_rate": (
                self._total_wins / self._total_trades * 100
                if self._total_trades > 0 else 0
            ),
        }


# Глобальный Risk Engine
_risk_engine: Optional[RiskEngine] = None


def get_risk_engine() -> RiskEngine:
    """Получить глобальный Risk Engine"""
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskEngine()
    return _risk_engine


def reset_risk_engine():
    """Сбросить Risk Engine (для тестов)"""
    global _risk_engine
    _risk_engine = RiskEngine()
