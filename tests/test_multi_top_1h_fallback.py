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
    # Last candle is still forming; the confirmed break is the previous bar.
    forming = SimpleNamespace(
        open=96.8, high=97.0, low=96.0, close=96.8,
        open_time=bars_1h[-1].open_time + 3_600_000,
    )
    bars_1h.append(forming)
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
        async def evaluate(self, symbol, series, current_price=None):
            return None

    monkeypatch.setattr("astra_bot.decision.live_arm_feed.fetch_candles", fake_candles)
    monkeypatch.setattr("astra_bot.decision.live_arm_feed._channel_strategy", lambda: Silent())
    row = await scan_symbol(MagicMock(), "BTC-USDT", strategy=Silent())
    assert row is not None
    assert row["strategy"] == "zeus_multi_top_breakdown"
    assert row["side"] == "short"
    assert row.get("tf") == "1h"
    assert row.get("n_tops", 0) >= 2
    assert row["retest_ts"] == bars_1h[-2].open_time
    assert row["entry"] == pytest.approx(96.8)


@pytest.mark.asyncio
async def test_scan_prefers_4h_when_present(monkeypatch):
    h, l, c = _series_two_flat_tops()
    bars = _candles_from_hlc(h, l, c, step_ms=14_400_000)
    forming = SimpleNamespace(
        open=96.8, high=97.0, low=96.0, close=96.8,
        open_time=bars[-1].open_time + 14_400_000,
    )
    bars.append(forming)

    async def fake_candles(client, symbol, *, timeframe="4h", limit=120):
        return bars

    class Silent:
        async def evaluate(self, symbol, series, current_price=None):
            return None

    monkeypatch.setattr("astra_bot.decision.live_arm_feed.fetch_candles", fake_candles)
    monkeypatch.setattr("astra_bot.decision.live_arm_feed._channel_strategy", lambda: Silent())
    row = await scan_symbol(MagicMock(), "BTC-USDT", strategy=Silent())
    assert row is not None
    assert row.get("tf") == "4h"
    assert row["retest_ts"] == bars[-2].open_time
    assert row["entry"] == pytest.approx(96.8)



@pytest.mark.asyncio
async def test_intrabar_break_without_closed_break_is_not_a_signal(monkeypatch):
    h, l, c = _series_two_flat_tops()
    # Keep the pre-break candle closed; the only break is in the forming bar.
    h, l, c = h[:-1], l[:-1], c[:-1]
    closed_bars = _candles_from_hlc(h, l, c, step_ms=3_600_000)
    forming_break = SimpleNamespace(
        open=97.0, high=97.0, low=94.0, close=94.5,
        open_time=closed_bars[-1].open_time + 3_600_000,
    )
    bars = closed_bars + [forming_break]

    async def fake_candles(client, symbol, *, timeframe="4h", limit=120):
        return bars

    class Silent:
        async def evaluate(self, symbol, series, current_price=None):
            return None

    monkeypatch.setattr("astra_bot.decision.live_arm_feed.fetch_candles", fake_candles)
    monkeypatch.setattr("astra_bot.decision.live_arm_feed._channel_strategy", lambda: Silent())
    row = await scan_symbol(MagicMock(), "BTC-USDT", strategy=Silent())
    assert row is None



@pytest.mark.asyncio
async def test_channel_evaluate_receives_closed_bars_and_live_price(monkeypatch):
    bars = [
        SimpleNamespace(
            open=100.0, high=101.0, low=99.0, close=100.0,
            open_time=1_700_000_000_000 + i * 14_400_000,
        )
        for i in range(40)
    ]
    bars[-1].close = 100.25
    observed = []

    class Probe:
        async def evaluate(self, symbol, series, current_price=None):
            observed.append((len(series), current_price))
            return None

        def diagnose(self, series, current_price=None):
            return {"would_signal": False}

    async def fake_candles(client, symbol, *, timeframe="4h", limit=120):
        return bars

    monkeypatch.setattr("astra_bot.decision.live_arm_feed.fetch_candles", fake_candles)
    monkeypatch.setattr("astra_bot.decision.live_arm_feed._channel_strategy", lambda: Probe())
    row = await scan_symbol(MagicMock(), "BTC-USDT", strategy=Probe())
    assert row is None
    assert observed == [(39, 100.25)]
