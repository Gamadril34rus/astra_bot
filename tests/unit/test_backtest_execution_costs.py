from decimal import Decimal
from datetime import UTC, datetime

from astra_bot.backtester.engine import BacktestConfig, BacktestEngine, Trade


def _engine() -> BacktestEngine:
    return BacktestEngine(
        BacktestConfig(
            initial_capital=Decimal("1000"),
            maker_fee_rate=Decimal("0.001"),
            taker_fee_rate=Decimal("0.002"),
            slippage_percent=Decimal("0.001"),
            min_notional=Decimal("1"),
        )
    )


def test_long_close_charges_exit_fee_and_adverse_slippage():
    engine = _engine()
    trade = Trade(
        id=1,
        entry_time=datetime(2026, 1, 1, tzinfo=UTC),
        entry_price=Decimal("100.1"),  # 100 + 0.1% entry slippage
        side="long",
        quantity=Decimal("1"),
        fees=Decimal("0.1001"),
        slippage=Decimal("0.1"),
        stop_loss=Decimal("90"),
    )
    engine._open_positions[trade.id] = trade

    engine._close_position(
        trade.id,
        "take_profit",
        exit_price=Decimal("110"),
        timestamp=1767225600000,
    )

    # Exit fill is 109.89 after 0.1% adverse slippage.
    expected_exit = Decimal("109.89")
    expected_exit_fee = expected_exit * Decimal("0.002")
    expected_pnl = (expected_exit - Decimal("100.1")) - Decimal("0.1001") - expected_exit_fee

    assert trade.exit_price == expected_exit
    assert trade.fees == Decimal("0.1001") + expected_exit_fee
    assert trade.slippage == Decimal("0.1") + Decimal("0.11")
    assert trade.pnl == expected_pnl
    assert engine._realized_equity == Decimal("1000") + expected_pnl


def test_short_close_charges_exit_fee_and_adverse_slippage():
    engine = _engine()
    trade = Trade(
        id=2,
        entry_time=datetime(2026, 1, 1, tzinfo=UTC),
        entry_price=Decimal("99.9"),  # 100 - 0.1% entry slippage
        side="short",
        quantity=Decimal("1"),
        fees=Decimal("0.0999"),
        slippage=Decimal("0.1"),
        stop_loss=Decimal("110"),
    )
    engine._open_positions[trade.id] = trade

    engine._close_position(
        trade.id,
        "take_profit",
        exit_price=Decimal("90"),
        timestamp=1767225600000,
    )

    # Exit fill is 90.09 after 0.1% adverse slippage.
    expected_exit = Decimal("90.09")
    expected_exit_fee = expected_exit * Decimal("0.002")
    expected_pnl = (Decimal("99.9") - expected_exit) - Decimal("0.0999") - expected_exit_fee

    assert trade.exit_price == expected_exit
    assert trade.fees == Decimal("0.0999") + expected_exit_fee
    assert trade.slippage == Decimal("0.1") + Decimal("0.09")
    assert trade.pnl == expected_pnl


def test_entry_slippage_is_included_in_risk_sizing():
    engine = _engine()
    result = engine._risk_engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=Decimal("100.1"),
        stop_loss=Decimal("90"),
    )

    assert result.accepted
    # Risk distance is 10.1, not the pre-slippage 10.0.
    assert result.stop_distance == Decimal("10.1")
