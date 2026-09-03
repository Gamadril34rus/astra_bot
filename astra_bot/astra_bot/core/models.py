"""
ASTRA BOT — Domain модели
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class Side(Enum):
    """Сторона сделки"""
    BUY = "buy"
    SELL = "sell"
    LONG = "long"
    SHORT = "short"


class OrderType(Enum):
    """Тип ордера"""
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS_LIMIT = "stop_loss_limit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    STOP_MARKET = "stop_market"
    TAKE_PROFIT_MARKET = "take_profit_market"


class OrderStatus(Enum):
    """Статус ордера"""
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    TRIGGERED = "triggered"


class PositionStatus(Enum):
    """Статус позиции"""
    OPEN = "open"
    CLOSED = "closed"
    PARTIALLY_CLOSED = "partially_closed"


class TradeDirection(Enum):
    """Направление сделки"""
    LONG = "long"
    SHORT = "short"


class LiquidityLevel(Enum):
    """Уровень ликвидности"""
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"
    CRITICAL = "CRITICAL"


class MarketRegime(Enum):
    """Режим рынка"""
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    PANIC = "PANIC"
    UNKNOWN = "UNKNOWN"


@dataclass
class Instrument:
    """Инструмент торговли"""
    exchange: str
    symbol: str
    base_asset: str
    quote_asset: str
    min_quantity: Decimal
    min_notional: Decimal
    step_size: Decimal
    tick_size: Decimal
    price_precision: int
    quantity_precision: int
    trading_status: str = "trading"
    fee_rate: Decimal = Decimal("0.001")  # 0.1% по умолчанию
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def contract_size(self) -> Decimal:
        """Размер контракта (для фьючерсов)"""
        return Decimal("1")

    def is_valid_quantity(self, quantity: Decimal) -> bool:
        """Проверить валидность количества"""
        if quantity < self.min_quantity:
            return False
        # Проверка кратности шагу
        remainder = quantity % self.step_size
        if remainder > Decimal("0") and remainder > self.step_size * Decimal("0.01"):
            return False
        return True

    def is_valid_price(self, price: Decimal) -> bool:
        """Проверить валидность цены"""
        if price <= 0:
            return False
        # Проверка кратности тик-сайзу
        remainder = price % self.tick_size
        if remainder > Decimal("0") and remainder > self.tick_size * Decimal("0.01"):
            return False
        return True

    def format_quantity(self, quantity: Decimal) -> Decimal:
        """Отформатировать количество согласно precision"""
        quantizer = Decimal(10) ** -self.quantity_precision
        return quantity.quantize(quantizer, rounding=ROUND_DOWN)

    def format_price(self, price: Decimal) -> Decimal:
        """Отформатировать цену согласно precision"""
        quantizer = Decimal(10) ** -self.price_precision
        return price.quantize(quantizer, rounding=ROUND_DOWN)


@dataclass
class Candle:
    """Свеча OHLCV"""
    exchange: str
    symbol: str
    timeframe: str
    open_time: int  # Unix timestamp
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades_count: int = 0
    taker_buy_base_volume: Decimal = Decimal("0")
    taker_buy_quote_volume: Decimal = Decimal("0")
    close_time: int = field(init=False)

    def __post_init__(self):
        # close_time = open_time + timeframe_duration
        timeframe_minutes = {
            "1m": 1, "5m": 5, "15m": 15,
            "1h": 60, "4h": 240, "1d": 1440
        }
        minutes = timeframe_minutes.get(self.timeframe, 1)
        self.close_time = self.open_time + minutes * 60

    @property
    def range(self) -> Decimal:
        """Диапазон свечи"""
        return self.high - self.low

    @property
    def body(self) -> Decimal:
        """Тело свечи"""
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def change_pct(self) -> Decimal:
        """Изменение в процентах"""
        if self.open <= 0:
            return Decimal("0")
        return (self.close - self.open) / self.open * Decimal("100")

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": self.open_time,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "quote_volume": str(self.quote_volume),
        }


@dataclass
class Trade:
    """Торговая сделка"""
    trade_id: str
    exchange: str
    symbol: str
    price: Decimal
    quantity: Decimal
    side: Side
    timestamp: int
    is_taker: bool = False
    fee: Decimal = Decimal("0")

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity

    @property
    def value_usdt(self) -> Decimal:
        return self.notional


@dataclass
class OrderBookEntry:
    """Запись стакана заявок"""
    price: Decimal
    quantity: Decimal

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity


@dataclass
class OrderBook:
    """Стакан заявок"""
    symbol: str
    exchange: str
    bids: list[OrderBookEntry] = field(default_factory=list)
    asks: list[OrderBookEntry] = field(default_factory=list)
    timestamp: int = field(default_factory=lambda: int(datetime.utcnow().timestamp() * 1000))
    sequence: int | None = None

    @property
    def best_bid(self) -> Decimal | None:
        if not self.bids:
            return None
        return self.bids[0].price

    @property
    def best_ask(self) -> Decimal | None:
        if not self.asks:
            return None
        return self.asks[0].price

    @property
    def spread(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid

    @property
    def spread_pct(self) -> Decimal | None:
        spread = self.spread
        mid = self.mid_price
        if spread is None or mid is None or mid <= 0:
            return None
        return spread / mid * Decimal("100")

    @property
    def mid_price(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / Decimal("2")

    def get_depth(self, side: str, levels: int = 5) -> Decimal:
        """Получить глубину по указанной стороне"""
        book = self.bids if side == "buy" else self.asks
        return sum(entry.quantity for entry in book[:levels])

    def get_imbalance(self, depth: int = 10) -> Decimal:
        """Рассчитать дисбаланс стакана"""
        bid_volume = sum(e.quantity for e in self.bids[:depth])
        ask_volume = sum(e.quantity for e in self.asks[:depth])
        total = bid_volume + ask_volume
        if total <= 0:
            return Decimal("0")
        return (bid_volume - ask_volume) / total


@dataclass
class Ticker:
    """Тикер"""
    symbol: str
    exchange: str
    last_price: Decimal
    bid_price: Decimal
    ask_price: Decimal
    high_24h: Decimal
    low_24h: Decimal
    volume_24h: Decimal
    quote_volume_24h: Decimal
    price_change_24h: Decimal
    price_change_pct_24h: Decimal
    timestamp: int = field(default_factory=lambda: int(datetime.utcnow().timestamp() * 1000))

    @property
    def spread(self) -> Decimal:
        return self.ask_price - self.bid_price

    @property
    def spread_pct(self) -> Decimal:
        if self.last_price <= 0:
            return Decimal("0")
        return self.spread / self.last_price * Decimal("100")


@dataclass
class AccountBalance:
    """Баланс аккаунта"""
    account_id: str
    exchange: str
    asset: str
    free: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    usdt_equivalent: Decimal = Decimal("0")
    last_update: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_available(self) -> bool:
        return self.free > 0


@dataclass
class Fill:
    """Исполнение ордера"""
    fill_id: str
    order_id: str
    exchange: str
    symbol: str
    side: Side
    price: Decimal
    quantity: Decimal
    fee: Decimal
    fee_asset: str
    timestamp: int
    is_maker: bool = False

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity

    @property
    def net_notional(self) -> Decimal:
        return self.notional + self.fee if self.side == Side.BUY else self.notional - self.fee


@dataclass
class Position:
    """Позиция"""
    id: UUID = field(default_factory=uuid4)
    account_id: str = ""
    symbol: str = ""
    side: Side = Side.LONG
    quantity: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    status: PositionStatus = PositionStatus.OPEN
    strategy_name: str = ""
    signal_id: UUID | None = None
    open_order_id: str | None = None
    open_time: datetime = field(default_factory=datetime.utcnow)
    close_time: datetime | None = None

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.current_price

    @property
    def entry_value(self) -> Decimal:
        return self.quantity * self.entry_price

    def update_price(self, current_price: Decimal):
        """Обновить цену позиции"""
        self.current_price = current_price
        pnl_per_unit = current_price - self.entry_price if self.side == Side.LONG else self.entry_price - current_price
        self.unrealized_pnl = pnl_per_unit * self.quantity


@dataclass
class Signal:
    """Торговый сигнал"""
    id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    strategy_name: str = ""
    signal_type: str = "momentum"  # momentum, mean_reversion, grid, arbitrage
    direction: TradeDirection = TradeDirection.LONG
    entry_price: Decimal = Decimal("0")
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    position_size: Decimal = Decimal("0")
    risk_amount: Decimal = Decimal("0")
    confidence: float = 0.0
    ml_probability: float | None = None
    expected_value: float | None = None
    market_regime: str = "UNKNOWN"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    status: str = "pending"  # pending, approved, rejected, executed
    rejection_reason: str | None = None
    features: dict[str, float] = field(default_factory=dict)

    @property
    def risk_reward_ratio(self) -> float:
        risk = abs(float(self.entry_price - self.stop_loss))
        reward = abs(float(self.take_profit - self.entry_price))
        if risk <= 0:
            return 0.0
        return reward / risk

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "symbol": self.symbol,
            "strategy": self.strategy_name,
            "direction": self.direction.value,
            "entry": str(self.entry_price),
            "stop_loss": str(self.stop_loss),
            "take_profit": str(self.take_profit),
            "size": str(self.position_size),
            "risk": str(self.risk_amount),
            "confidence": self.confidence,
            "ml_prob": self.ml_probability,
            "ev": self.expected_value,
            "regime": self.market_regime,
            "status": self.status,
        }


@dataclass
class RiskEvent:
    """Событие риска"""
    id: UUID = field(default_factory=uuid4)
    event_type: str = ""
    severity: str = "info"  # info, warning, critical, emergency
    description: str = ""
    current_value: Decimal | None = None
    limit_value: Decimal | None = None
    action_taken: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "type": self.event_type,
            "severity": self.severity,
            "description": self.description,
            "current_value": str(self.current_value) if self.current_value else None,
            "limit_value": str(self.limit_value) if self.limit_value else None,
            "action_taken": self.action_taken,
        }


@dataclass
class NewsEvent:
    """Новостное событие"""
    id: UUID = field(default_factory=uuid4)
    title: str = ""
    summary: str = ""
    source: str = ""
    source_reliability: float = 0.5  # 0-1
    assets: list[str] = field(default_factory=list)
    severity: str = "medium"  # low, medium, high, critical
    confidence: float = 0.5  # 0-1
    event_type: str = "general"  # macro, regulatory, security, listing, unlock, etc.
    duration_minutes: int = 60
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(init=False)
    decay_factor: float = 1.0

    def __post_init__(self):
        self.expires_at = self.created_at + timedelta(minutes=self.duration_minutes)

    def update_decay(self, elapsed_minutes: int):
        """Обновить decay factor"""
        if self.duration_minutes <= 0:
            self.decay_factor = 0
        else:
            decay_ratio = elapsed_minutes / self.duration_minutes
            self.decay_factor = max(0, 1 - decay_ratio)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def effective_confidence(self) -> float:
        return self.confidence * self.decay_factor


@dataclass
class MLPrediction:
    """Предсказание ML модели"""
    id: UUID = field(default_factory=uuid4)
    model_version: str = ""
    symbol: str = ""
    feature_hash: str = ""
    prediction: float = 0.5  # Вероятность
    probability: float = 0.0  # Уверенность модели
    confidence: float = 0.0  # Общий confidence
    features: dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_profitable_signal(self) -> bool:
        return self.prediction > 0.5


@dataclass
class StrategyMetrics:
    """Метрики стратегии"""
    strategy_name: str = ""
    period_start: datetime = field(default_factory=datetime.utcnow)
    period_end: datetime = field(default_factory=datetime.utcnow)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    net_profit: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    exposure_hours: float = 0.0
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    largest_win: Decimal = Decimal("0")
    largest_loss: Decimal = Decimal("0")

    @property
    def is_healthy(self) -> bool:
        return self.profit_factor > 1.0 and self.win_rate > 45.0

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "period": f"{self.period_start.isoformat()} - {self.period_end.isoformat()}",
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": f"{self.win_rate:.2f}%",
            "profit_factor": f"{self.profit_factor:.2f}",
            "net_profit": str(self.net_profit),
            "max_drawdown": str(self.max_drawdown),
            "exposure_hours": f"{self.exposure_hours:.1f}",
        }
