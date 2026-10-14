"""B-001: extremes from entry_bar_ts; gap stop fill; no phantom pre-entry stop."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from astra_bot.decision.broker import PaperBroker

T0 = 1_700_000_000_000
T1 = T0 + 60_000
T2 = T0 + 120_000


def _bar(symbol, o, h, l, c, ts):
    return SimpleNamespace(symbol=symbol, open=o, high=h, low=l, close=c, open_time=ts)


def test_pre_entry_high_does_not_update_extremes(tmp_path) -> None:
    br = PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "tr.jsonl",
        initial_capital=Decimal("1000"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )
    pos = br.open_position(
        symbol="BTC-USDT",
        direction="short",
        entry_price=Decimal("100"),
        stop_loss=Decimal("105"),
        take_profit=Decimal("90"),
        quantity=Decimal("1"),
        entry_bar_ts=T1,
    )
    pos.highest_price = pos.entry_price
    pos.lowest_price = pos.entry_price
    # Pre-entry bar must not move extremes
    br.update_extremes(_bar("BTC-USDT", 100, 120, 99, 101, ts=T0))
    assert pos.highest_price == pos.entry_price
    closed = br.check_exits(_bar("BTC-USDT", 100, 103, 98, 99, ts=T2))
    assert closed == []
    assert pos.highest_price <= Decimal("103")


def test_gap_stop_fill_long_min_open(tmp_path) -> None:
    br = PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "tr.jsonl",
        initial_capital=Decimal("1000"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )
    br.open_position(
        symbol="ETH-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        quantity=Decimal("1"),
        entry_bar_ts=T0,
    )
    closed = br.check_exits(_bar("ETH-USDT", 90, 92, 88, 91, ts=T1))
    assert len(closed) == 1
    assert closed[0].exit_price == 90.0
