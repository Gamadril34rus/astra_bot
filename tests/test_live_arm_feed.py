"""Arm feed: row format, signal_id dedup, freshness ts on write."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal

from astra_bot.core.models import TradeDirection
from astra_bot.decision.live_arm_feed import (
    make_signal_id,
    produce_pending,
    signal_to_row,
    write_pending,
)
from astra_bot.decision.live_arm_wiring import is_fresh, load_pending_rows
from astra_bot.strategies.base import Signal, SignalType


def _sig(symbol="BTC-USDT", side="long", entry=100, stop=95, tp=110, conf=0.7):
    direction = TradeDirection.LONG if side == "long" else TradeDirection.SHORT
    return Signal(
        symbol=symbol,
        strategy_name="zeus_wedge_retest",
        signal_type=SignalType.MEAN_REVERSION,
        direction=direction,
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(stop)),
        take_profit=Decimal(str(tp)),
        position_size=Decimal("0"),
        risk_amount=Decimal("0"),
        confidence=conf,
        market_regime="UNKNOWN",
        features={},
    )


def test_row_format():
    row = signal_to_row(_sig(), bar_ts=1_700_000_000, now=1_700_000_100.0)
    assert row["signal_id"] == make_signal_id("BTC-USDT", "long", 1_700_000_000)
    assert row["symbol"] == "BTC-USDT"
    assert row["side"] == "long"
    assert row["entry"] == 100.0
    assert row["stop"] == 95.0
    assert row["tp"] == 110.0
    assert row["retest_ts"] == 1_700_000_000
    assert row["ts"] == 1_700_000_100.0
    assert row["strategy"] == "zeus_wedge_retest"
    assert row["confidence"] == 0.7
    assert is_fresh(row, max_sec=600, now=1_700_000_100.0 + 60)


def test_signal_id_one_per_symbol_bar():
    a = make_signal_id("ETH-USDT", "short", 42)
    b = make_signal_id("ETH-USDT", "short", 42)
    c = make_signal_id("ETH-USDT", "short", 43)
    assert a == b and a != c


def test_write_and_load_pending(tmp_path):
    path = tmp_path / "pending.jsonl"
    rows = [signal_to_row(_sig(), bar_ts=100, now=time.time())]
    write_pending(rows, path)
    loaded = load_pending_rows(path)
    assert len(loaded) == 1
    assert loaded[0]["signal_id"] == rows[0]["signal_id"]


def test_produce_dedup_seen_ids(tmp_path, monkeypatch):
    async def _():
        path = tmp_path / "pending.jsonl"
        bar_ts = 1_700_000_000
        sid = make_signal_id("BTC-USDT", "long", bar_ts)

        class FakeClient:
            async def get_candles(self, symbol, timeframe, limit=120):
                return []

        import astra_bot.decision.live_arm_feed as feed

        async def fake_scan(client, symbol, strategy=None):
            if symbol != "BTC-USDT":
                return None
            return signal_to_row(_sig(symbol=symbol), bar_ts=bar_ts, now=time.time())

        monkeypatch.setattr(feed, "scan_symbol", fake_scan)
        out1 = await produce_pending(
            FakeClient(), ["BTC-USDT", "ETH-USDT"], path=path, seen_ids=set()
        )
        assert len(out1) == 1
        out2 = await produce_pending(
            FakeClient(), ["BTC-USDT"], path=path, seen_ids={sid}
        )
        assert out2 == []
        assert load_pending_rows(path) == []
    asyncio.run(_())


def test_stale_not_fresh():
    row = signal_to_row(_sig(), bar_ts=1, now=time.time() - 3600)
    assert not is_fresh(row, max_sec=600)
