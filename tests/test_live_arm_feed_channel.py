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
    """Owner AUDIT 798/800/801: BTC ETH SOL HYPE INJ."""
    assert len(FEED_SYMBOLS) == 5
    assert FEED_SYMBOLS == (
        "BTC-USDT",
        "ETH-USDT",
        "SOL-USDT",
        "HYPE-USDT",
        "INJ-USDT",
    )
    assert "AVAX-USDT" not in FEED_SYMBOLS
    assert "DOT-USDT" not in FEED_SYMBOLS


def test_paper_channel_config_capital_parity():
    c = paper_channel_config()
    assert c.enabled is True
    assert c.lookback == 48
    assert c.min_width_pct == 0.01
    assert c.max_width_pct == 0.12
    assert c.min_rr == 2.0
    assert c.max_hold_bars == 5
    assert c.tp2_rr == 2.0
    assert c.min_stop_pct == 0.01
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


def test_zone_trade_filter_blocks_only_zones_before_planned_target():
    from astra_bot.decision.live_arm_feed import _zone_trade_filter_decision

    long_row = {"side": "long", "entry": 100.0, "stop": 95.0, "tp": 120.0}
    blocked_long = _zone_trade_filter_decision(
        long_row, [{"lower": 104.0, "upper": 106.0}]
    )
    assert not blocked_long["accepted"]
    assert blocked_long["state"] == "ZONE_BEFORE_TARGET"
    assert blocked_long["room_r"] == 0.8
    assert blocked_long["target_r"] == 4.0

    clear_long = _zone_trade_filter_decision(
        long_row, [{"lower": 122.0, "upper": 124.0}]
    )
    assert clear_long["accepted"]
    assert clear_long["state"] == "TARGET_BEFORE_ZONE"

    short_row = {"side": "short", "entry": 100.0, "stop": 105.0, "tp": 80.0}
    blocked_short = _zone_trade_filter_decision(
        short_row, [{"lower": 94.0, "upper": 96.0}]
    )
    assert not blocked_short["accepted"]
    assert blocked_short["boundary"] == 96.0
    assert blocked_short["room_r"] == 0.8


def test_zone_trade_filter_uses_far_edge_when_entry_is_inside_zone():
    from astra_bot.decision.live_arm_feed import _zone_trade_filter_decision

    row = {"side": "long", "entry": 100.0, "stop": 95.0, "tp": 120.0}
    decision = _zone_trade_filter_decision(
        row, [{"lower": 98.0, "upper": 103.0}]
    )
    assert not decision["accepted"]
    assert decision["boundary"] == 103.0
    assert decision["room_r"] == 0.6


def test_zone_trade_filter_fails_closed_on_empty_but_passes_when_no_zone_ahead():
    from astra_bot.decision.live_arm_feed import _zone_trade_filter_decision

    row = {"side": "long", "entry": 100.0, "stop": 95.0, "tp": 110.0}
    empty = _zone_trade_filter_decision(row, [])
    assert not empty["accepted"]
    assert empty["state"] == "UNKNOWN"

    behind = _zone_trade_filter_decision(
        row, [{"lower": 90.0, "upper": 95.0}]
    )
    assert behind["accepted"]
    assert behind["state"] == "NO_OPPOSING_ZONE"


def test_zone_trade_filter_fails_closed_on_unknown_or_invalid_candidate():
    from astra_bot.decision.live_arm_feed import _zone_trade_filter_decision

    row = {"side": "long", "entry": 100.0, "stop": 95.0, "tp": 120.0}
    missing = _zone_trade_filter_decision(row, None)
    assert not missing["accepted"]
    assert missing["state"] == "UNKNOWN"

    malformed = _zone_trade_filter_decision(row, [{"lower": 102.0}])
    assert not malformed["accepted"]
    assert malformed["state"] == "UNKNOWN"

    invalid = _zone_trade_filter_decision(
        {"side": "long", "entry": 100.0, "stop": 105.0, "tp": 120.0}, []
    )
    assert not invalid["accepted"]
    assert invalid["state"] == "UNKNOWN"
