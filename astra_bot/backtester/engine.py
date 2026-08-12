"""
ASTRA BOT — Backtest Engine
Event-driven бэктестер
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from ..core import models
from ..core.utils import (
    round_to_precision,
)
from ..engines.risk_engine import RiskConfig, RiskEngine
from ..strategies import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Конфигурация бэктеста"""
    # Параметры тестирования
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    start_date: datetime = field(default_factory=lambda: datetime(2024, 1, 1))
    end_date: datetime = field(default_factory=lambda: datetime(2024, 12, 31))

    # Капитал
    initial_capital: Decimal = Decimal("1000")

    # Комиссии
    maker_fee_rate: Decimal = Decimal("0.001")  # 0.1%
    taker_fee_rate: Decimal = Decimal("0.001")  # 0.1%

    # Slippage
    slippage_percent: Decimal = Decimal("0.001")  # 0.1%

    # Минимальный ордер
    min_notional: Decimal = Decimal("10")
    min_quantity: Decimal = Decimal("0.0001")

    # Стратегии
    strategies: dict[str, dict] = field(default_factory=dict)

    # Риск
    risk_config: dict = field(default_factory=dict)

    # Ограничения
    max_open_positions: int = 5
    max_exposure_pct: Decimal = Decimal("0.30")

    # Отчётность
    save_trades: bool = True
    save_equity_curve: bool = True
    trades_output_path: str | None = None
    equity_output_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": str(self.initial_capital),
            "maker_fee_rate": str(self.maker_fee_rate),
            "taker_fee_rate": str(self.taker_fee_rate),
            "slippage_percent": str(self.slippage_percent),
            "min_notional": str(self.min_notional),
            "min_quantity": str(self.min_quantity),
            "max_open_positions": self.max_open_positions,
            "max_exposure_pct": str(self.max_exposure_pct),
        }


@dataclass
class Trade:
    """Сделка в бэктесте"""
    id: int
    entry_time: datetime
    entry_price: Decimal
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    side: str = "long"  # long, short
    quantity: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    pnl_pct: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    result: str = "open"  # open, won, lost, pending
    strategy_name: str = ""
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    exit_reason: str = ""

    @property
    def is_open(self) -> bool:
        return self.result == "open"


@dataclass
class DailyStats:
    """Статистика за день"""
    date: datetime
    start_equity: Decimal
    end_equity: Decimal
    pnl: Decimal = Decimal("0")
    trades: int = 0
    wins: int = 0
    losses: int = 0
    commissions: Decimal = Decimal("0")
    exposure_hours: float = 0.0


@dataclass
class BacktestResult:
    """Результат бэктеста"""
    config: BacktestConfig
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    total_slippage: Decimal = Decimal("0")
    net_profit: Decimal = Decimal("0")
    profit_factor: float = 0.0
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    largest_win: Decimal = Decimal("0")
    largest_loss: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_pct: Decimal = Decimal("0")
    final_equity: Decimal = Decimal("0")
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    total_return: Decimal = Decimal("0")
    return_pct: Decimal = Decimal("0")
    trades: list[Trade] = field(default_factory=list)
    daily_stats: list[DailyStats] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    start_date: datetime = field(default_factory=datetime.utcnow)
    end_date: datetime = field(default_factory=datetime.utcnow)
    duration_seconds: float = 0.0

    @property
    def is_profitable(self) -> bool:
        return self.net_profit > 0

    @property
    def expectancy(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return float(self.net_profit / self.total_trades)

    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": f"{self.win_rate:.2f}%",
            "total_pnl": str(self.total_pnl),
            "net_profit": str(self.net_profit),
            "profit_factor": f"{self.profit_factor:.2f}",
            "avg_win": str(self.avg_win),
            "avg_loss": str(self.avg_loss),
            "largest_win": str(self.largest_win),
            "largest_loss": str(self.largest_loss),
            "max_drawdown": str(self.max_drawdown),
            "max_drawdown_pct": f"{self.max_drawdown_pct:.2f}%",
            "final_equity": str(self.final_equity),
            "total_return": str(self.total_return),
            "return_pct": f"{self.return_pct:.2f}%",
            "sharpe_ratio": f"{self.sharpe_ratio:.2f}",
            "expectancy": f"{self.expectancy:.4f}",
            "duration": f"{self.duration_seconds:.1f}s",
        }


class BacktestEngine:
    """
    Event-driven бэктестер для тестирования торговых стратегий.

    Поддерживает:
    - Несколько стратегий одновременно
    - Реалистичные комиссии и slippage
    - Частичные закрытия
    - Ограничения биржи (min order, precision)
    - Подробная статистика
    """

    def __init__(self, config: BacktestConfig):
        self.config = config

        # Состояние. ``_realized_equity`` меняется только при закрытии позиций
        # (учитывая комиссии/проскальзывание), а ``_equity`` на каждом тике
        # пересчитывается как realized + текущий unrealized PnL.
        self._realized_equity = config.initial_capital
        self._equity = config.initial_capital
        self._high_water_mark = config.initial_capital
        self._current_drawdown = Decimal("0")
        self._max_drawdown = Decimal("0")

        # Позиции
        self._open_positions: dict[str, Trade] = {}
        self._position_id_counter = 0

        # Торговые действия
        self._trades: list[Trade] = []
        self._trade_id_counter = 0

        # Статистика
        self._daily_stats: dict[str, DailyStats] = {}

        # Equity curve
        self._equity_curve: list[dict] = []

        # Стратегии
        self._strategies: dict[str, BaseStrategy] = {}

        # Risk engine
        self._risk_engine = RiskEngine(
            RiskConfig(
                risk_per_trade=Decimal(str(config.risk_config.get("risk_per_trade", "0.004"))),
                max_open_positions=config.max_open_positions,
            )
        )
        self._risk_engine.set_capital(config.initial_capital, config.initial_capital)

        # Рыночные данные
        self._candles: list[dict] = []
        self._current_idx = 0

        # Логи
        self._logs: list[dict] = []

    def add_strategy(self, name: str, strategy: BaseStrategy):
        """Добавить стратегию"""
        self._strategies[name] = strategy
        logger.info(f"Strategy added: {name}")

    def load_candles(self, candles: list[dict]):
        """Загрузить свечи для бэктеста"""
        self._candles = candles
        self._current_idx = 0
        logger.info(f"Loaded {len(candles)} candles for backtest")

    def load_candles_from_dataframe(self, df: pd.DataFrame):
        """Загрузить свечи из DataFrame"""
        required_columns = ["open_time", "open", "high", "low", "close", "volume"]
        if not all(col in df.columns for col in required_columns):
            raise ValueError(f"DataFrame missing required columns: {required_columns}")

        self._candles = df.to_dict("records")
        self._current_idx = 0
        logger.info(f"Loaded {len(self._candles)} candles from DataFrame")

    def run(self) -> BacktestResult:
        """Запустить бэктест"""
        start_time = time.time()

        if not self._candles:
            raise ValueError("No candles loaded")

        logger.info(f"Starting backtest: {self.config.symbol} "
                    f"{self.config.start_date} to {self.config.end_date}")

        # Инициализация
        self._init_equity_curve()

        # Основной цикл
        for idx in range(len(self._candles)):
            self._current_idx = idx
            self._process_tick()

        # Закрытие всех открытых позиций в конце
        self._close_all_positions()

        # Расчёт результатов
        result = self._calculate_results()
        result.duration_seconds = time.time() - start_time
        result.start_date = self.config.start_date
        result.end_date = self.config.end_date
        result.config = self.config

        logger.info(f"Backtest completed: {self._trade_id_counter} trades, "
                    f"net PnL: {result.net_profit}")

        return result

    def _init_equity_curve(self):
        """Инициализировать equity curve"""
        if self._candles:
            self._equity_curve.append({
                "timestamp": self._candles[0]["open_time"],
                "equity": float(self._equity),
                "drawdown": 0.0,
            })

    def _process_tick(self):
        """Обработать один тик (свечу)"""
        candle = self._candles[self._current_idx]
        timestamp = candle["open_time"]
        current_price = Decimal(str(candle["close"]))

        # Обновление equity (закрытие позиций по текущей цене для расчёта unrealized PnL)
        self._update_unrealized_pnl(current_price)

        # Проверка стратегий
        for strategy_name, strategy in self._strategies.items():
            if not strategy.enabled:
                continue

            # Получение свечей для стратегии
            lookback = getattr(strategy, "get_required_candles", lambda: 100)()
            start_idx = max(0, self._current_idx - lookback)
            candles_slice = self._candles[start_idx:self._current_idx + 1]

            # Конвертация в модельные свечи
            model_candles = self._convert_to_model_candles(candles_slice)

            if len(model_candles) < lookback:
                continue

            # Оценка стратегии
            try:
                signal = strategy.evaluate(
                    symbol=self.config.symbol,
                    candles=model_candles,
                    current_price=float(current_price),
                )

                if signal:
                    self._process_signal(signal, current_price, timestamp)

            except Exception as e:
                logger.warning(f"Strategy {strategy_name} error: {e}")

        # Обновление equity curve
        self._update_equity_curve(timestamp, current_price)

    def _convert_to_model_candles(self, candles: list[dict]) -> list[models.Candle]:
        """Конвертировать dict свечей в модели"""
        result = []
        for c in candles:
            candle = models.Candle(
                exchange="backtest",
                symbol=self.config.symbol,
                timeframe=self.config.timeframe,
                open_time=int(c["open_time"]),
                open=Decimal(str(c["open"])),
                high=Decimal(str(c["high"])),
                low=Decimal(str(c["low"])),
                close=Decimal(str(c["close"])),
                volume=Decimal(str(c["volume"])),
                quote_volume=Decimal(str(c.get("quote_volume", c["volume"]))),
            )
            result.append(candle)
        return result

    def _process_signal(self, signal: models.Signal, current_price: Decimal, timestamp: datetime):
        """Обработать сигнал"""
        if signal.direction == models.TradeDirection.LONG:
            self._open_long_position(signal, current_price, timestamp)
        elif signal.direction == models.TradeDirection.SHORT:
            self._open_short_position(signal, current_price, timestamp)

    def _open_long_position(self, signal: models.Signal, entry_price: Decimal, timestamp: datetime):
        """Открыть LONG позицию"""
        # Расчёт размера
        risk_engine_result = self._risk_engine.calculate_position_size(
            symbol=self.config.symbol,
            entry_price=entry_price,
            stop_loss=signal.stop_loss,
        )

        if not risk_engine_result.accepted:
            logger.debug(f"Position rejected: {risk_engine_result.reason}")
            return

        # Расчёт фактического размера с учётом ограничений биржи
        quantity = self._calculate_quantity(
            risk_engine_result.quantity,
            entry_price,
        )

        if quantity <= 0:
            return

        # Расчёт комиссии
        notional = quantity * entry_price
        fees = notional * self.config.maker_fee_rate

        # Сlippage
        slippage = notional * self.config.slippage_percent
        effective_price = entry_price + slippage / quantity

        # Валидация
        if notional < self.config.min_notional:
            logger.debug(f"Notional {notional} below min {self.config.min_notional}")
            return

        # Открытие позиции
        trade = Trade(
            id=self._trade_id_counter,
            entry_time=timestamp,
            entry_price=effective_price,
            side="long",
            quantity=quantity,
            fees=fees,
            slippage=slippage,
            strategy_name=signal.strategy_name,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            result="open",
        )

        self._trade_id_counter += 1
        self._open_positions[trade.id] = trade
        self._trades.append(trade)

        # Обновление риск-движка
        self._risk_engine.add_position(str(trade.id))

        # Логирование
        logger.debug(f"LONG opened: {trade.id}, qty={quantity}, price={effective_price}")

    def _open_short_position(self, signal: models.Signal, entry_price: Decimal, timestamp: datetime):
        """Открыть SHORT позицию"""
        # Аналогично LONG, но с учётом SHORT логики
        risk_engine_result = self._risk_engine.calculate_position_size(
            symbol=self.config.symbol,
            entry_price=entry_price,
            stop_loss=signal.stop_loss,
        )

        if not risk_engine_result.accepted:
            return

        quantity = self._calculate_quantity(
            risk_engine_result.quantity,
            entry_price,
        )

        if quantity <= 0:
            return

        notional = quantity * entry_price
        fees = notional * self.config.maker_fee_rate
        slippage = notional * self.config.slippage_percent
        effective_price = entry_price - slippage / quantity

        if notional < self.config.min_notional:
            return

        trade = Trade(
            id=self._trade_id_counter,
            entry_time=timestamp,
            entry_price=effective_price,
            side="short",
            quantity=quantity,
            fees=fees,
            slippage=slippage,
            strategy_name=signal.strategy_name,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            result="open",
        )

        self._trade_id_counter += 1
        self._open_positions[trade.id] = trade
        self._trades.append(trade)

        self._risk_engine.add_position(str(trade.id))

        logger.debug(f"SHORT opened: {trade.id}, qty={quantity}, price={effective_price}")

    def _calculate_quantity(self, desired_qty: Decimal, price: Decimal) -> Decimal:
        """Рассчитать допустимое количество"""
        # Округление до количества
        qty = round_to_precision(desired_qty, 6)

        # Проверка минимального количества
        if qty < self.config.min_quantity:
            return Decimal("0")

        # Проверка минимального номинала
        if qty * price < self.config.min_notional:
            return Decimal("0")

        return qty

    def _update_unrealized_pnl(self, current_price: Decimal):
        """Обновить unrealized PnL для открытых позиций"""
        for _trade_id, trade in self._open_positions.items():
            if trade.side == "long":
                unrealized = (current_price - trade.entry_price) * trade.quantity
            else:
                unrealized = (trade.entry_price - current_price) * trade.quantity

            trade.pnl = unrealized - trade.fees
            trade.pnl_pct = (unrealized / (trade.entry_price * trade.quantity)) * 100 if trade.quantity > 0 else 0

    def _close_all_positions(self):
        """Закрыть все открытые позиции"""
        for trade_id, _trade in list(self._open_positions.items()):
            self._close_position(trade_id, "end_of_backtest")

    def _close_position(self, trade_id: int, reason: str):
        """Закрыть позицию"""
        if trade_id not in self._open_positions:
            return

        trade = self._open_positions.pop(trade_id)
        trade.exit_time = datetime.utcnow()
        trade.exit_reason = reason
        trade.result = "won" if trade.pnl > 0 else "lost"

        # Фиксируем реализованный PnL (включая уже учтённые комиссии,
        # которые вычитаются из ``trade.pnl`` в ``_update_unrealized_pnl``).
        self._realized_equity += trade.pnl

        if trade.pnl > 0:
            self._risk_engine.record_trade(
                symbol=self.config.symbol,
                side=trade.side,
                entry_price=trade.entry_price,
                quantity=trade.quantity,
                pnl=trade.pnl,
                won=True,
            )
        else:
            self._risk_engine.record_trade(
                symbol=self.config.symbol,
                side=trade.side,
                entry_price=trade.entry_price,
                quantity=trade.quantity,
                pnl=trade.pnl,
                won=False,
            )

        logger.debug(f"Position closed: {trade_id}, PnL={trade.pnl}, reason={reason}")

    def _check_exit_conditions(self, trade: Trade, current_price: Decimal, timestamp: datetime):
        """Проверить условия выхода"""
        # Стоп-лосс
        if trade.side == "long" and current_price <= trade.stop_loss:
            self._close_position(trade.id, "stop_loss")
            return

        if trade.side == "short" and current_price >= trade.stop_loss:
            self._close_position(trade.id, "stop_loss")
            return

        # Тейк-профит (упрощённо — фиксированный TP)
        if trade.side == "long" and current_price >= trade.take_profit:
            self._close_position(trade.id, "take_profit")
            return

        if trade.side == "short" and current_price <= trade.take_profit:
            self._close_position(trade.id, "take_profit")
            return

    def _update_equity_curve(self, timestamp: int, current_price: Decimal):
        """Обновить equity curve."""
        # Unrealized PnL по открытым позициям.
        unrealized = sum(t.pnl for t in self._open_positions.values())

        # Equity = реализованный капитал + плавающий PnL.
        # Раньше здесь было ``self._equity += unrealized``, что приводило к
        # кумулятивному сложению одного и того же unrealized PnL на каждом
        # тике и экспоненциальному раздуву капитала.
        self._equity = self._realized_equity + unrealized

        # High water mark
        if self._equity > self._high_water_mark:
            self._high_water_mark = self._equity

        # Drawdown
        if self._high_water_mark > 0:
            self._current_drawdown = (self._high_water_mark - self._equity) / self._high_water_mark * 100
            if self._current_drawdown > float(self._max_drawdown):
                self._max_drawdown = self._current_drawdown

    def _calculate_results(self) -> BacktestResult:
        """Рассчитать результаты"""
        trades = [t for t in self._trades if t.result in ["won", "lost"]]

        total_trades = len(trades)
        wins = sum(1 for t in trades if t.result == "won")
        losses = sum(1 for t in trades if t.result == "lost")

        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        total_pnl = sum(t.pnl for t in trades)
        total_fees = sum(t.fees for t in self._trades)
        total_slippage = sum(t.slippage for t in self._trades)
        net_profit = total_pnl - total_fees - total_slippage

        # Profit Factor
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0

        # Average win/loss
        avg_win = (sum(t.pnl for t in trades if t.pnl > 0) / wins) if wins > 0 else 0
        avg_loss = (sum(t.pnl for t in trades if t.pnl < 0) / losses) if losses > 0 else 0

        # Largest
        largest_win = max((t.pnl for t in trades if t.pnl > 0), default=Decimal("0"))
        largest_loss = min((t.pnl for t in trades if t.pnl < 0), default=0)

        # Max Drawdown
        max_drawdown_pct = self._max_drawdown

        # Return
        total_return = self._equity - self.config.initial_capital
        return_pct = (total_return / self.config.initial_capital) * 100 if self.config.initial_capital > 0 else 0

        # Sharpe Ratio (упрощённый)
        if len(self._equity_curve) > 1:
            returns = [float(self._equity_curve[i]["equity"] / self._equity_curve[i-1]["equity"] - 1)
                      for i in range(1, len(self._equity_curve))]
            if returns and np.std(returns) > 0:
                sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)  # Annualized
            else:
                sharpe = 0
        else:
            sharpe = 0

        result = BacktestResult(
            config=self.config,
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            total_pnl=total_pnl,
            total_fees=total_fees,
            total_slippage=total_slippage,
            net_profit=net_profit,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            max_drawdown=Decimal(str(self._max_drawdown * self._high_water_mark / 100)) if self._max_drawdown > 0 else Decimal("0"),
            max_drawdown_pct=Decimal(str(max_drawdown_pct)),
            final_equity=self._equity,
            sharpe_ratio=sharpe,
            total_return=total_return,
            return_pct=return_pct,
            trades=self._trades,
            daily_stats=list(self._daily_stats.values()),
            equity_curve=self._equity_curve,
        )

        return result

    def get_equity_curve(self) -> list[dict]:
        """Получить equity curve"""
        return self._equity_curve

    def get_trades(self) -> list[Trade]:
        """Получить все сделки"""
        return self._trades


# Фабрика конфигурации
def create_default_config(
    symbol: str = "BTC/USDT",
    start_date: datetime = None,
    end_date: datetime = None,
    initial_capital: Decimal = Decimal("1000"),
) -> BacktestConfig:
    """Создать конфигурацию по умолчанию"""
    if start_date is None:
        start_date = datetime(2024, 1, 1)
    if end_date is None:
        end_date = datetime(2024, 12, 31)

    return BacktestConfig(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        strategies={
            "momentum": {"enabled": True},
            "mean_reversion": {"enabled": True},
        },
        risk_config={
            "risk_per_trade": "0.004",
        },
    )
