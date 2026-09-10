"""Бэклог аудита A1 (H3): Risk Engine видит NET ликвидационный капитал.

До фикса: broker.equity = initial + realized, и плавающая PnL была
невидима для риска/просадки/сайзинга, пока позиции не закрыты.
После: realized_equity / gross_unrealized / net_equity разделены,
риск-движок синхронизируется с net_equity (mark-to-market).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.engines.cost_model import CostModel

# Минимальные ненулевые издержки (CostModel запрещает нули, TZ P0-1).
# Ожидаемые значения в тестах считаются от фактического fill_price.
_ZERO_COST = CostModel(
    taker_fee_rate=Decimal("0.000001"),
    maker_fee_rate=Decimal("0.000001"),
    slippage_pct=Decimal("0.000001"),
    funding_rate=Decimal("0"),
)


def _fill(pos) -> Decimal:
    return pos.fill_price if pos.fill_price is not None else pos.entry_price


def _broker(tmp_path: Path, capital: Decimal = Decimal("10000")) -> PaperBroker:
    return PaperBroker(
        state_path=tmp_path / "state.json",
        trades_path=tmp_path / "trades.jsonl",
        initial_capital=capital,
        cost_model=_ZERO_COST,
    )


def _open_long(b: PaperBroker, entry: Decimal = Decimal("100")):
    return b.open_position(
        symbol="TEST-USDT",
        direction="long",
        entry_price=entry,
        stop_loss=entry - Decimal("5"),
        take_profit=entry + Decimal("10"),
        quantity=Decimal("10"),
        strategy="test",
    )


# ------------------------------------------------------------ broker level
def test_flat_net_equals_realized(tmp_path):
    b = _broker(tmp_path)
    assert b.equity == b.realized_equity == b.net_equity
    assert b.unrealized_pnl() == Decimal("0")
    assert b.gross_unrealized == Decimal("0")


def test_long_profit_increases_net(tmp_path):
    b = _broker(tmp_path)
    pos = _open_long(b, Decimal("100"))
    b.update_mark_price("TEST-USDT", Decimal("110"))
    expected = (Decimal("110") - _fill(pos)) * Decimal("10")
    assert b.unrealized_pnl() == expected
    assert b.net_equity == Decimal("10000") + expected
    assert b.realized_equity == Decimal("10000")  # realized не изменился
    assert b.gross_unrealized == expected


def test_short_loss_decreases_net(tmp_path):
    b = _broker(tmp_path)
    pos = b.open_position(
        symbol="TEST-USDT",
        direction="short",
        entry_price=Decimal("100"),
        stop_loss=Decimal("105"),
        take_profit=Decimal("90"),
        quantity=Decimal("10"),
        strategy="test",
    )
    b.update_mark_price("TEST-USDT", Decimal("110"))
    # шорт, цена выросла: (fill - 110) * 10 < 0
    expected = (_fill(pos) - Decimal("110")) * Decimal("10")
    assert expected < 0
    assert b.unrealized_pnl() == expected
    assert b.net_equity == Decimal("10000") + expected


def test_no_mark_gives_zero_unrealized(tmp_path):
    b = _broker(tmp_path)
    _open_long(b)
    # mark не подтянут (новый процесс) — плавающая = 0, net = realized
    assert b.unrealized_pnl() == Decimal("0")
    assert b.net_equity == b.equity


def test_gross_unrealized_sums_abs(tmp_path):
    b = _broker(tmp_path)
    pos1 = _open_long(b, Decimal("100"))
    pos2 = b.open_position(
        symbol="SECOND-USDT",
        direction="short",
        entry_price=Decimal("50"),
        stop_loss=Decimal("55"),
        take_profit=Decimal("40"),
        quantity=Decimal("10"),
        strategy="test",
    )
    b.update_mark_price("TEST-USDT", Decimal("110"))    # profit
    b.update_mark_price("SECOND-USDT", Decimal("54"))   # loss
    u1 = (Decimal("110") - _fill(pos1)) * Decimal("10")
    u2 = (_fill(pos2) - Decimal("54")) * Decimal("10")
    assert u1 > 0 and u2 < 0
    assert b.unrealized_pnl() == u1 + u2
    assert b.gross_unrealized == abs(u1) + abs(u2)
    assert b.net_equity == Decimal("10000") + u1 + u2


# ---------------------------------------------------------- engine level
def _engine_with(b: PaperBroker, tmp_path: Path) -> TradingEngine:
    return TradingEngine(
        exchange=MagicMock(),
        pipeline=MagicMock(),
        broker=b,
        config=TradingEngineConfig(halt_alerts_path=str(tmp_path / "halt_alerts.json")),
    )


def test_risk_sees_net_equity_after_mark_move(tmp_path):
    """Главный сценарий A1: просадка видна риску ДО закрытия позиции."""
    b = _broker(tmp_path)
    pos = _open_long(b, Decimal("100"))
    engine = _engine_with(b, tmp_path)

    fill = _fill(pos)
    # Стартовая синхронизация (как в начале сессии): HWM = flat-капитал.
    engine._sync_risk_equity()
    assert engine.risk._high_water_mark == Decimal("10000")
    b.update_mark_price("TEST-USDT", Decimal("90"))  # float loss
    engine._sync_risk_equity()
    expected = Decimal("10000") + (Decimal("90") - fill) * Decimal("10")
    assert engine.risk._current_equity == expected

    # Просадка от HWM (= initial 10000) теперь ненулевая —
    # до фикса (realized-only) она была 0.
    dd = engine.risk.current_drawdown
    assert dd > 0
    assert float(dd) > 0.9  # ~1% при fill ~ 100

    # Восстановление цены — просадка сходит, HWM обновляется вверх.
    b.update_mark_price("TEST-USDT", Decimal("105"))  # float profit
    engine._sync_risk_equity()
    expected = Decimal("10000") + (Decimal("105") - fill) * Decimal("10")
    assert engine.risk._current_equity == expected
    assert expected > Decimal("10000")
    assert engine.risk.current_drawdown == Decimal("0")
    assert engine.risk._high_water_mark == expected


def test_sync_equity_absolute_no_double_count(tmp_path):
    """set_capital — абсолютное значение: += pnl в record_trade не копится."""
    b = _broker(tmp_path)
    pos = _open_long(b, Decimal("100"))
    engine = _engine_with(b, tmp_path)
    b.update_mark_price("TEST-USDT", Decimal("102"))
    engine._sync_risk_equity()
    base = engine.risk._current_equity
    engine.risk.record_trade(
        symbol="TEST-USDT", side="long",
        entry_price=Decimal("100"), quantity=Decimal("1"),
        pnl=Decimal("5"), won=True,
    )
    # После record_trade внутри шага equity временно +5, но следующий
    # sync перезаписывает из брокера (источник правды) — без двойного счёта.
    assert engine.risk._current_equity == base + Decimal("5")
    engine._sync_risk_equity()
    assert engine.risk._current_equity == base
