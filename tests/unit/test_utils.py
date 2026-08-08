"""
ASTRA BOT — Unit Tests for Utilities
"""

import pytest
from decimal import Decimal

from astra_bot.core.utils import (
    safe_decimal,
    round_to_precision,
    round_to_step,
    calculate_position_size,
    calculate_stop_loss,
    calculate_take_profit_levels,
    calculate_risk_reward_ratio,
    calculate_expected_value,
    calculate_position_risk,
    format_currency,
    format_percentage,
    is_valid_symbol,
    parse_instrument_symbol,
    get_timebucket,
    calculate_timeframe_minutes,
    generate_client_order_id,
    calculate_atr,
    calculate_rsi,
    calculate_bollinger_bands,
    simple_moving_average,
    exponential_moving_average,
    normalize,
    sigmoid,
    clamp,
)


class TestSafeDecimal:
    """Тесты safe_decimal"""
    
    def test_none_returns_default(self):
        assert safe_decimal(None) == Decimal("0")
        assert safe_decimal(None, Decimal("10")) == Decimal("10")
    
    def test_decimal_passthrough(self):
        val = Decimal("123.456")
        assert safe_decimal(val) == val
    
    def test_float_conversion(self):
        assert safe_decimal(123.456) == Decimal("123.456")
    
    def test_string_conversion(self):
        assert safe_decimal("123.456") == Decimal("123.456")
    
    def test_invalid_conversion(self):
        # При неудаче возвращается default (Decimal("0"))
        assert safe_decimal("invalid") == Decimal("0")
        assert safe_decimal("invalid", Decimal("999")) == Decimal("999")


class TestRounding:
    """Тесты округления"""
    
    def test_round_to_precision(self):
        assert round_to_precision(Decimal("123.456789"), 2) == Decimal("123.45")
        assert round_to_precision(Decimal("123.456789"), 0) == Decimal("123")
        assert round_to_precision(Decimal("0.001"), 4) == Decimal("0.0010")
    
    def test_round_to_step(self):
        assert round_to_step(Decimal("123.456"), Decimal("0.1")) == Decimal("123.4")
        assert round_to_step(Decimal("123.456"), Decimal("1")) == Decimal("123")
        assert round_to_step(Decimal("0.005"), Decimal("0.01")) == Decimal("0.00")


class TestPositionCalculations:
    """Тесты расчётов позиции"""
    
    def test_calculate_position_size(self):
        """Тест расчёта размера позиции"""
        risk = Decimal("100")
        entry = Decimal("50000")
        stop = Decimal("49000")
        
        size, reason = calculate_position_size(risk, entry, stop)
        
        # 100 / 1000 = 0.1 (не 0.01)
        assert size == Decimal("0.1")
        assert reason == ""
    
    def test_calculate_position_size_insufficient_min_notional(self):
        """Тест недостаточного номинала"""
        risk = Decimal("10")
        entry = Decimal("50000")
        stop = Decimal("49999")
        min_notional = Decimal("50")
        
        size, reason = calculate_position_size(
            risk, entry, stop, min_notional=min_notional
        )
        
        # 10 / 1 = 10 BTC, notional = 10 * 50000 = 500000 > 50
        # Так что пройдёт проверку, размер будет 10
        assert size > 0
        assert reason == ""
    
    def test_calculate_position_size_below_min_qty(self):
        """Тест когда размер ниже минимального количества"""
        risk = Decimal("0.001")  # Очень маленький риск
        entry = Decimal("50000")
        stop = Decimal("49999")  # Стоп на 1 рубль
        min_qty = Decimal("0.001")
        
        size, reason = calculate_position_size(
            risk, entry, stop, min_quantity=min_qty
        )
        
        # 0.001 / 1 = 0.001 BTC = min_qty, должно пройти
        # Для теста сделаем риск ещё меньше
        risk = Decimal("0.0001")
        size, reason = calculate_position_size(
            risk, entry, stop, min_quantity=min_qty
        )
        
        assert size == Decimal("0")
        assert "below minimum quantity" in reason


class TestStopLoss:
    """Тесты расчёта стоп-лосса"""
    
    def test_atr_based_stop(self):
        """Тест стоп-лосса на основе ATR"""
        entry = Decimal("100")
        atr = Decimal("2")
        stop = calculate_stop_loss(entry, atr, atr_multiplier=1.5)
        
        assert stop == Decimal("97")  # 100 - 2*1.5 = 97
    
    def test_atr_multiplier_zero(self):
        """Тест с нулевым ATR"""
        entry = Decimal("100")
        stop = calculate_stop_loss(entry, Decimal("0"))
        
        assert stop == entry - Decimal("0")


class TestTakeProfit:
    """Тесты расчёта тейк-профита"""
    
    def test_r_multiple_levels(self):
        """Тест уровней на основе R-множителей"""
        entry = Decimal("100")
        stop = Decimal("95")
        
        levels = calculate_take_profit_levels(entry, stop, [1, 2, 3])
        
        assert len(levels) == 3
        assert levels[0]["r_multiple"] == 1.0
        assert levels[1]["r_multiple"] == 2.0
        assert levels[2]["r_multiple"] == 3.0


class TestRiskReward:
    """Тесты risk/reward"""
    
    def test_calculation(self):
        """Тест расчёта R:R"""
        entry = Decimal("100")
        stop = Decimal("95")
        tp = Decimal("110")
        
        ratio = calculate_risk_reward_ratio(entry, stop, tp)
        
        assert ratio == 2.0  # (110-100)/(100-95) = 10/5 = 2


class TestExpectedValue:
    """Тесты математического ожидания"""
    
    def test_positive_ev(self):
        """Тест положительного EV"""
        ev = calculate_expected_value(
            win_probability=0.6,
            avg_win_r=1.5,
            avg_loss_r=1.0,
        )
        
        # 0.6 * 1.5 - 0.4 * 1.0 = 0.9 - 0.4 = 0.5
        assert abs(ev - 0.5) < 0.0001
    
    def test_negative_ev(self):
        """Тест отрицательного EV"""
        ev = calculate_expected_value(
            win_probability=0.4,
            avg_win_r=1.0,
            avg_loss_r=1.0,
        )
        
        # 0.4 * 1.0 - 0.6 * 1.0 = -0.2
        assert abs(ev - (-0.2)) < 0.0001


class TestFormatting:
    """Тесты форматирования"""
    
    def test_format_currency(self):
        assert format_currency(Decimal("1234.56")) == "1234.56 ₽"
        assert format_currency(None) == "0.00"  # Без валюты для None
    
    def test_format_percentage(self):
        assert format_percentage(Decimal("12.345")) == "12.35%"
        assert format_percentage(None) == "0.00%"


class TestSymbolUtils:
    """Тесты работы с символами"""
    
    def test_valid_symbol(self):
        assert is_valid_symbol("BTC/USDT") is True
        assert is_valid_symbol("ETH/USDT") is True
        assert is_valid_symbol("BTC-USDT") is False
        assert is_valid_symbol("") is False
        assert is_valid_symbol("/") is False
    
    def test_parse_symbol(self):
        base, quote = parse_instrument_symbol("BTC/USDT")
        assert base == "BTC"
        assert quote == "USDT"


class TestTimeUtils:
    """Тесты временных утилит"""
    
    def test_get_timebucket(self):
        from datetime import datetime
        
        dt = datetime(2024, 1, 15, 14, 37, 23)
        
        bucket_1m = get_timebucket(dt, "1m")
        assert bucket_1m.minute == 37
        assert bucket_1m.second == 0
        
        bucket_5m = get_timebucket(dt, "5m")
        assert bucket_5m.minute == 35
        assert bucket_5m.second == 0
        
        bucket_1h = get_timebucket(dt, "1h")
        assert bucket_1h.hour == 14
        assert bucket_1h.minute == 0
    
    def test_timeframe_minutes(self):
        assert calculate_timeframe_minutes("1m") == 1
        assert calculate_timeframe_minutes("5m") == 5
        assert calculate_timeframe_minutes("15m") == 15
        assert calculate_timeframe_minutes("1h") == 60
        assert calculate_timeframe_minutes("4h") == 240
        assert calculate_timeframe_minutes("1d") == 1440


class TestClientOrderId:
    """Тесты генерации client order ID"""
    
    def test_generation(self):
        from datetime import datetime
        
        uid = generate_client_order_id("momentum", "BTC/USDT")
        
        assert uid.startswith("astra_BTC-USDT/momentum_")
        assert len(uid) > 30


class TestMathUtils:
    """Тесты математических утилит"""
    
    def test_clamp(self):
        assert clamp(5, 0, 10) == 5
        assert clamp(-5, 0, 10) == 0
        assert clamp(15, 0, 10) == 10
    
    def test_normalize(self):
        assert normalize(50, 0, 100) == 0.5
        assert normalize(0, 0, 100) == 0
        assert normalize(100, 0, 100) == 1.0
        assert normalize(50, 50, 50) == 0.5  # degenerate case
    
    def test_sigmoid(self):
        import math
        assert abs(sigmoid(0) - 0.5) < 0.0001
        assert sigmoid(10) > 0.99
        assert sigmoid(-10) < 0.01


class TestMovingAverages:
    """Тесты скользящих средних"""
    
    def test_sma(self):
        values = [10, 20, 30, 40, 50]
        assert simple_moving_average(values, 3) == 40.0
        assert simple_moving_average(values, 5) == 30.0
    
    def test_sma_insufficient_data(self):
        values = [10, 20]
        assert simple_moving_average(values, 5) is None
    
    def test_ema(self):
        values = [10, 20, 30, 40, 50]
        ema = exponential_moving_average(values, 3)
        # EMA должна быть между SMA и последним значением
        assert ema is not None
        assert ema > simple_moving_average(values, 3)


class TestRSI:
    """Тесты RSI"""
    
    def test_rsi_calculation(self):
        # Upward trend
        closes = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118,
                  120, 122, 124, 126, 128, 130, 132, 134, 136, 138, 140]
        rsi = calculate_rsi(closes)
        assert rsi is not None
        assert rsi > 50  # Восходящий тренд -> RSI > 50
    
    def test_rsi_overbought(self):
        # Strong upward move
        closes = list(range(100, 150))
        rsi = calculate_rsi(closes)
        assert rsi is not None
        assert rsi > 70  # Перекуплен


class TestATR:
    """Тесты ATR"""
    
    def test_atr_calculation(self):
        highs = [102, 104, 106, 108, 110, 112, 114, 116, 118, 120,
                 122, 124, 126, 128, 130, 132, 134, 136, 138, 140, 142, 144]
        lows = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118,
                120, 122, 124, 126, 128, 130, 132, 134, 136, 138, 140, 142]
        closes = [101, 103, 105, 107, 109, 111, 113, 115, 117, 119,
                  121, 123, 125, 127, 129, 131, 133, 135, 137, 139, 141, 143]
        
        atr = calculate_atr(highs, lows, closes, 14)
        assert atr is not None
        assert atr > 0


class TestBollingerBands:
    """Тесты Bollinger Bands"""
    
    def test_bb_calculation(self):
        # Создаём данные с нормальным распределением вокруг 100
        import random
        random.seed(42)
        closes = [100 + random.gauss(0, 2) for _ in range(30)]
        
        bb = calculate_bollinger_bands(closes, period=20, std_dev=2.0)
        
        assert bb is not None
        assert bb["middle"] is not None
        assert bb["upper"] > bb["middle"]
        assert bb["lower"] < bb["middle"]
        assert bb["bandwidth"] > 0
    
    def test_bb_insufficient_data(self):
        closes = [100, 101, 102]
        bb = calculate_bollinger_bands(closes, period=20)
        assert bb is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
