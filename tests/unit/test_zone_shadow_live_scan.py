from __future__ import annotations

from types import SimpleNamespace

import pytest
from astra_bot.decision import live_arm_feed as feed


class _Client:
    def __init__(self):
        self.calls = []

    async def get_candles(self, symbol, timeframe, *, limit):
        self.calls.append((symbol, timeframe, limit))
        return [
            SimpleNamespace(
                open_time=1_800_000_000_000 + i * 14_400_000,
                high=101.0 + i,
                low=99.0 + i,
                close=100.0 + i,
                quote_volume=1000.0 + i,
            )
            for i in range(40)
        ]


@pytest.mark.asyncio
async def test_zone_shadow_scans_symbols_outside_entry_gate(monkeypatch, tmp_path):
    from astra_bot.decision import live_arm_wiring, zone_shadow

    events = []
    built = []
    zones = [{"lower": 99.0, "upper": 101.0, "center": 100.0,
              "quote_volume": 10.0, "width": 2.0}]
    monkeypatch.setenv("ZONE_SHADOW", "1")
    monkeypatch.setattr(feed, "_ZONE_CACHE", {})
    monkeypatch.setattr(feed, "_ZONE_APPROACH_PATH", tmp_path / "approach.json")
    monkeypatch.setattr(live_arm_wiring, "append_journal", events.append)
    monkeypatch.setattr(
        zone_shadow, "build_volume_zones",
        lambda candles, price: built.append(price) or zones,
    )
    monkeypatch.setattr(
        zone_shadow, "zone_context",
        lambda price, current_zones: {
            "above": None, "below": None, "in_zone": True,
            "zone": current_zones[0],
        },
    )
    client = _Client()

    first = await feed.scan_zone_shadow(client, ("ETH-USDT", "SOL-USDT"))
    second = await feed.scan_zone_shadow(client, ("ETH-USDT", "SOL-USDT"))

    assert set(first) == {"ETH-USDT", "SOL-USDT"}
    assert set(second) == {"ETH-USDT", "SOL-USDT"}
    assert len(client.calls) == 4
    assert all(call[1:] == ("4h", 600) for call in client.calls)
    assert len(built) == 2  # cached until a new closed 4h candle
    assert [event["event"] for event in events] == ["zone_shadow"] * 4
    assert events[0]["symbol"] == "ETH-USDT"
    assert events[0]["in_zone"] is True
    assert events[0]["zones"][0]["min_distance_pct"] == 0.0


@pytest.mark.asyncio
async def test_zone_shadow_errors_are_journaled_and_swallowed(monkeypatch, tmp_path):
    from astra_bot.decision import live_arm_wiring

    events = []
    monkeypatch.setenv("ZONE_SHADOW", "1")
    monkeypatch.setattr(feed, "_ZONE_CACHE", {})
    monkeypatch.setattr(feed, "_ZONE_APPROACH_PATH", tmp_path / "approach.json")
    monkeypatch.setattr(live_arm_wiring, "append_journal", events.append)

    class BrokenClient:
        async def get_candles(self, symbol, timeframe, *, limit):
            raise RuntimeError("offline")

    result = await feed.scan_zone_shadow(BrokenClient(), ("BTC-USDT",))

    assert result == {}
    assert events[0]["event"] == "zone_shadow_error"
    assert events[0]["symbol"] == "BTC-USDT"
    assert events[0]["error"] == "RuntimeError"



@pytest.mark.asyncio
async def test_zone_shadow_minimum_resets_and_summarizes_once_on_new_bar(monkeypatch, tmp_path):
    from astra_bot.decision import live_arm_wiring, zone_shadow

    events = []
    monkeypatch.setenv("ZONE_SHADOW", "1")
    monkeypatch.setattr(feed, "_ZONE_CACHE", {})
    monkeypatch.setattr(feed, "_ZONE_APPROACH_PATH", tmp_path / "approach.json")
    monkeypatch.setattr(live_arm_wiring, "append_journal", events.append)
    zones = [{"lower": 99.0, "upper": 101.0, "center": 100.0,
              "quote_volume": 10.0, "width": 2.0}]
    monkeypatch.setattr(zone_shadow, "build_volume_zones", lambda candles, price: zones)
    monkeypatch.setattr(zone_shadow, "zone_context", lambda price, current_zones: {
        "above": None, "below": None, "in_zone": False, "zone": None,
    })

    class MovingClient:
        def __init__(self):
            self.base_ts = 1_800_000_000_000
            self.price = 100.0

        async def get_candles(self, symbol, timeframe, *, limit):
            return [
                SimpleNamespace(
                    open_time=self.base_ts + i * 14_400_000,
                    high=self.price + 1, low=self.price - 1,
                    close=self.price, quote_volume=1000.0,
                )
                for i in range(40)
            ]

    client = MovingClient()
    await feed.scan_zone_shadow(client, ("ETH-USDT",))
    assert events[-1]["zones"][0]["min_distance_pct"] == 0.0

    client.price = 110.0
    await feed.scan_zone_shadow(client, ("ETH-USDT",))
    assert events[-1]["zones"][0]["min_distance_pct"] == 0.0

    old_bar_ts = client.base_ts + 38 * 14_400_000
    client.base_ts += 14_400_000
    client.price = 120.0
    await feed.scan_zone_shadow(client, ("ETH-USDT",))
    summaries = [event for event in events if event["event"] == "zone_shadow_summary"]
    assert len(summaries) == 1
    assert summaries[0]["bar_ts"] == old_bar_ts
    assert summaries[0]["zones"] == [{"center": 100.0, "min_distance_pct": 0.0}]
    assert events[-1]["zones"][0]["min_distance_pct"] == 16.6667

    client.price = 125.0
    await feed.scan_zone_shadow(client, ("ETH-USDT",))
    assert len([event for event in events if event["event"] == "zone_shadow_summary"]) == 1
    assert events[-1]["zones"][0]["min_distance_pct"] == 16.0


@pytest.mark.asyncio
async def test_zone_shadow_empty_zones_are_skipped_without_error(monkeypatch, tmp_path):
    from astra_bot.decision import live_arm_wiring, zone_shadow

    events = []
    monkeypatch.setenv("ZONE_SHADOW", "1")
    monkeypatch.setattr(feed, "_ZONE_CACHE", {})
    monkeypatch.setattr(feed, "_ZONE_APPROACH_PATH", tmp_path / "approach.json")
    monkeypatch.setattr(live_arm_wiring, "append_journal", events.append)
    monkeypatch.setattr(zone_shadow, "build_volume_zones", lambda candles, price: [])
    monkeypatch.setattr(zone_shadow, "zone_context", lambda price, zones: {
        "above": None, "below": None, "in_zone": False, "zone": None,
    })

    await feed.scan_zone_shadow(_Client(), ("INJ-USDT",))
    assert [event["event"] for event in events] == ["zone_shadow"]
    assert events[0]["zones"] == []


@pytest.mark.asyncio
async def test_zone_shadow_flag_off_does_not_fetch(monkeypatch):
    monkeypatch.setenv("ZONE_SHADOW", "0")

    class NoFetchClient:
        async def get_candles(self, *args, **kwargs):
            raise AssertionError("must not fetch while disabled")

    assert await feed.scan_zone_shadow(NoFetchClient(), ("BTC-USDT",)) == {}
