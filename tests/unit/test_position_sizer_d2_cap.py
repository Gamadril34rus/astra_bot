""""D2: жёсткий потолок риска на сделку <= 3% капитала (решение владельца)."""

from decimal import Decimal

from astra_bot.engines.position_sizer import calculate_position_size


class TestD2RiskCap:
    def test_current_config_max_risk_is_1pct(self):
        """Вычисление: базовый 1%, все множители в лучшем случае 1.0
        (Kelly 0.5-1.0, ML 0.5-1.0, волатильность 0.3-1.0) -> 1% < 3%."""
        equity = Decimal("10000")
        entry = Decimal("100")
        stop = Decimal("90")  # 10% стоп
        size = calculate_position_size(
            equity=equity, entry_price=entry, stop_loss=stop,
            risk_per_trade_pct=Decimal("0.01"),
            win_rate=0.99, avg_win_r=10.0, avg_loss_r=0.1,  # Kelly -> max
            ml_confidence=1.0, atr_pct=None,
        )
        actual_risk = size * abs(entry - stop)
        assert actual_risk <= equity * Decimal("0.01") * Decimal("1.0001")

    def test_hard_cap_3pct(self):
        equity = Decimal("10000")
        entry = Decimal("100")
        stop = Decimal("90")
        size = calculate_position_size(
            equity=equity, entry_price=entry, stop_loss=stop,
            risk_per_trade_pct=Decimal("0.05"),  # гипотетически 5%
        )
        actual_risk = size * abs(entry - stop)
        assert actual_risk <= equity * Decimal("0.03") + Decimal("0.000001")
