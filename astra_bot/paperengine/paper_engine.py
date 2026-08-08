"""
ASTRA BOT — Paper Trading Engine
Движок бумажной торговли на реальных рыночных данных
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Callable, Any
from uuid import uuid4

from ..core import models, events
from ..core.state import get_system_state, TradingState, RiskState
from ..core.utils import (
    Decimal,
    round_to_precision,
    generate_client_order_id,
)
from ..adapters.base import ExchangeAdapter
from ..engines.risk_engine import RiskEngine, RiskConfig
from ..engines.execution_engine import ExecutionEngine, ExecutionConfig
from ..strategies.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    """Бумажная сделка"""
    id: str = field(default_factory=lambda: str(uuid4()))
    symbol: str = ""
    side: str = "long"
    strategy_name: str = ""
    entry_time: datetime = field(default_factory=datetime.utcnow)
    entry_price: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    pnl_pct: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    status: str = "open"  # open, closed, pending
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    exit_time: Optional[datetime] = None
    exit_price: Optional[Decimal] = None
    exit_reason: str = ""
    
    @property
    def is_open(self) -> bool:
        return self.status == "open"
    
    @property
    def unrealized_pnl(self) -> Decimal:
        if self.side == "long":
            return (self.current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.current_price) * self.quantity
    
    def update_price(self, price: Decimal):
        """Обновить цену и пересчитать PnL"""
        self.current_price = price
        self.pnl = self.unrealized_pnl - self.fees
        if self.entry_price > 0:
            self.pnl_pct = (self.unrealized_pnl / (self.entry_price * self.quantity)) * 100


@dataclass
class PaperAccount:
    """Бумажный аккаунт"""
    id: str = field(default_factory=lambda: str(uuid4()))
    exchange: str = "paper"
    is_paper: bool = True
    
    # Балансы
    balances: Dict[str, Decimal] = field(default_factory=lambda: {"USDT": Decimal("0"), "BTC": Decimal("0"), "ETH": Decimal("0")})
    usdt_balance: Decimal = Decimal("1000")  # Начальный капитал
    
    # Позиции
    open_positions: Dict[str, PaperTrade] = field(default_factory=dict)
    
    # Статистика
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_pnl: Decimal = Decimal("0")
    equity: Decimal = Decimal("1000")
    initial_capital: Decimal = Decimal("1000")
    high_water_mark: Decimal = Decimal("1000")
    
    def __post_init__(self):
        # Обновляем equity и high_water_mark при инициализации
        self.equity = self.usdt_balance
        self.high_water_mark = self.usdt_balance
        self.initial_capital = self.usdt_balance
    
    # Пороговые значения
    daily_pnl: Decimal = Decimal("0")
    weekly_pnl: Decimal = Decimal("0")
    current_drawdown: Decimal = Decimal("0")
    
    def update_equity(self, price: Decimal):
        """Обновить equity на основе unrealized PnL"""
        unrealized = sum(t.unrealized_pnl for t in self.open_positions.values())
        self.equity = self.usdt_balance + unrealized
        
        # High water mark
        if self.equity > self.high_water_mark:
            self.high_water_mark = self.equity
        
        # Drawdown
        if self.high_water_mark > 0:
            self.current_drawdown = (self.high_water_mark - self.equity) / self.high_water_mark * 100


class PaperTradingEngine:
    """
    Движок бумажной торговли.
    
    Работает на реальных рыночных данных, но без реальных ордеров.
    Используется для:
    - Тестирования стратегий на свежих данных
    - Валидации execution logic
    - Минимум 30 дней перед real live
    """
    
    def __init__(
        self,
        initial_capital: Decimal = Decimal("1000"),
        strategies: Dict[str, BaseStrategy] = None,
        risk_config: RiskConfig = None,
        execution_config: ExecutionConfig = None,
    ):
        self.initial_capital = initial_capital
        self.account = PaperAccount(usdt_balance=initial_capital)
        
        self._strategies = strategies or {}
        self._risk_engine = RiskEngine(risk_config or RiskConfig())
        self._risk_engine.set_capital(initial_capital, initial_capital)
        
        # Execution engine для управления "ордерами"
        self._execution_engine = ExecutionEngine(execution_config or ExecutionConfig())
        
        # Callback'и
        self._on_trade_opened: List[Callable] = []
        self._on_trade_closed: List[Callable] = []
        self._on_signal: List[Callable] = []
        
        # Логи
        self._logs: List[Dict] = []
        
        # Таймер для обновления
        self._running = False
        self._update_task: Optional[asyncio.Task] = None
    
    def add_strategy(self, name: str, strategy: BaseStrategy):
        """Добавить стратегию"""
        self._strategies[name] = strategy
        logger.info(f"Paper trading strategy added: {name}")
    
    def register_on_trade_opened(self, callback: Callable):
        """Зарегистрировать callback на открытие сделки"""
        self._on_trade_opened.append(callback)
    
    def register_on_trade_closed(self, callback: Callable):
        """Зарегистрировать callback на закрытие сделки"""
        self._on_trade_closed.append(callback)
    
    def register_on_signal(self, callback: Callable):
        """Зарегистрировать callback на сигнал"""
        self._on_signal.append(callback)
    
    async def start(self, update_interval_seconds: int = 1):
        """Запустить бумажную торговлю"""
        self._running = True
        logger.info(f"Paper trading started with capital: {self.initial_capital}")
        
        self._update_task = asyncio.create_task(
            self._run_loop(update_interval_seconds)
        )
    
    async def stop(self):
        """Остановить бумажную торговлю"""
        self._running = False
        
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Paper trading stopped")
    
    async def _run_loop(self, interval: int):
        """Основной цикл"""
        while self._running:
            try:
                await asyncio.sleep(interval)
                # NOTE: В реальной реализации здесь была бы подписка на WebSocket
                # и обработка новых тиков
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Paper trading loop error: {e}")
    
    async def process_market_data(
        self,
        symbol: str,
        current_price: Decimal,
        candles: List[models.Candle] = None,
    ):
        """
        Обработать новые рыночные данные.
        
        Этот метод вызывается при получении новых данных из WebSocket.
        """
        # Обновить цены открытых позиций
        for trade in self.account.open_positions.values():
            if trade.symbol == symbol:
                trade.update_price(current_price)
        
        # Обновить equity
        self.account.update_equity(current_price)
        
        # Обновить risk engine
        self._risk_engine.set_capital(self.account.equity, self.initial_capital)
        
        # Проверить стратегии
        for strategy_name, strategy in self._strategies.items():
            if not strategy.enabled:
                continue
            
            # Получить свечи для стратегии
            if candles is None:
                continue
            
            try:
                signal = await strategy.evaluate(
                    symbol=symbol,
                    candles=candles,
                    current_price=float(current_price),
                )
                
                if signal:
                    await self._process_signal(signal, current_price)
            
            except Exception as e:
                logger.warning(f"Strategy {strategy_name} error: {e}")
    
    async def _process_signal(self, signal: models.Signal, current_price: Decimal):
        """Обработать сигнал"""
        # Уведомить callback'и
        for callback in self._on_signal:
            try:
                await callback(signal)
            except Exception as e:
                logger.error(f"Signal callback error: {e}")
        
        # Проверить риск
        risk_result = self._risk_engine.check_trade(
            symbol=signal.symbol,
            side=signal.direction.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            proposed_size=signal.position_size,
            strategy_name=signal.strategy_name,
        )
        
        if not risk_result.approved:
            logger.debug(f"Signal rejected by risk engine: {risk_result.reason}")
            return
        
        # Открыть позицию
        await self._open_position(signal, current_price)
    
    async def _open_position(self, signal: models.Signal, current_price: Decimal):
        """Открыть позицию"""
        # Расчёт размера
        self._risk_engine.calculate_position_size(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
        )
        
        # Создать сделку
        trade = PaperTrade(
            symbol=signal.symbol,
            side=signal.direction.value,
            strategy_name=signal.strategy_name,
            entry_price=signal.entry_price,
            current_price=current_price,
            quantity=signal.position_size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            fees=Decimal("0"),  # Без комиссий в paper
            status="open",
        )
        
        # Добавить в аккаунт
        self.account.open_positions[trade.id] = trade
        
        # Статистика
        self.account.total_trades += 1
        
        logger.info(
            f"Paper position opened: {trade.id}, "
            f"symbol={signal.symbol}, side={trade.side}, "
            f"price={current_price}, size={trade.quantity}"
        )
        
        # Уведомить callback'и
        for callback in self._on_trade_opened:
            try:
                callback(trade)
            except Exception as e:
                logger.error(f"Trade opened callback error: {e}")
        
        # Уведомить через events
        await events.publish_async(events.EventType.ORDER_PLACED, {
            "trade_id": trade.id,
            "symbol": signal.symbol,
            "side": trade.side,
            "price": str(current_price),
            "quantity": str(trade.quantity),
        })
    
    async def close_position(self, trade_id: str, reason: str = "manual"):
        """Закрыть позицию"""
        if trade_id not in self.account.open_positions:
            return
        
        trade = self.account.open_positions.pop(trade_id)
        trade.status = "closed"
        trade.exit_time = datetime.utcnow()
        trade.exit_reason = reason
        
        # Обновить статистику
        if trade.pnl > 0:
            self.account.total_wins += 1
        else:
            self.account.total_losses += 1
        
        self.account.total_pnl += trade.pnl
        self.account.daily_pnl += trade.pnl
        self.account.weekly_pnl += trade.pnl
        
        # Обновить USDT баланс (в paper Trading просто обновляем equity)
        # В реальной торговле сюда пришло бы обновление баланса
        
        logger.info(
            f"Paper position closed: {trade_id}, "
            f"pnl={trade.pnl}, reason={reason}"
        )
        
        # Уведомить callback'и
        for callback in self._on_trade_closed:
            try:
                callback(trade)
            except Exception as e:
                logger.error(f"Trade closed callback error: {e}")
        
        # Уведомить через events
        await events.publish_async(events.EventType.ORDER_FILLED, {
            "trade_id": trade_id,
            "symbol": trade.symbol,
            "pnl": str(trade.pnl),
            "reason": reason,
        })
    
    def get_account_info(self) -> Dict:
        """Получить информацию об аккаунте"""
        return {
            "equity": str(self.account.equity),
            "initial_capital": str(self.initial_capital),
            "total_pnl": str(self.account.total_pnl),
            "total_pnl_pct": f"{(float(self.account.total_pnl) / float(self.initial_capital) * 100):.2f}%",
            "current_drawdown": f"{float(self.account.current_drawdown):.2f}%",
            "open_positions": len(self.account.open_positions),
            "total_trades": self.account.total_trades,
            "wins": self.account.total_wins,
            "losses": self.account.total_losses,
            "win_rate": f"{(self.account.total_wins / self.account.total_trades * 100) if self.account.total_trades > 0 else 0:.2f}%",
            "balances": {k: str(v) for k, v in self.account.balances.items()},
        }
    
    def get_positions(self) -> List[PaperTrade]:
        """Получить все открытые позиции"""
        return list(self.account.open_positions.values())
    
    def get_position(self, trade_id: str) -> Optional[PaperTrade]:
        """Получить позицию по ID"""
        return self.account.open_positions.get(trade_id)
    
    @property
    def is_running(self) -> bool:
        return self._running


# Глобальный paper engine
_paper_engine: Optional[PaperTradingEngine] = None


def get_paper_engine() -> PaperTradingEngine:
    """Получить глобальный paper engine"""
    global _paper_engine
    if _paper_engine is None:
        _paper_engine = PaperTradingEngine()
    return _paper_engine


def reset_paper_engine():
    """Сбросить paper engine (для тестов)"""
    global _paper_engine
    _paper_engine = None
