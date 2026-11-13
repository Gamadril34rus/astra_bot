from __future__ import annotations

from astra_bot.decision import live_arm_feed


def test_recent_profile_counterfactual_does_not_override_fail_closed_filter(monkeypatch):
    monkeypatch.setenv("ZONE_TRADE_FILTER", "1")
    symbol = "HYPE-USDT"
    bar_ts = 123456
    fallback_zones = [{
        "lower": 71.0, "upper": 72.0, "center": 71.5,
        "quote_volume": 1000.0, "width": 1.0,
    }]
    monkeypatch.setitem(live_arm_feed._ZONE_CACHE, symbol, (bar_ts, []))
    monkeypatch.setitem(live_arm_feed._ZONE_SHADOW_CACHE, symbol, (bar_ts, fallback_zones))
    monkeypatch.setitem(live_arm_feed._ZONE_PROFILE_META, symbol, {
        "source": "empty_full_window_shadow_only",
        "lookback_bars": 499,
        "shadow_source": "recent_fallback",
        "shadow_lookback_bars": 119,
        "shadow_only": True,
    })
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    monkeypatch.setattr(live_arm_feed, "_append_zone_trade_filter_event", lambda event: None)

    def fake_decision(row, zones):
        if zones:
            return {"accepted": True, "state": "NO_OPPOSING_ZONE", "reason": "no_zone_ahead"}
        return {"accepted": False, "state": "UNKNOWN", "reason": "empty_or_unusable_zone_profile"}

    monkeypatch.setattr(live_arm_feed, "_zone_trade_filter_decision", fake_decision)
    row = {
        "signal_id": "hype-shadow-test",
        "symbol": symbol,
        "side": "short",
        "strategy": "zeus_channel_boundary",
        "entry": 71.2,
        "stop": 71.9,
        "tp": 69.8,
    }

    result = live_arm_feed._apply_zone_trade_filter(
        row, symbol=symbol, price=71.2, expected_bar_ts=bar_ts,
    )

    assert result is None
    actual = next(x for x in journals if x.get("event") == "zone_trade_filter")
    shadow = next(x for x in journals if x.get("event") == "zone_filter_shadow_counterfactual")
    assert actual["accepted"] is False
    assert actual["reason"] == "empty_or_unusable_zone_profile"
    assert shadow["actual_accepted"] is False
    assert shadow["shadow_accepted"] is True
    assert shadow["shadow_source"] == "recent_fallback"
