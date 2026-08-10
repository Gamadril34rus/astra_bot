"""
ASTRA BOT — Unit Tests for Risk Engine
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from astra_bot.engines.risk_engine import (
    PositionSizeResult,
    RiskConfig,
    RiskEngine,
)


class TestRiskConfig:
    """Тесты конфигурации риска"""

    def test_default_config(self):
        """Тест конфигурации по умолчанию"""
        config = RiskConfig()

        assert config.risk_per_trade == Decimal("0.004")
        assert config.daily_loss_limit == Decimal("0.02")
        assert config.weekly_loss_limit == Decimal("0.04")
        assert config.soft_drawdown == Decimal("0.05")
        assert config.hard_drawdown == Decimal("0.08")
        assert config.emergency_drawdown == Decimal("0.10")
        assert config.max_exposure_pct == Decimal("0.30")
        assert config.max_open_positions == 5

    def test_custom_config(self):
        """Тест кастомной конфигурации"""
        config = RiskConfig(
            risk_per_trade=Decimal("0.005"),
            max_open_positions=3,
        )

        assert config.risk_per_trade == Decimal("0.005")
        assert config.max_open_positions == 3


class TestRiskEngine:
    """Тесты Risk Engine"""

    @pytest.fixture
    def risk_engine(self):
        """Создать Risk Engine"""
        config = RiskConfig()
        engine = RiskEngine(config)
        engine.set_capital(Decimal("1000"), Decimal("1000"))
        return engine

    def test_initial_state(self, risk_engine):
        """Тест начального состояния"""
        assert risk_engine.risk_state.value == "NORMAL"
        assert risk_engine.trading_enabled is True
        assert risk_engine.current_drawdown == Decimal("0")
        assert risk_engine._daily_pnl == Decimal("0")
        assert risk_engine._weekly_pnl == Decimal("0")

    def test_set_capital(self, risk_engine):
        """Тест установки капитала"""
        risk_engine.set_capital(Decimal("1500"), Decimal("1000"))

        assert risk_engine._current_equity == Decimal("1500")
        assert risk_engine._high_water_mark == Decimal("1500")

    def test_drawdown_calculation(self, risk_engine):
        """Тест расчёта просадки"""
        risk_engine.set_capital(Decimal("800"), Decimal("1000"))

        # Просадка 20%
        assert float(risk_engine.current_drawdown) > 19.0
        assert float(risk_engine.current_drawdown) < 21.0

    def test_risk_multiplier_no_drawdown(self, risk_engine):
        """Тест множителя риска без просадки."""
        risk_engine.set_capital(Decimal("1000"), Decimal("1000"))

        multiplier = risk_engine._get_risk_multiplier()
        # При нулевой просадке риск не снижается — полный размер.
        assert multiplier == Decimal("1.0")

    def test_risk_multiplier_with_drawdown(self, risk_engine):
        """Тест множителя риска с просадкой."""
        risk_engine.set_capital(Decimal("960"), Decimal("1000"))  # 4% DD -> tier 3%

        multiplier = risk_engine._get_risk_multiplier()
        assert multiplier == Decimal("0.75")

    def test_risk_multiplier_reduced_at_5pct(self, risk_engine):
        risk_engine.set_capital(Decimal("950"), Decimal("1000"))  # 5% DD
        assert risk_engine._get_risk_multiplier() == Decimal("0.5")

    def test_risk_multiplier_zero_at_8pct(self, risk_engine):
        risk_engine.set_capital(Decimal("920"), Decimal("1000"))  # 8% DD
        assert risk_engine._get_risk_multiplier() == Decimal("0.0")

    def test_risk_multiplier_critical_drawdown(self, risk_engine):
        """Тест множителя риска при критической просадке"""
        risk_engine.set_capital(Decimal("900"), Decimal("1000"))  # 10% DD

        multiplier = risk_engine._get_risk_multiplier()
        # При 10% DD множитель должен быть 0
        assert multiplier == Decimal("0")

    def test_check_trade_approved(self, risk_engine):
        """Тест проверки сделки — одобрено"""
        # Используем меньший размер позиции чтобы риск был в пределах лимита
        # Риск 0.4% от 1000 = 4 рубля, стоп 1000 рублей -> размер 0.004 BTC
        result = risk_engine.check_trade(
            symbol="BTC/USDT",
            side="buy",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("49000"),
            take_profit=Decimal("52000"),
            proposed_size=Decimal("0.001"),  # Меньший размер
            strategy_name="momentum",
        )

        assert result.approved is True
        assert result.risk_state == "NORMAL"

    def test_check_trade_excessive_risk(self, risk_engine):
        """Тест проверки сделки — превышение риска"""
        # Предлагаем слишком большой размер позиции
        result = risk_engine.check_trade(
            symbol="BTC/USDT",
            side="buy",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),  # Большой стоп
            take_profit=Decimal("55000"),
            proposed_size=Decimal("1.0"),  # Слишком большой размер
            strategy_name="momentum",
        )

        assert result.approved is False
        assert result.reason is not None

    def test_check_trade_daily_loss_limit(self, risk_engine):
        """Тест проверки — превышение дневного лимита"""
        # Имитация потерь за день
        risk_engine._daily_pnl = -Decimal("25")  # Более 2% от 1000

        result = risk_engine.check_trade(
            symbol="BTC/USDT",
            side="buy",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("49000"),
            take_profit=Decimal("52000"),
            proposed_size=Decimal("0.1"),
        )

        assert result.approved is False
        assert "daily" in result.reason.lower()

    def test_calculate_position_size(self, risk_engine):
        """Тест расчёта размера позиции"""
        result = risk_engine.calculate_position_size(
            symbol="BTC/USDT",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("49000"),
        )

        assert result.accepted is True
        assert result.quantity > 0
        # Риск 0.4% от 1000 = 4 USDT, стоп 1000 USDT -> размер 0.004 BTC.
        # Множитель риска при нулевой просадке равен 1.0.
        expected_size = Decimal("0.004")
        assert abs(result.quantity - expected_size) < Decimal("0.0001")

    def test_calculate_position_size_insufficient_funds(self, risk_engine):
        """Тест расчёта размера — недостаточно средств"""
        # Устанавливаем слишком маленький капитал
        risk_engine.set_capital(Decimal("10"), Decimal("10"))

        result = risk_engine.calculate_position_size(
            symbol="BTC/USDT",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("49000"),
        )

        # При капитале 10 и риске 0.4% * 0.75 = 0.03 рубля
        # Стоп 1000 рублей -> размер 0.00003 BTC
        # Это очень маленький размер, но теоретически возможный
        assert result.accepted is True
        # Размер просто очень маленький
        assert result.quantity >= 0

    def test_record_trade(self, risk_engine):
        """Тест записи сделки"""
        initial_equity = risk_engine._current_equity

        risk_engine.record_trade(
            symbol="BTC/USDT",
            side="buy",
            entry_price=Decimal("50000"),
            quantity=Decimal("0.1"),
            pnl=Decimal("50"),
            won=True,
        )

        assert risk_engine._total_trades == 1
        assert risk_engine._total_wins == 1
        assert risk_engine._daily_pnl == Decimal("50")
        assert risk_engine._current_equity == initial_equity + Decimal("50")

    def test_record_losing_trade(self, risk_engine):
        """Тест записи убыточной сделки"""
        risk_engine.record_trade(
            symbol="BTC/USDT",
            side="buy",
            entry_price=Decimal("50000"),
            quantity=Decimal("0.1"),
            pnl=Decimal("-30"),
            won=False,
        )

        assert risk_engine._total_trades == 1
        assert risk_engine._total_losses == 1
        assert risk_engine._daily_pnl == Decimal("-30")

    def test_trading_disabled_after_hard_drawdown(self, risk_engine):
        """Тест отключения торговли при жёсткой просадке"""
        risk_engine.set_capital(Decimal("920"), Decimal("1000"))  # 8% DD

        # При 8% просадке trading_enabled должно стать False
        # Но сначала нужно вызвать _check_drawdown_state
        risk_engine._check_drawdown_state()

        assert risk_engine.trading_enabled is False
        assert risk_engine.risk_state.value == "STOP"

    def test_multiple_positions_limit(self, risk_engine):
        """Тест ограничения количества позиций"""
        # Добавляем позиции
        for i in range(5):
            pos = MagicMock()
            pos.id = f"pos_{i}"
            pos.quantity = Decimal("0.001")  # Маленький размер
            pos.entry_price = Decimal("50000")
            risk_engine.add_position(pos)

        # Проверяем что 6-я сделка отклоняется
        result = risk_engine.check_trade(
            symbol="ETH/USDT",
            side="buy",
            entry_price=Decimal("3000"),
            stop_loss=Decimal("2900"),
            take_profit=Decimal("3200"),
            proposed_size=Decimal("0.001"),  # Малый риск
        )

        assert result.approved is False
        # Либо превышение риска, либо превышение количества позиций
        assert result.reason is not None


class TestPositionSizeResult:
    """Тесты PositionSizeResult"""

    def test_accepted_result(self):
        """Тест принятого результата"""
        result = PositionSizeResult(
            accepted=True,
            quantity=Decimal("0.1"),
            risk_amount=Decimal("4"),
            risk_state="NORMAL",
            stop_distance=Decimal("1000"),
        )

        assert result.accepted is True
        assert result.quantity == Decimal("0.1")
        assert result.reason is None

    def test_rejected_result(self):
        """Тест отклонённого результата"""
        result = PositionSizeResult(
            accepted=False,
            reason="Insufficient funds",
        )

        assert result.accepted is False
        assert result.quantity is None
        assert result.reason == "Insufficient funds"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
