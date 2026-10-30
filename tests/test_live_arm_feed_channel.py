"""Arm feed (channel): row format, signal_id, pending I/O, dedup, freshness."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal

from astra_bot.core.models import TradeDirection
from astra_bot.decision.live_arm_feed import (
    FEED_SYMBOLS,
    STRATEGY_NAME,
    make_signal_id,
    paper_channel_config,
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
        strategy_name=STRATEGY_NAME,
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


def test_feed_symbols_match_paper_clock():
    # Owner 30.10: majors + INJ/AVAX/HYPE/ZEC/TOTAL/NEAR
    assert len(FEED_SYMBOLS) == 11
    assert FEED_SYMBOLS[0] == "BTC-USDT"
    assert "BTC-USDT" in FEED_SYMBOLS and "ETH-USDT" in FEED_SYMBOLS
    assert "INJ-USDT" in FEED_SYMBOLS and "AVAX-USDT" in FEED_SYMBOLS
    assert "DOT-USDT" not in FEED_SYMBOLS
    assert "PENDLE-USDT" not in FEED_SYMBOLS


def test_paper_channel_config_capital_parity():
    c = paper_channel_config()
    assert c.enabled is True
    assert c.lookback == 40
    assert c.min_width_pct == 0.01
    assert c.max_width_pct == 0.12
    assert c.min_rr == 2.0
    assert c.max_hold_bars == 5
    assert c.tp2_rr == 2.0
    assert c.min_stop_pct == 0.008
    assert c.require_htf_bias is True


def test_row_format_channel():
    row = signal_to_row(_sig(), bar_ts=1_700_000_000, now=1_700_000_100.0)
    assert row["signal_id"].startswith("zeus_channel_boundary|")
    assert row["signal_id"] == make_signal_id("BTC-USDT", "long", 1_700_000_000)
    assert row["strategy"] == "zeus_channel_boundary"
    assert row["side"] == "long"
    assert row["entry"] == 100.0 and row["stop"] == 95.0
    assert is_fresh(row, max_sec=600, now=1_700_000_100.0 + 60)


def test_write_and_load_pending(tmp_path):
    path = tmp_path / "pending.jsonl"
    rows = [signal_to_row(_sig(), bar_ts=100, now=time.time())]
    write_pending(rows, path)
    loaded = load_pending_rows(path)
    assert len(loaded) == 1
    assert loaded[0]["strategy"] == "zeus_channel_boundary"


def test_produce_dedup_seen_ids(tmp_path, monkeypatch):
    async def _():
        path = tmp_path / "pending.jsonl"
        bar_ts = 1_700_000_000
        sid = make_signal_id("BTC-USDT", "long", bar_ts)
        import astra_bot.decision.live_arm_feed as feed

        async def fake_scan(client, symbol, strategy=None):
            if symbol != "BTC-USDT":
                return None
            return signal_to_row(_sig(symbol=symbol), bar_ts=bar_ts, now=time.time())

        monkeypatch.setattr(feed, "scan_symbol", fake_scan)

        class FakeClient:
            async def get_candles(self, *a, **k):
                return []

        out1 = await produce_pending(
            FakeClient(), ["BTC-USDT", "ETH-USDT"], path=path, seen_ids=set()
        )
        assert len(out1) == 1
        out2 = await produce_pending(
            FakeClient(), ["BTC-USDT"], path=path, seen_ids={sid}
        )
        assert out2 == []
    asyncio.run(_())


def test_stale_not_fresh():
    row = signal_to_row(_sig(), bar_ts=1, now=time.time() - 3600)
    assert not is_fresh(row, max_sec=600)
