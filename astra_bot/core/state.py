"""
ASTRA BOT — Управление состоянием системы
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .events import EventType, emit

logger = logging.getLogger(__name__)


class RiskState(Enum):
    """Состояние риск-движка"""
    NORMAL = "NORMAL"
    REDUCED = "REDUCED"        # DD 3-5%
    DEFENSIVE = "DEFENSIVE"    # DD 5-8%
    STOP = "STOP"              # DD ≥ 8%
    EMERGENCY = "EMERGENCY"    # DD ≥ 10% или критическое событие


class TradingState(Enum):
    """Состояние торговли"""
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    RECONCILING = "RECONCILING"
    RECOVERY = "RECOVERY"
    CAPITAL_PRESERVATION = "CAPITAL_PRESERVATION"


class SystemHealth(Enum):
    """Здоровье системы"""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"


@dataclass
class PositionInfo:
    """Информация о позиции"""
    symbol: str
    side: str  # long, short
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    strategy_name: str
    order_id: str | None = None
    open_time: datetime = field(default_factory=datetime.utcnow)
    current_value: Decimal = field(init=False)

    def __post_init__(self):
        self.current_value = self.quantity * self.current_price


@dataclass
class OrderInfo:
    """Информация об ордере"""
    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None
    stop_price: Decimal | None
    take_profit: Decimal | None
    status: str
    filled_quantity: Decimal = Decimal("0")
    filled_price: Decimal | None = None
    filled_fees: Decimal = Decimal("0")
    client_order_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    exchange_order_id: str | None = None


@dataclass
class AccountInfo:
    """Информация об аккаунте"""
    account_id: str
    exchange: str
    account_type: str
    is_paper: bool = False
    is_trading_enabled: bool = True

    # Балансы
    balances: dict[str, Decimal] = field(default_factory=dict)
    usdt_balance: Decimal = Decimal("0")

    # Позиции
    positions: dict[str, PositionInfo] = field(default_factory=dict)

    # Ордера
    open_orders: list[OrderInfo] = field(default_factory=list)

    # Статистика
    equity: Decimal = Decimal("0")
    initial_capital: Decimal = Decimal("0")
    high_water_mark: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    total_trades: int = 0
    wins: int = 0
    losses: int = 0

    # Просадка
    current_drawdown: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")

    # Лимиты использованы
    daily_loss_used: Decimal = Decimal("0")
    weekly_loss_used: Decimal = Decimal("0")

    # Лast обновление
    last_update: datetime = field(default_factory=datetime.utcnow)

    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def total_pnl_pct(self) -> Decimal:
        if self.initial_capital <= 0:
            return Decimal("0")
        return self.total_pnl / self.initial_capital * Decimal("100")

    @property
    def exposure(self) -> Decimal:
        """Рыночная экспозиция в USD"""
        total = Decimal("0")
        for pos in self.positions.values():
            total += pos.current_value
        return total

    @property
    def exposure_pct(self) -> Decimal:
        """Экспозиция в % от капитала"""
        if self.equity <= 0:
            return Decimal("0")
        return self.exposure / self.equity * Decimal("100")

    @property
    def available_capital(self) -> Decimal:
        """Доступный капитал для торговли"""
        return self.usdt_balance - self.exposure * Decimal("0.1")  # reserve


@dataclass
class StrategyHealth:
    """Здоровье стратегии"""
    name: str
    is_running: bool = True
    is_killed: bool = False
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    net_profit: float = 0.0
    max_drawdown: float = 0.0
    exposure_hours: float = 0.0
    last_trade_time: datetime | None = None
    decay_detected: bool = False

    @property
    def is_healthy(self) -> bool:
        return not self.is_killed and not self.decay_detected

    def update_from_trade(self, won: bool, pnl: float):
        """Обновить метрики после сделки"""
        self.total_trades += 1
        if won:
            self.wins += 1
            self.net_profit += pnl
        else:
            self.losses += 1
            self.net_profit -= abs(pnl)

        if self.total_trades > 0:
            self.win_rate = self.wins / self.total_trades * 100

        # Простой расчёт profit factor
        if self.losses > 0:
            avg_win = self.net_profit / self.wins if self.wins > 0 else 0
            avg_loss = abs(self.net_profit) / self.losses
            if avg_loss > 0:
                self.profit_factor = avg_win / avg_loss


@dataclass
class ExchangeHealthInfo:
    """Здоровье биржи"""
    exchange: str
    status: str = "HEALTHY"
    api_latency_ms: float = 0.0
    websocket_status: str = "DISCONNECTED"
    rejected_orders_count: int = 0
    execution_quality_score: float = 1.0
    price_anomaly_detected: bool = False
    maintenance_mode: bool = False
    error_rate: float = 0.0
    last_check: datetime = field(default_factory=datetime.utcnow)

    @property
    def health_score(self) -> float:
        """Общий балл здоровья (0-100)"""
        score = 100.0
        score -= self.api_latency_ms / 10
        if self.websocket_status != "CONNECTED":
            score -= 30
        score -= self.rejected_orders_count * 5
        score *= self.execution_quality_score
        if self.price_anomaly_detected:
            score -= 40
        if self.maintenance_mode:
            score = 0
        return max(0, min(100, score))

    @property
    def is_healthy(self) -> bool:
        return self.health_score >= 70 and not self.maintenance_mode


@dataclass
class MarketRegimeInfo:
    """Информация о режиме рынка"""
    symbol: str
    regime: str = "UNKNOWN"
    confidence: float = 0.0
    trend_strength: float = 0.0
    volatility: float = 0.0
    volume_profile: str = "NORMAL"
    detected_at: datetime = field(default_factory=datetime.utcnow)
    indicators: dict[str, float] = field(default_factory=dict)


@dataclass
class SystemState:
    """
    Глобальное состояние системы.
    """

    # Общее состояние
    trading_state: TradingState = TradingState.STARTING
    system_health: SystemHealth = SystemHealth.OFFLINE
    start_time: datetime | None = None
    last_update: datetime = field(default_factory=datetime.utcnow)

    # Risk state
    risk_state: RiskState = RiskState.NORMAL
    risk_state_changed_at: datetime | None = None

    # Капитал
    current_equity: Decimal = Decimal("0")
    initial_capital: Decimal = Decimal("0")
    high_water_mark: Decimal = Decimal("0")

    # Просадка
    current_drawdown: Decimal = Decimal("0")
    max_drawdown_ever: Decimal = Decimal("0")

    # Daily/weekly P&L
    daily_pnl: Decimal = Decimal("0")
    weekly_pnl: Decimal = Decimal("0")

    # Стратегии
    strategies: dict[str, StrategyHealth] = field(default_factory=dict)

    # Биржи
    exchanges: dict[str, ExchangeHealthInfo] = field(default_factory=dict)

    # Режимы рынка
    market_regimes: dict[str, MarketRegimeInfo] = field(default_factory=dict)

    # Заказанные ордера (для отслеживания)
    pending_orders: dict[str, OrderInfo] = field(default_factory=dict)

    # Новые позиции
    positions: dict[str, PositionInfo] = field(default_factory=dict)

    # Signal tracking
    signals_today: int = 0
    signals_accepted: int = 0
    signals_rejected: int = 0

    # ML
    ml_model_version: str | None = None
    ml_enabled: bool = False

    # News
    active_news_events: list[dict] = field(default_factory=list)

    # System errors
    errors_today: int = 0
    critical_errors: int = 0
    error_log: list[dict] = field(default_factory=list)

    # Trading statistics
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_net_pnl: Decimal = Decimal("0")

    @property
    def total_pnl_pct(self) -> Decimal:
        """Совокупный PnL в процентах от начального капитала."""
        if self.initial_capital <= 0:
            return Decimal("0")
        return self.total_net_pnl / self.initial_capital * Decimal("100")

    def initialize(self, initial_capital: Decimal):
        """Инициализация состояния"""
        self.initial_capital = initial_capital
        self.current_equity = initial_capital
        self.high_water_mark = initial_capital
        self.start_time = datetime.utcnow()
        self.trading_state = TradingState.RUNNING
        self.system_health = SystemHealth.HEALTHY
        emit(EventType.SYSTEM_START, {"initial_capital": str(initial_capital)})
        logger.info(f"System initialized with capital: {initial_capital}")

    def update_equity(self, new_equity: Decimal):
        """Обновить капитализацию"""
        old_equity = self.current_equity
        self.current_equity = new_equity

        # Обновить high water mark
        if new_equity > self.high_water_mark:
            self.high_water_mark = new_equity

        # Рассчитать просадку
        if self.high_water_mark > 0:
            self.current_drawdown = (
                (self.high_water_mark - new_equity) / self.high_water_mark * Decimal("100")
            )

        # Обновить максимальную просадку
        if self.current_drawdown > self.max_drawdown_ever:
            self.max_drawdown_ever = self.current_drawdown

        # Проверить drawdown thresholds
        self._check_drawdown_thresholds()

        logger.debug(f"Equity updated: {old_equity} → {new_equity}, "
                    f"Drawdown: {self.current_drawdown:.2f}%")

    def _check_drawdown_thresholds(self):
        """Проверить пороги просадки"""
        dd = float(self.current_drawdown)
        config = get_system_config()

        # Экстренная просадка
        if dd >= float(config.risk.emergency_drawdown):
            if self.risk_state != RiskState.EMERGENCY:
                self._set_risk_state(RiskState.EMERGENCY,
                                     f"Emergency drawdown: {dd:.2f}%")

        # Жёсткая просадка
        elif dd >= float(config.risk.hard_drawdown):
            if self.risk_state not in [RiskState.STOP, RiskState.EMERGENCY]:
                self._set_risk_state(RiskState.STOP,
                                    f"Hard drawdown reached: {dd:.2f}%")

        # Мягкая просадка
        elif dd >= float(config.risk.soft_drawdown):
            if self.risk_state not in [RiskState.REDUCED, RiskState.DEFENSIVE,
                                       RiskState.STOP, RiskState.EMERGENCY]:
                self._set_risk_state(RiskState.REDUCED,
                                    f"Soft drawdown: {dd:.2f}%")

        # Нормальное состояние
        elif dd < float(config.risk.soft_drawdown) * 0.6:
            if self.risk_state not in [RiskState.NORMAL]:
                self._set_risk_state(RiskState.NORMAL, "Drawdown normalized")

    def _set_risk_state(self, new_state: RiskState, reason: str):
        """Установить новое состояние риска"""
        old_state = self.risk_state
        self.risk_state = new_state
        self.risk_state_changed_at = datetime.utcnow()

        logger.warning(f"Risk state changed: {old_state.value} → {new_state.value}: {reason}")

        # Отправить событие
        emit(EventType.RISK_LIMIT_HIT, {
            "old_state": old_state.value,
            "new_state": new_state.value,
            "reason": reason,
            "drawdown": str(self.current_drawdown),
        })

        # Остановить торговлю при STOP/EMERGENCY
        if new_state in [RiskState.STOP, RiskState.EMERGENCY]:
            self.trading_state = TradingState.EMERGENCY_STOP
            emit(EventType.EMERGENCY_STOP, {
                "reason": reason,
                "risk_state": new_state.value,
            })

    def record_trade(self, won: bool, pnl: Decimal,
                    strategy_name: str = "unknown"):
        """Записать сделку"""
        self.total_trades += 1
        self.total_net_pnl += pnl

        if won:
            self.total_wins += 1
        else:
            self.total_losses += 1

        # Обновить стратегию
        if strategy_name in self.strategies:
            self.strategies[strategy_name].update_from_trade(won, float(pnl))

        logger.debug(f"Trade recorded: won={won}, pnl={pnl}, "
                    f"total_trades={self.total_trades}")

    def update_strategy(self, name: str, health: StrategyHealth):
        """Обновить состояние стратегии"""
        self.strategies[name] = health

        # Проверить decay
        if health.decay_detected and not health.is_killed:
            health.is_killed = True
            emit(EventType.STRATEGY_KILLED, {
                "strategy": name,
                "profit_factor": health.profit_factor,
            })
            logger.warning(f"Strategy killed due to decay: {name}")

    def get_risk_multiplier(self) -> float:
        """Получить множитель риска для текущего состояния.

        ``current_drawdown`` хранится в процентах (0-100), а пороги в
        ``drawdown_adaptation`` — в долях (0-1). Проходим по tiers в порядке
        возрастания порога и возвращаем множитель ПОСЛЕДНЕГО пройденного
        порога (а не первого непройденного, как было раньше — из-за чего при
        нулевой просадке множитель ошибочно равнялся 0.75).
        """
        config = get_system_config()
        dd = float(self.current_drawdown)

        multiplier = 1.0
        for tier in config.risk.drawdown_adaptation:
            threshold_pct = float(tier["drawdown"]) * 100.0
            if dd >= threshold_pct:
                multiplier = float(tier["risk_multiplier"])
            else:
                break
        return multiplier

    def to_dict(self) -> dict:
        """Сериализовать состояние"""
        return {
            "trading_state": self.trading_state.value,
            "risk_state": self.risk_state.value,
            "system_health": self.system_health.value,
            "current_equity": str(self.current_equity),
            "initial_capital": str(self.initial_capital),
            "current_drawdown": str(self.current_drawdown),
            "max_drawdown_ever": str(self.max_drawdown_ever),
            "daily_pnl": str(self.daily_pnl),
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "total_net_pnl": str(self.total_net_pnl),
            "strategies": {
                name: {
                    "is_running": s.is_running,
                    "is_killed": s.is_killed,
                    "profit_factor": s.profit_factor,
                    "win_rate": s.win_rate,
                }
                for name, s in self.strategies.items()
            },
            "risk_multiplier": self.get_risk_multiplier(),
            "ml_model_version": self.ml_model_version,
        }


# Глобальное состояние
_system_state: SystemState | None = None


def get_system_state() -> SystemState:
    """Получить глобальное состояние"""
    global _system_state
    if _system_state is None:
        _system_state = SystemState()
    return _system_state


def reset_system_state():
    """Сбросить состояние (для тестов)"""
    global _system_state
    _system_state = SystemState()


def get_system_config():
    """Получить конфигурацию (импорт*lazy)"""
    from .config import get_settings
    return get_settings()
