from astra_bot.decision.zone_shadow import build_volume_zones, zone_context


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
