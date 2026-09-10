"""Бэклог аудита A2: бэктестер хранит позиции как str-id.

Цепочка бага (воспроизведено):
    backtester/engine.py _open_long_position():
        self._risk_engine.add_position(str(trade.id))          # строка, без meta
    -> следующий sizing-вызов:
        risk_engine.py calculate_position_size():
            sum(abs(p.quantity * p.entry_price)
                for p in self._open_positions.values())
    -> AttributeError: 'str' object has no attribute 'quantity'

Ошибка глоталась циклом бэктестера (engine.py ~строка 403:
``except Exception: logger.warning("Strategy ... error")``), поэтому
бэктест ПОЛНОСТЬЮ блокировал новые входы, пока была открыта хотя бы
одна позиция — исследования строились на искажённой выборке сделок.
"""
from __future__ import annotations

from decimal import Decimal

from astra_bot.backtester.engine import BacktestConfig, BacktestEngine
from astra_bot.core import models
from astra_bot.engines.risk_engine import RiskConfig, RiskEngine


def test_str_position_no_attribute_error_in_sizing():
    """Юнит-репро: str-id в книге не должен ронять calculate_position_size."""
    risk = RiskEngine(RiskConfig(risk_per_trade=Decimal("0.004"), max_open_positions=5))
    risk.set_capital(Decimal("1000"), Decimal("1000"))
    # Именно так бэктестер добавлял позицию (до фикса — без meta):
    risk.add_position("0")
    # До фикса: AttributeError: 'str' object has no attribute 'quantity'
    res = risk.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=Decimal("100"),
        stop_loss=Decimal("90"),  # distance 10 -> size 0.4 -> exposure 40 <= 300
    )
    assert res.accepted
    assert res.quantity > 0


def test_str_position_without_meta_counts_as_zero():
    """str без meta: не падает, но номинал = 0 (позиция «невидима»).

    Для корректного учёта нужен meta — его теперь передаёт бэктестер
    (см. test_backtester_passes_meta).
    """
    risk = RiskEngine(
        RiskConfig(risk_per_trade=Decimal("0.004"), max_open_positions=5, max_exposure_pct=Decimal("0.30"))
    )
    risk.set_capital(Decimal("1000"), Decimal("1000"))
    risk.add_position("0")
    assert risk._position_notional("0") == Decimal("0")


def test_str_position_with_meta_counts_exposure():
    """str + meta: номинал учитывается в exposure-лимите."""
    risk = RiskEngine(
        RiskConfig(risk_per_trade=Decimal("0.004"), max_open_positions=5, max_exposure_pct=Decimal("0.10"))
    )
    risk.set_capital(Decimal("1000"), Decimal("1000"))
    # Max exposure = 10% * 1000 = 100; открыто уже 99.
    risk.add_position(
        "0", symbol="BTC/USDT", side="long", notional=Decimal("99")
    )
    res = risk.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
    )
    # Теоретический размер (0.4% от 1000 / 1 = 40) -> exposure 99+4000 >> 100
    assert not res.accepted
    assert res.reason in {"Maximum exposure reached", "Exposure limit exceeded"}


class _RepeatLongStrategy:
    """Два LONG-сигнала (на 3-й и 5-й свече), ничего не закрывает."""

    enabled = True
    _fire_at = (3, 5)

    def get_required_candles(self) -> int:
        return 2

    def evaluate(self, symbol, candles, current_price):
        if len(candles) in self._fire_at:
            price = Decimal(str(current_price))
            return models.Signal(
                symbol=symbol,
                strategy_name="repeat_long",
                direction=models.TradeDirection.LONG,
                entry_price=price,
                # Стоп 4%: size = 0.4% капитала / 4% = 10% notionals на
                # сделку — две сделки укладываются в max_exposure 30%.
                stop_loss=price * Decimal("0.96"),
                take_profit=price * Decimal("1.05"),
                position_size=Decimal("1"),
            )
        return None


def _flat_candles(n: int = 8, price: float = 100.0) -> list[dict]:
    out = []
    for i in range(n):
        out.append(
            {
                "open_time": 1_700_000_000_000 + i * 3_600_000,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000,
            }
        )
    return out


def test_backtester_opens_second_trade_while_first_open():
    """Интеграция: до фикса вторая сделка не открывалась — sizing ронялся
    на str-id первой (ошибка глоталась как «Strategy error»)."""
    bt = BacktestEngine(BacktestConfig(initial_capital=Decimal("10000")))
    bt.add_strategy("repeat_long", _RepeatLongStrategy())
    bt.load_candles(_flat_candles())
    result = bt.run()
    opened = [t for t in bt._trades]
    # Обе свечи с сигналом дали сделки — риск-движок больше не падает.
    assert bt._trade_id_counter == 2, (
        f"ожидали 2 открытые сделки, фактически {bt._trade_id_counter}; "
        "скорее всего sizing упал на str-id (бэклог A2)"
    )
    assert len(opened) >= 2
    # Экспозиция после фикса: обе позиции видны риск-движку с номиналом.
    meta = bt._risk_engine._open_meta
    assert all(m.get("notional") for m in meta.values())
    assert result is not None
