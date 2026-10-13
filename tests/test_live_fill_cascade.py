"""Cascade fill sources for exit PnL."""
from __future__ import annotations

from astra_bot.decision.live_broker import _aggregate_exit_fills


def test_aggregate_close_side_only_and_fees() -> None:
    fills = [
        {"price": "100", "qty": "1", "fee": "0.05", "side": "BUY", "type": "MARKET", "realizedPnl": "0"},
        {"price": "90", "qty": "1", "fee": "0.04", "side": "SELL", "type": "STOP_MARKET", "realizedPnl": "-10.09"},
    ]
    avg, fees, reason, q = _aggregate_exit_fills(fills, side="long", entry_px=100.0)
    assert avg == 90.0
    assert reason == "стоп"
    assert q == 1.0
    assert abs(fees - 0.09) < 1e-9


def test_aggregate_empty_window() -> None:
    avg, _fees, _reason, _q = _aggregate_exit_fills([], side="short", entry_px=1.0)
    assert avg is None
