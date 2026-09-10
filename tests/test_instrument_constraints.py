"""Бэклог аудита A6: ограничения инструмента (step/min_qty/min_notional).

До фикса: paper-вход валидировался только по риск-лимитам — step_size,
min_quantity и min_notional биржи нигде не проверялись (брокер их не
знает, adapter отдаёт Instrument, но живой контур мёртв, а paper —
единственное исполнение). Теперь size режется по ограничениям ДО
открытия; без данных инструмента — fail-open (вход не блокируется).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


def _engine(tmp_path: Path, instrument) -> TradingEngine:
    exchange = MagicMock()
    exchange.get_instrument = AsyncMock(return_value=instrument)
    return TradingEngine(
        exchange=exchange,
        pipeline=MagicMock(),
        config=TradingEngineConfig(
            halt_alerts_path=str(tmp_path / "halt.json")
        ),
    )


def _inst(step="0.001", min_qty="0.001", min_notional="10", tick="0.1"):
    return SimpleNamespace(
        step_size=Decimal(step),
        min_quantity=Decimal(min_qty),
        min_notional=Decimal(min_notional),
        tick_size=Decimal(tick),
    )


@pytest.mark.asyncio
async def test_steps_round_down(tmp_path):
    eng = _engine(tmp_path, _inst(step="0.001"))
    size = await eng._apply_instrument_constraints(
        "BTC-USDT", Decimal("100"), Decimal("1.2345678")
    )
    # Округление ВНИЗ до 0.001 — риск не увеличивается.
    assert size == Decimal("1.234")


@pytest.mark.asyncio
async def test_below_min_quantity_rejected(tmp_path):
    eng = _engine(tmp_path, _inst(min_qty="1"))
    size = await eng._apply_instrument_constraints(
        "BTC-USDT", Decimal("100"), Decimal("0.5")
    )
    assert size is None


@pytest.mark.asyncio
async def test_below_min_notional_rejected(tmp_path):
    eng = _engine(tmp_path, _inst(min_notional="1000"))
    # 0.5 * 100 = 50 < 1000
    size = await eng._apply_instrument_constraints(
        "BTC-USDT", Decimal("100"), Decimal("0.5")
    )
    assert size is None


@pytest.mark.asyncio
async def test_valid_size_unchanged(tmp_path):
    eng = _engine(tmp_path, _inst())
    size = await eng._apply_instrument_constraints(
        "BTC-USDT", Decimal("100"), Decimal("10")
    )
    assert size == Decimal("10")


@pytest.mark.asyncio
async def test_no_instrument_fail_open(tmp_path):
    """Адаптер не отдал инструмент — размер не трогаем (не блокируем вход)."""
    eng = _engine(tmp_path, None)
    size = await eng._apply_instrument_constraints(
        "BTC-USDT", Decimal("100"), Decimal("0.123456")
    )
    assert size == Decimal("0.123456")


@pytest.mark.asyncio
async def test_exchange_error_fail_open(tmp_path):
    """get_instrument упал (сеть) — fail-open, кэшируем None."""
    exchange = MagicMock()
    exchange.get_instrument = AsyncMock(side_effect=RuntimeError("net down"))
    eng = TradingEngine(
        exchange=exchange,
        pipeline=MagicMock(),
        config=TradingEngineConfig(
            halt_alerts_path=str(tmp_path / "halt.json")
        ),
    )
    size = await eng._apply_instrument_constraints(
        "BTC-USDT", Decimal("100"), Decimal("0.123")
    )
    assert size == Decimal("0.123")
    assert eng._instruments_cache["BTC-USDT"] is None


@pytest.mark.asyncio
async def test_instrument_cached(tmp_path):
    """Один запрос на символ за сессию (не на каждый вход)."""
    eng = _engine(tmp_path, _inst())
    await eng._apply_instrument_constraints("BTC-USDT", Decimal("100"), Decimal("1"))
    await eng._apply_instrument_constraints("BTC-USDT", Decimal("100"), Decimal("2"))
    assert eng.exchange.get_instrument.await_count == 1


@pytest.mark.asyncio
async def test_magicmock_exchange_fails_open(tmp_path):
    """Мок без AsyncMock (старые тесты) — await падает, fail-open."""
    eng = TradingEngine(
        exchange=MagicMock(),
        pipeline=MagicMock(),
        config=TradingEngineConfig(
            halt_alerts_path=str(tmp_path / "halt.json")
        ),
    )
    size = await eng._apply_instrument_constraints(
        "BTC-USDT", Decimal("100"), Decimal("0.123")
    )
    assert size == Decimal("0.123")
