"""Бэклог аудита A4: open24h из реального тикера, а не (hi+lo)/2.

Клиент BingX возвращает open_24h (adapters/bingx/client.py:654,
openPrice из swap/v2 quote/ticker); до фикса process_symbol его
игнорировал и всегда брал середину диапазона. After-фикс: реальный
open24h, середина — только fallback для адаптеров без поля.
"""
from __future__ import annotations

from decimal import Decimal

from astra_bot.decision.trading_engine import TradingEngine


def test_uses_real_open_when_present():
    """Ключ open_24h (контракт клиента BingX) берётся как есть."""
    ticker = {
        "last": Decimal("100"),
        "high_24h": Decimal("101"),
        "low_24h": Decimal("99.5"),
        "open_24h": Decimal("90"),  # памп +11% — важно для safety > 8%
    }
    m = TradingEngine._build_ticker_map(ticker)
    assert m == {"last": 100.0, "open24h": 90.0}


def test_alt_key_name_supported():
    """Совместимость: адаптеры могут отдавать open24h (без подчёркивания)."""
    m = TradingEngine._build_ticker_map(
        {"last": Decimal("10"), "open24h": Decimal("9"), "high_24h": Decimal("10"), "low_24h": Decimal("8")}
    )
    assert m["open24h"] == 9.0


def test_fallback_midpoint_without_field():
    """Нет open24h (например, simulated.py) — середина диапазона, как раньше."""
    m = TradingEngine._build_ticker_map(
        {"last": Decimal("100"), "high_24h": Decimal("102"), "low_24h": Decimal("98")}
    )
    assert m["open24h"] == 100.0  # (102 + 98) / 2


def test_fallback_on_zero_open():
    """open24h == 0 (поле есть, но адаптер не заполнил) — тоже fallback."""
    m = TradingEngine._build_ticker_map(
        {"last": Decimal("100"), "high_24h": Decimal("102"), "low_24h": Decimal("98"), "open_24h": Decimal("0")}
    )
    assert m["open24h"] == 100.0


def test_empty_ticker_returns_none():
    assert TradingEngine._build_ticker_map({}) is None
    assert TradingEngine._build_ticker_map(None) is None


def test_no_range_no_open():
    """Нечего брать ниоткуда — open24h = 0 (safety-проверка пропустится)."""
    m = TradingEngine._build_ticker_map({"last": Decimal("100")})
    assert m == {"last": 100.0, "open24h": 0.0}


def test_pump_not_masked():
    """Регрессия: после пампа, закрепившегося у хая, сдвиг НЕ исчезает.

    Старая оценка (hi+lo)/2 давала open24≈last и «резкое движение 24ч»
    (порог 8% в market_safety.py) не срабатывало.
    """
    # Цена: open 90, pump до 100, закрепление у хая (hi=100, lo=91).
    ticker = {
        "last": Decimal("100"),
        "high_24h": Decimal("100"),
        "low_24h": Decimal("91"),
        "open_24h": Decimal("90"),
    }
    m = TradingEngine._build_ticker_map(ticker)
    change = (m["last"] / m["open24h"] - 1) * 100
    assert change >= 8.0  # safety заблокировал бы вход — как и должно

    # Сравнение со старой оценкой: сдвиг «растворялся».
    old_open = (100 + 91) / 2
    old_change = (100 / old_open - 1) * 100
    assert abs(old_change) < 8.0
