"""P0 exit fill aggregate + TG dedup flags."""
from __future__ import annotations

from pathlib import Path

from astra_bot.decision.live_broker import _aggregate_exit_fills
from astra_bot.decision.live_tg_flags import already_sent, try_claim


def test_tg_dedup(tmp_path: Path) -> None:
    p = tmp_path / "flags.json"
    assert try_claim("ETH-USDT", "exit", "sig1", path=p) is True
    assert try_claim("ETH-USDT", "exit", "sig1", path=p) is False
    assert already_sent("ETH-USDT", "exit", "sig1", path=p) is True
    assert try_claim("ETH-USDT", "exit", "sig2", path=p) is True


def test_aggregate_exit_fills_stop() -> None:
    fills = [
        {"price": "100", "qty": "2", "commission": "0.01", "side": "SELL", "type": "STOP_MARKET"},
    ]
    avg, fees, reason, q = _aggregate_exit_fills(fills, side="long", entry_px=110.0)
    assert avg == 100.0
    assert reason == "стоп"
    assert q == 2.0
    assert fees >= 0.01


def test_aggregate_empty() -> None:
    avg, fees, reason, q = _aggregate_exit_fills([], side="short", entry_px=1.0)
    assert avg is None
