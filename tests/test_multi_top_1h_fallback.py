"""multi-top: try 2–3 tops; 4h primary + 1h fallback (owner continuation dumps)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astra_bot.decision.live_arm_feed import scan_symbol
from astra_bot.strategies.zeus_multi_top_breakdown import detect_multi_top_breakdown


def _series_two_flat_tops():
    """OHLC lists: two flat swing highs then first close under neckline."""
    h, l, c = [], [], []
    for _ in range(20):
        h.append(100.5)
        l.append(99.5)
        c.append(100.0)
    # top1 peak
    h += [101, 105.0, 103]
    l += [99.5, 100.5, 100]
    c += [100.5, 104, 101]
    # trough
    h += [101, 99, 99.5]
    l += [98, 96.5, 97]
    c += [98.5, 97, 98]
    # top2 near top1
    h += [101, 104.8, 103]
    l += [97.5, 100, 99.5]
    c += [100.5, 103.5, 100]
    # pre-break above neck
    h.append(100)
    l.append(96.8)
    c.append(97.5)
    # break
    h.append(97)
    l.append(94)
    c.append(94.5)
    return h, l, c


def _candles_from_hlc(h, l, c, step_ms=3_600_000):
    bars = []
    ts0 = 1_700_000_000_000
    for i, (hi, lo, cl) in enumerate(zip(h, l, c)):
        bars.append(
            SimpleNamespace(
                open=cl,
                high=hi,
                low=lo,
                close=cl,
                open_time=ts0 + i * step_ms,
            )
        )
    return bars


def test_detect_prefers_2_when_3rd_not_flat():
    h, l, c = _series_two_flat_tops()
    sig = detect_multi_top_breakdown(h, l, c, min_tops=2, max_tops=3, radius=2)
    assert sig is not None
    assert sig.side == "short"
    assert sig.n_tops == 2


@pytest.mark.asyncio
async def test_scan_1h_fallback_when_4h_silent(monkeypatch):
    h, l, c = _series_two_flat_tops()
    bars_1h = _candles_from_hlc(h, l, c, step_ms=3_600_000)
    # flat 4h — no breakdown
    flat_4h = [
        SimpleNamespace(open=100, high=101, low=99, close=100, open_time=1_700_000_000_000 + i * 14_400_000)
        for i in range(40)
    ]

    async def fake_candles(client, symbol, *, timeframe="4h", limit=120):
        if timeframe == "4h":
            return flat_4h
        if timeframe == "1h":
            return bars_1h
        return []

    class Silent:
        async def evaluate(self, symbol, series):
            return None

    monkeypatch.setattr("astra_bot.decision.live_arm_feed.fetch_candles", fake_candles)
    monkeypatch.setattr("astra_bot.decision.live_arm_feed._channel_strategy", lambda: Silent())
    row = await scan_symbol(MagicMock(), "BTC-USDT", strategy=Silent())
    assert row is not None
    assert row["strategy"] == "zeus_multi_top_breakdown"
    assert row["side"] == "short"
    assert row.get("tf") == "1h"
    assert row.get("n_tops", 0) >= 2


@pytest.mark.asyncio
async def test_scan_prefers_4h_when_present(monkeypatch):
    h, l, c = _series_two_flat_tops()
    bars = _candles_from_hlc(h, l, c, step_ms=14_400_000)

    async def fake_candles(client, symbol, *, timeframe="4h", limit=120):
        return bars

    class Silent:
        async def evaluate(self, symbol, series):
            return None

    monkeypatch.setattr("astra_bot.decision.live_arm_feed.fetch_candles", fake_candles)
    monkeypatch.setattr("astra_bot.decision.live_arm_feed._channel_strategy", lambda: Silent())
    row = await scan_symbol(MagicMock(), "BTC-USDT", strategy=Silent())
    assert row is not None
    assert row.get("tf") == "4h"
