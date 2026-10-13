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


def test_tg_dedup_none_sid_uses_ts(tmp_path: Path) -> None:
    p = tmp_path / "flags.json"
    # same ts → dedup
    assert try_claim("BTC-USDT", "entry", None, path=p, ts=1000) is True
    assert try_claim("BTC-USDT", "entry", None, path=p, ts=1000) is False
    # new ts → allowed (re-entry)
    assert try_claim("BTC-USDT", "entry", None, path=p, ts=2000) is True


def test_aggregate_exit_fills_stop() -> None:
    fills = [
        {"price": "110", "qty": "2", "commission": "0.02", "side": "BUY", "type": "MARKET"},
        {"price": "100", "qty": "2", "commission": "0.01", "side": "SELL", "type": "STOP_MARKET"},
    ]
    avg, fees, reason, q = _aggregate_exit_fills(fills, side="long", entry_px=110.0)
    assert avg == 100.0
    assert reason == "стоп"
    assert q == 2.0
    # open 0.02 + close 0.01
    assert abs(fees - 0.03) < 1e-9


def test_aggregate_ignores_open_side_in_avg() -> None:
    fills = [
        {"price": "50", "qty": "10", "commission": "0.1", "side": "SELL", "type": "MARKET"},
        {"price": "40", "qty": "10", "commission": "0.1", "side": "BUY", "type": "TAKE_PROFIT_MARKET"},
    ]
    # short: close side is BUY
    avg, fees, reason, q = _aggregate_exit_fills(fills, side="short", entry_px=50.0)
    assert avg == 40.0
    assert q == 10.0
    assert reason == "тейк"
    assert abs(fees - 0.2) < 1e-9


def test_aggregate_empty() -> None:
    avg, fees, reason, q = _aggregate_exit_fills([], side="short", entry_px=1.0)
    assert avg is None
