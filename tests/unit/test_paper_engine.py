"""
ASTRA BOT — Unit Tests for Paper Trading Engine
"""

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

from astra_bot.paperengine.paper_engine import (
    PaperTradingEngine,
    PaperTrade,
    PaperAccount,
)
from astra_bot.paperengine.simulator import (
    MarketDataSimulator,
    PaperTradingSimulator,
    run_simulation,
)
from astra_bot.core.models import TradeDirection


class TestPaperTrade:
    """Тесты модели PaperTrade"""
    
    def test_trade_creation(self):
        """Тест создания сделки"""
        trade = PaperTrade(
            symbol="BTC/USDT",
            side="long",
            strategy_name="momentum",
            entry_price=Decimal("50000"),
            current_price=Decimal("51000"),
            quantity=Decimal("0.1"),
        )
        
        assert trade.id is not None
        assert trade.symbol == "BTC/USDT"
        assert trade.side == "long"
        assert trade.status == "open"
    
    def test_unrealized_pnl_long(self):
        """Тест расчёта unrealized PnL для LONG"""
        trade = PaperTrade(
            symbol="BTC/USDT",
            side="long",
            entry_price=Decimal("50000"),
            current_price=Decimal("51000"),
            quantity=Decimal("0.1"),
        )
        
        # (51000 - 50000) * 0.1 = 100
        assert float(trade.unrealized_pnl) > 99.9
        assert float(trade.unrealized_pnl) < 100.1
    
    def test_unrealized_pnl_short(self):
        """Тест расчёта unrealized PnL для SHORT"""
        trade = PaperTrade(
            symbol="BTC/USDT",
            side="short",
            entry_price=Decimal("50000"),
            current_price=Decimal("49000"),
            quantity=Decimal("0.1"),
        )
        
        # (50000 - 49000) * 0.1 = 100
        assert float(trade.unrealized_pnl) > 99.9
        assert float(trade.unrealized_pnl) < 100.1
    
    def test_update_price(self):
        """Тест обновления цены"""
        trade = PaperTrade(
            symbol="BTC/USDT",
            side="long",
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            quantity=Decimal("0.1"),
        )
        
        # Сначала без PnL
        assert trade.pnl == Decimal("0")
        
        # Обновляем цену
        trade.update_price(Decimal("51000"))
        
        # Теперь PnL = (51000 - 50000) * 0.1 = 100
        assert float(trade.pnl) > 99.9
        assert float(trade.pnl) < 100.1


class TestPaperAccount:
    """Тесты модели PaperAccount"""
    
    def test_account_creation(self):
        """Тест создания аккаунта"""
        account = PaperAccount(usdt_balance=Decimal("1000"))
        
        assert account.usdt_balance == Decimal("1000")
        assert account.initial_capital == Decimal("1000")
        assert account.equity == Decimal("1000")
        assert len(account.open_positions) == 0
    
    def test_update_equity(self):
        """Тест обновления equity"""
        account = PaperAccount(usdt_balance=Decimal("1000"))
        
        # Добавляем позицию
        trade = PaperTrade(
            symbol="BTC/USDT",
            side="long",
            entry_price=Decimal("50000"),
            current_price=Decimal("51000"),
            quantity=Decimal("0.1"),
        )
        account.open_positions[trade.id] = trade
        
        # Обновляем equity
        account.update_equity(Decimal("51000"))
        
        # Equity = 1000 + (51000-50000)*0.1 = 1100
        assert float(account.equity) > 1099.9
        assert float(account.equity) < 1100.1


class TestPaperTradingEngine:
    """Тесты Paper Trading Engine"""
    
    def test_engine_creation(self):
        """Тест создания движка"""
        engine = PaperTradingEngine(initial_capital=Decimal("1000"))
        
        assert engine.initial_capital == Decimal("1000")
        assert engine.account.usdt_balance == Decimal("1000")
        assert len(engine._strategies) == 0
    
    def test_add_strategy(self):
        """Тест добавления стратегии"""
        from astra_bot.strategies import MomentumStrategy
        
        engine = PaperTradingEngine(initial_capital=Decimal("1000"))
        strategy = MomentumStrategy()
        
        engine.add_strategy("momentum", strategy)
        
        assert "momentum" in engine._strategies
    
    def test_get_account_info(self):
        """Тест получения информации об аккаунте"""
        engine = PaperTradingEngine(initial_capital=Decimal("2000"))
        
        info = engine.get_account_info()
        
        assert info["equity"] == "2000"
        assert info["initial_capital"] == "2000"
        assert info["open_positions"] == 0
        assert info["total_trades"] == 0
    
    def test_register_callbacks(self):
        """Тест регистрации callback'ов"""
        engine = PaperTradingEngine(initial_capital=Decimal("1000"))
        
        callback_called = []
        
        def on_trade_opened(trade):
            callback_called.append(("opened", trade.id))
        
        def on_trade_closed(trade):
            callback_called.append(("closed", trade.id))
        
        engine.register_on_trade_opened(on_trade_opened)
        engine.register_on_trade_closed(on_trade_closed)
        
        assert len(engine._on_trade_opened) == 1
        assert len(engine._on_trade_closed) == 1


class TestMarketDataSimulator:
    """Тесты симулятора рынка"""
    
    def test_simulator_creation(self):
        """Тест создания симулятора"""
        sim = MarketDataSimulator(
            symbol="BTC/USDT",
            initial_price=Decimal("50000"),
            volatility=0.02,
        )
        
        assert sim.symbol == "BTC/USDT"
        assert float(sim.initial_price) == 50000
        assert sim.volatility == 0.02
    
    def test_generate_tick(self):
        """Тест генерации тика"""
        sim = MarketDataSimulator(
            symbol="BTC/USDT",
            initial_price=Decimal("50000"),
            volatility=0.001,  # Низкая волатильность для стабильности
            seed=42,
        )
        
        tick = sim.generate_tick()
        
        assert tick["symbol"] == "BTC/USDT"
        assert "price" in tick
        assert "timestamp" in tick
        assert tick["price"] > 0
    
    def test_generate_candles(self):
        """Тест генерации свечей"""
        sim = MarketDataSimulator(
            symbol="BTC/USDT",
            initial_price=Decimal("50000"),
            volatility=0.001,
            seed=42,
        )
        
        candles = sim.simulate_candles(num_candles=10, timeframe_seconds=3600)
        
        assert len(candles) == 10
        assert "open_time" in candles[0]
        assert "open" in candles[0]
        assert "high" in candles[0]
        assert "low" in candles[0]
        assert "close" in candles[0]


class TestPaperTradingSimulator:
    """Тесты симулятора бумажной торговли"""
    
    def test_simulator_creation(self):
        """Тест создания симулятора"""
        sim = PaperTradingSimulator(
            initial_capital=Decimal("1000"),
            symbol="BTC/USDT",
            initial_price=Decimal("50000"),
        )
        
        assert sim.initial_capital == Decimal("1000")
        assert sim.symbol == "BTC/USDT"
        assert not sim.is_running
    
    def test_add_strategy(self):
        """Тест добавления стратегии"""
        from astra_bot.strategies import MomentumStrategy
        
        sim = PaperTradingSimulator(initial_capital=Decimal("1000"))
        strategy = MomentumStrategy()
        
        sim.add_strategy("momentum", strategy)
        
        assert "momentum" in sim._paper_engine._strategies
    
    def test_get_account_info(self):
        """Тест получения информации"""
        sim = PaperTradingSimulator(initial_capital=Decimal("1000"))
        
        info = sim.get_account_info()
        
        assert info["equity"] == "1000"
        assert info["initial_capital"] == "1000"


class TestIntegration:
    """Интеграционные тесты"""
    
    @pytest.mark.asyncio
    async def test_run_simulation(self):
        """Тест запуска симуляции"""
        from asyncio import get_event_loop
        
        # Запускаем симуляцию на 1 минуту
        result = await run_simulation(
            capital=Decimal("1000"),
            symbol="BTC/USDT",
            initial_price=Decimal("50000"),
            duration_minutes=0.01,  # Очень короткая симуляция для теста
            update_interval=0.1,
        )
        
        assert result is not None
        assert "equity" in result
        assert float(result["equity"]) >= 0
    
    def test_full_workflow(self):
        """Тест полного workflow"""
        # 1. Создание симулятора
        sim = PaperTradingSimulator(
            initial_capital=Decimal("1000"),
            symbol="BTC/USDT",
        )
        
        # 2. Добавление стратегии
        from astra_bot.strategies import MomentumStrategy
        strategy = MomentumStrategy()
        sim.add_strategy("momentum", strategy)
        
        # 3. Проверка начального состояния
        info = sim.get_account_info()
        assert info["equity"] == "1000"
        
        # 4. Генерация данных
        sim._market_sim = MarketDataSimulator(
            symbol="BTC/USDT",
            initial_price=Decimal("50000"),
            seed=42,
        )
        
        candles = sim._market_sim.simulate_candles(num_candles=100)
        
        # 5. Проверка что симулятор готов
        assert sim._paper_engine is not None
        assert sim._market_sim is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
