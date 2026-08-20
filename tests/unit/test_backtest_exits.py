"""Тесты бэктестера: async-стратегии, внутрибарные стоп/тейк, без двойного
учёта комиссий и лимит одной позиции одновременно.

Проверяют именно те пути, которые используются в scripts/backtest_book_2y.py
для проверки «Простой книги торговли» на истории.
"""

from decimal import Decimal

from astra_bot.backtester.engine import BacktestConfig, BacktestEngine
from astra_bot.core import models
from astra_bot.strategies.base import BaseStrategy, Signal, StrategyConfig

# Движок отдаёт стратегии ровно ``lookback`` (по умолчанию 100) свечей,
# поэтому сигналить можно только начиная с бара 99.
SIGNAL_IDX = 99


class OneShotLongStrategy(BaseStrategy):
    """Async-стратегия: один LONG-сигнал при первом же вызове."""

    def __init__(self):
        super().__init__(StrategyConfig(name="one_shot_long"))
        self._fired = False

    async def evaluate(
        self, symbol, candles, orderbook=None, current_price=None, market_regime=None
    ):
        if self._fired:
            return None
        self._fired = True
        price = Decimal(str(current_price or float(candles[-1].close)))
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            direction=models.TradeDirection.LONG,
            entry_price=price,
            stop_loss=price * Decimal("0.95"),
            take_profit=price * Decimal("1.10"),
        )

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        return entry_price * Decimal("0.95")

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        return []


class AlwaysLongStrategy(BaseStrategy):
    """Async-стратегия: LONG-сигнал на каждом вызове."""

    def __init__(self):
        super().__init__(StrategyConfig(name="always_long"))

    async def evaluate(
        self, symbol, candles, orderbook=None, current_price=None, market_regime=None
    ):
        price = Decimal(str(current_price or float(candles[-1].close)))
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            direction=models.TradeDirection.LONG,
            entry_price=price,
            stop_loss=price * Decimal("0.95"),
            take_profit=price * Decimal("1.10"),
        )

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        return entry_price * Decimal("0.95")

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        return []


def make_base_candles(n: int, open_time0: int = 1_700_000_000_000) -> list[dict]:
    """n свечей по цене 100 (high 101, low 99) с часовым шагом."""
    out = []
    for i in range(n):
        out.append(
            {
                "open_time": open_time0 + i * 3_600_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 10.0,
                "quote_volume": 1000.0,
            }
        )
    return out


def run_engine(strategy: BaseStrategy, candles: list[dict]) -> BacktestEngine:
    config = BacktestConfig(
        symbol="BTC/USDT",
        timeframe="1h",
        initial_capital=Decimal("1000"),
        max_open_positions=1,
        risk_config={"risk_per_trade": "0.004"},
    )
    engine = BacktestEngine(config)
    engine.add_strategy(strategy.name, strategy)
    engine.load_candles(candles)
    engine.run()
    return engine


def closed_trades(engine: BacktestEngine):
    return [t for t in engine.get_trades() if t.result in ("won", "lost")]


def test_async_strategy_is_awaited_and_trade_opens():
    engine = run_engine(OneShotLongStrategy(), make_base_candles(102))
    trades = closed_trades(engine)
    assert len(trades) == 1
    assert trades[0].strategy_name == "one_shot_long"


def test_stop_loss_exits_intrabar_at_stop_price():
    candles = make_base_candles(102)
    candles[SIGNAL_IDX + 1] = candles[SIGNAL_IDX + 1] | {"low": 94.0, "close": 96.0}
    engine = run_engine(OneShotLongStrategy(), candles)
    trades = closed_trades(engine)
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "stop_loss"
    assert t.exit_price == Decimal("95")
    assert t.exit_price == t.stop_loss
    assert t.pnl < 0


def test_take_profit_exits_intrabar_at_tp_price():
    candles = make_base_candles(102)
    candles[SIGNAL_IDX + 1] = candles[SIGNAL_IDX + 1] | {"high": 112.0, "close": 108.0}
    engine = run_engine(OneShotLongStrategy(), candles)
    trades = closed_trades(engine)
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "take_profit"
    assert t.exit_price == Decimal("110")
    assert t.pnl > 0


def test_net_profit_does_not_double_count_fees():
    candles = make_base_candles(102)
    candles[SIGNAL_IDX + 1] = candles[SIGNAL_IDX + 1] | {"high": 112.0, "close": 108.0}
    engine = run_engine(OneShotLongStrategy(), candles)
    result = engine._calculate_results()
    # trade.pnl уже включает комиссии; итоговый net_profit не должен
    # вычитать их повторно.
    assert result.net_profit == result.total_pnl
    assert result.total_fees > 0


def test_one_position_at_a_time():
    # Стратегия сигналит на каждом баре, но с max_open_positions=1 вторая
    # позиция не должна открываться, пока первая не закрыта.
    engine = run_engine(AlwaysLongStrategy(), make_base_candles(106))
    trades = closed_trades(engine)
    assert len(trades) == 1
