from decimal import Decimal
from unittest.mock import MagicMock

from astra_bot.core.state import RiskState
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


def test_halt_alert_notification_deduplicated(tmp_path):
    mock_notifier = MagicMock()
    engine = TradingEngine(
        exchange=MagicMock(),
        pipeline=MagicMock(),
        notifier=mock_notifier,
        config=TradingEngineConfig(
            halt_alerts_path=str(tmp_path / "halt_alerts.json")
        ),
    )

    # Force RiskEngine into EMERGENCY state and trading_enabled = False
    engine.risk.risk_state = RiskState.EMERGENCY
    engine.risk.trading_enabled = False

    cand = MagicMock()
    cand.entry_price = Decimal("100")
    cand.stop_loss = Decimal("95")
    cand.take_profit = Decimal("110")
    cand.strategy = "test"

    # First check_trade call
    res1 = engine._risk_check_and_adjust("BTC-USDT", "long", cand, Decimal("1"))
    assert res1 is None
    assert mock_notifier.call_count == 1
    call_args = mock_notifier.call_args[0]
    assert "EMERGENCY" in call_args[0]

    # Second check_trade call (deduplicated: should not call notifier again)
    res2 = engine._risk_check_and_adjust("ETH-USDT", "long", cand, Decimal("1"))
    assert res2 is None
    assert mock_notifier.call_count == 1
