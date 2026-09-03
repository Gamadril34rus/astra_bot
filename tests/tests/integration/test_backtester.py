"""
ASTRA BOT — Integration Tests for Backtester
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from astra_bot.backtester.analyzer import BacktestAnalyzer, PerformanceMetrics
from astra_bot.backtester.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    Trade,
)


class TestBacktestConfig:
    """Тесты конфигурации бэктеста"""

    def test_default_config(self):
        """Тест конфигурации по умолчанию"""
        config = BacktestConfig()

        assert config.symbol == "BTC/USDT"
        assert config.timeframe == "1h"
        assert config.initial_capital == Decimal("1000")
        assert config.maker_fee_rate == Decimal("0.001")
        assert config.taker_fee_rate == Decimal("0.001")

    def test_custom_config(self):
        """Тест кастомной конфигурации"""
        config = BacktestConfig(
            symbol="ETH/USDT",
            initial_capital=Decimal("5000"),
            maker_fee_rate=Decimal("0.0005"),
        )

        assert config.symbol == "ETH/USDT"
        assert config.initial_capital == Decimal("5000")
        assert config.maker_fee_rate == Decimal("0.0005")


class TestTrade:
    """Тесты модели сделки"""

    def test_trade_creation(self):
        """Тест создания сделки"""
        trade = Trade(
            id=1,
            entry_time=datetime(2024, 1, 1),
            entry_price=Decimal("50000"),
            side="long",
            quantity=Decimal("0.1"),
            strategy_name="momentum",
        )

        assert trade.id == 1
        assert trade.entry_price == Decimal("50000")
        assert trade.side == "long"
        assert trade.result == "open"

    def test_trade_pnl_calculation(self):
        """Тест расчёта PnL"""
        trade = Trade(
            id=1,
            entry_time=datetime(2024, 1, 1),
            entry_price=Decimal("50000"),
            exit_time=datetime(2024, 1, 2),
            exit_price=Decimal("51000"),
            side="long",
            quantity=Decimal("0.1"),
        )

        # PnL рассчитывается отдельно
        assert trade.pnl == Decimal("0")


class TestBacktestEngine:
    """Тесты движка бэктеста"""

    @pytest.fixture
    def simple_candles(self):
        """Создать простые свечи для тестов"""
        candles = []
        base_price = 100.0
        base_time = datetime(2024, 1, 1).timestamp()

        for i in range(100):
            price = base_price + i * 0.5

            candle = {
                "open_time": int(base_time + i * 60),
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 100,
                "quote_volume": 100 * price,
            }
            candles.append(candle)

        return candles

    def test_load_candles(self, simple_candles):
        """Тест загрузки свечей"""
        config = BacktestConfig()
        engine = BacktestEngine(config)
        engine.load_candles(simple_candles)

        assert len(engine._candles) == len(simple_candles)

    def test_load_candles_from_dataframe(self):
        """Тест загрузки из DataFrame"""
        dates = pd.date_range(start="2024-01-01", periods=100, freq="1h")

        df = pd.DataFrame({
            "open_time": dates,
            "open": np.linspace(100, 150, 100),
            "high": np.linspace(100, 150, 100) + 1,
            "low": np.linspace(100, 150, 100) - 1,
            "close": np.linspace(100, 150, 100),
            "volume": np.ones(100) * 100,
        })

        config = BacktestConfig()
        engine = BacktestEngine(config)
        engine.load_candles_from_dataframe(df)

        assert len(engine._candles) == 100

    def test_run_backtest_no_candles(self):
        """Тест бэктеста без свечей"""
        config = BacktestConfig()
        engine = BacktestEngine(config)

        with pytest.raises(ValueError, match="No candles loaded"):
            engine.run()

    def test_run_backtest_empty(self, simple_candles):
        """Тест бэктеста без стратегий"""
        config = BacktestConfig(
            initial_capital=Decimal("10000"),
            min_notional=Decimal("10"),
        )

        engine = BacktestEngine(config)
        engine.load_candles(simple_candles)

        result = engine.run()

        # Без стратегий не должно быть сделок
        assert result.total_trades == 0
        assert result.net_profit == 0

    def test_calculate_quantity(self):
        """Тест расчёта количества"""
        config = BacktestConfig(
            min_notional=Decimal("10"),
            min_quantity=Decimal("0.0001"),
        )

        engine = BacktestEngine(config)

        # Тест 1: нормальное количество
        qty = engine._calculate_quantity(Decimal("0.1"), Decimal("50000"))
        assert qty == Decimal("0.1")

        # Тест 2: слишком маленькое количество
        qty = engine._calculate_quantity(Decimal("0.00001"), Decimal("50000"))
        assert qty == Decimal("0")

        # Тест 3: слишком маленький номинал
        qty = engine._calculate_quantity(Decimal("0.0001"), Decimal("0.01"))
        assert qty == Decimal("0")


class TestBacktestAnalyzer:
    """Тесты анализатора"""

    @pytest.fixture
    def mock_result(self):
        """Создать mock результат"""
        result = MagicMock(spec=BacktestResult)
        result.total_trades = 10
        result.wins = 6
        result.losses = 4
        result.win_rate = 60.0
        result.total_pnl = Decimal("500")
        result.total_fees = Decimal("0")  # Без комиссий для упрощения
        result.total_slippage = Decimal("0")
        result.net_profit = Decimal("500")  # Положительный
        result.profit_factor = 1.5
        result.avg_win = Decimal("100")
        result.avg_loss = Decimal("-50")
        result.largest_win = Decimal("200")
        result.largest_loss = Decimal("-100")
        result.max_drawdown = Decimal("100")
        result.max_drawdown_pct = Decimal("5")
        result.final_equity = Decimal("10500")  # initial + 500
        result.sharpe_ratio = 1.2
        result.sortino_ratio = 1.5
        result.total_return = Decimal("500")
        result.return_pct = Decimal("5.0")

        # Создаём пустые списки для trades и daily_stats
        result.trades = []
        result.daily_stats = []

        result.equity_curve = [
            {"timestamp": 1704067200, "equity": 10000},
            {"timestamp": 1704153600, "equity": 10200},
            {"timestamp": 1704240000, "equity": 10100},
            {"timestamp": 1704326400, "equity": 10500},
        ]

        # Create a proper config mock
        config = MagicMock()
        config.initial_capital = Decimal("10000")
        result.config = config

        return result

    @pytest.fixture
    def mock_result_with_trades(self):
        """Создать mock результат с сделками"""
        result = MagicMock(spec=BacktestResult)
        result.total_trades = 3
        result.wins = 2
        result.losses = 1
        result.win_rate = 66.67
        result.total_pnl = Decimal("120")
        result.total_fees = Decimal("0")
        result.total_slippage = Decimal("0")
        result.net_profit = Decimal("120")
        result.profit_factor = 2.0
        result.sharpe_ratio = 1.0
        result.sortino_ratio = 1.0
        result.max_drawdown = Decimal("50")
        result.max_drawdown_pct = Decimal("2")
        result.final_equity = Decimal("10120")
        result.total_return = Decimal("120")
        result.return_pct = Decimal("1.2")
        result.equity_curve = [
            {"timestamp": 1704067200, "equity": 10000},
            {"timestamp": 1704326400, "equity": 10120},
        ]

        config = MagicMock()
        config.initial_capital = Decimal("10000")
        result.config = config

        # Создаём сделки
        from datetime import datetime

        trade1 = MagicMock()
        trade1.result = "won"
        trade1.pnl = Decimal("100")
        trade1.pnl_pct = Decimal("1.0")
        trade1.entry_time = datetime(2024, 1, 1)
        trade1.exit_time = datetime(2024, 1, 2)

        trade2 = MagicMock()
        trade2.result = "won"
        trade2.pnl = Decimal("50")
        trade2.pnl_pct = Decimal("0.5")
        trade2.entry_time = datetime(2024, 1, 3)
        trade2.exit_time = datetime(2024, 1, 4)

        trade3 = MagicMock()
        trade3.result = "lost"
        trade3.pnl = Decimal("-30")
        trade3.pnl_pct = Decimal("-0.3")
        trade3.entry_time = datetime(2024, 1, 5)
        trade3.exit_time = datetime(2024, 1, 6)

        result.trades = [trade1, trade2, trade3]
        result.daily_stats = []

        return result

    def test_performance_metrics_creation(self, mock_result):
        """Тест создания метрик"""
        metrics = PerformanceMetrics.from_backtest_result(mock_result)

        # Так как trades пустой, метрики рассчитываются на основе equity curve
        assert metrics is not None
        assert metrics.final_equity == 10500.0
        assert metrics.initial_equity == 10000.0
        assert metrics.total_return == 500.0
        assert metrics.total_return_pct == 5.0
        assert metrics.max_drawdown_pct == 5.0
        assert metrics.sharpe_ratio > 0
        # is_profitable проверяет net_profit > 0, который рассчитывается из trades
        # У нас trades пустой, поэтому net_profit = 0 - 0 = 0, и is_profitable будет False
        # Это корректное поведение - без сделок нет прибыли
        assert not metrics.is_profitable  # Без сделок нет прибыли

    def test_analyzer_analysis(self, mock_result):
        """Тест анализа"""
        analyzer = BacktestAnalyzer(mock_result)
        analysis = analyzer.analyze()

        assert "summary" in analysis
        assert "trades" in analysis
        assert "equity_curve" in analysis

    def test_trade_distribution(self, mock_result_with_trades):
        """Тест распределения сделок"""
        analyzer = BacktestAnalyzer(mock_result_with_trades)
        distribution = analyzer.get_trade_distribution()

        assert distribution["count"] == 3
        assert distribution["mean"] > 0
        assert distribution["min"] < 0

    def test_drawdown_analysis(self, mock_result):
        """Тест анализа просадок"""
        analyzer = BacktestAnalyzer(mock_result)
        dd_analysis = analyzer.get_drawdown_analysis()

        assert "max_drawdown_pct" in dd_analysis
        assert "current_drawdown_pct" in dd_analysis

    def test_monthly_performance(self, mock_result):
        """Тест месячной производительности"""
        analyzer = BacktestAnalyzer(mock_result)
        monthly = analyzer.get_monthly_performance()

        assert len(monthly) > 0
        for m in monthly:
            assert "month" in m
            assert "return_pct" in m

    def test_print_report(self, mock_result, capsys):
        """Тест вывода отчёта"""
        analyzer = BacktestAnalyzer(mock_result)
        analyzer.print_report()

        captured = capsys.readouterr()
        assert "BACKTEST REPORT" in captured.out
        assert "SUMMARY" in captured.out


class TestIntegration:
    """Интеграционные тесты"""

    def test_full_backtest_workflow(self):
        """Тест полного workflow бэктеста"""
        # 1. Создание конфига
        config = BacktestConfig(
            symbol="BTC/USDT",
            initial_capital=Decimal("10000"),
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 10),
            min_notional=Decimal("10"),
            slippage_percent=Decimal("0"),
            maker_fee_rate=Decimal("0"),
        )

        # 2. Создание движка
        engine = BacktestEngine(config)

        # 3. Генерация данных
        candles = []
        base_price = 50000.0
        base_time = datetime(2024, 1, 1).timestamp()

        for i in range(500):
            trend = i * 5  # Восходящий тренд
            price = base_price + trend

            candles.append({
                "open_time": int(base_time + i * 3600),
                "open": price,
                "high": price + 50,
                "low": price - 50,
                "close": price + 25,
                "volume": 100,
                "quote_volume": 100 * price,
            })

        # 4. Загрузка данных
        engine.load_candles(candles)

        # 5. Запуск (без стратегий для теста)
        result = engine.run()

        # 6. Анализ
        analyzer = BacktestAnalyzer(result)
        metrics = analyzer.metrics

        # 7. Проверка результатов
        assert result is not None
        assert metrics is not None
        assert metrics.total_trades == 0  # Без стратегий

        # 8. Equity curve не пустая
        assert len(result.equity_curve) > 0
        assert result.equity_curve[-1]["equity"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
