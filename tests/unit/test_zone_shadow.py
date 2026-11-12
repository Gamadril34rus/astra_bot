from astra_bot.decision.zone_shadow import (
    build_volume_zones,
    build_volume_zones_with_fallback,
    zone_context,
)


def test_volume_profile_finds_known_high_volume_price_area():
    candles = []
    for i in range(24):
        price = 90.0 + i
        volume = 100.0
        if 10 <= i <= 12:
            volume = 10000.0
        candles.append({
            "high": price + 0.2, "low": price - 0.2,
            "close": price, "quote_volume": volume,
        })
    # Forming candle is ignored by design.
    candles.append({"high": 113, "low": 112, "close": 112.5, "quote_volume": 1})
    zones = build_volume_zones(candles, price=101.0)
    assert zones
    assert any(99.0 <= zone["center"] <= 104.0 for zone in zones)


def test_zone_context_reports_nearest_sides_and_inside():
    zones = [
        {"lower": 99.0, "upper": 101.0, "center": 100.0, "quote_volume": 10.0, "width": 2.0},
        {"lower": 109.0, "upper": 111.0, "center": 110.0, "quote_volume": 20.0, "width": 2.0},
    ]
    ctx = zone_context(100.0, zones)
    assert ctx["in_zone"] is True
    assert ctx["above"]["center"] == 110.0
    assert ctx["below"] is None


def test_zero_quote_volume_falls_back_to_base_volume():
    candles = []
    for i in range(24):
        price = 90.0 + i
        base_volume = 100.0
        if 10 <= i <= 12:
            base_volume = 10000.0
        candles.append({
            "high": price + 0.2, "low": price - 0.2,
            "close": price, "quote_volume": 0.0,
            "volume": base_volume,
        })
    # Forming candle is ignored by design.
    candles.append({
        "high": 113.0, "low": 112.0, "close": 112.5,
        "quote_volume": 0.0, "volume": 1.0,
    })

    zones = build_volume_zones(candles, price=101.0)

    assert zones
    assert any(99.0 <= zone["center"] <= 104.0 for zone in zones)


def test_recent_fallback_is_used_only_when_full_profile_has_no_zones(monkeypatch):
    import astra_bot.decision.zone_shadow as module

    calls = []
    expected = [{
        "lower": 71.0, "upper": 72.0, "center": 71.5,
        "quote_volume": 1000.0, "width": 1.0,
    }]

    def fake_build(candles, **kwargs):
        calls.append(len(candles))
        return [] if len(candles) > 120 else expected

    monkeypatch.setattr(module, "build_volume_zones", fake_build)
    candles = [{"close": 70.0}] * 500
    zones, meta = build_volume_zones_with_fallback(
        candles, price=71.0, fallback_lookback=120,
    )

    assert calls == [500, 120]
    assert zones == expected
    assert meta == {
        "source": "recent_fallback",
        "lookback_bars": 119,
        "full_lookback_bars": 499,
    }


def test_full_profile_prevents_unnecessary_recent_fallback(monkeypatch):
    import astra_bot.decision.zone_shadow as module

    calls = []
    expected = [{"center": 100.0}]

    def fake_build(candles, **kwargs):
        calls.append(len(candles))
        return expected

    monkeypatch.setattr(module, "build_volume_zones", fake_build)
    zones, meta = build_volume_zones_with_fallback(
        [{"close": 100.0}] * 500, price=100.0,
    )

    assert calls == [500]
    assert zones == expected
    assert meta["source"] == "full_window"
