from __future__ import annotations

from datetime import date

import pytest
from scripts.research.clt_price_control import (
    _directional_summaries,
    _percentile,
    _summarize_events,
    detect_price_only_events,
    moving_block_confidence_intervals,
)


def _bars(count: int = 80) -> list[dict]:
    rows = []
    for index in range(count):
        price = 100.0
        rows.append({
            "open_time": index * 15 * 60_000,
            "open": price,
            "high": 100.2,
            "low": 99.8,
            "close": price,
        })
    return rows


def test_price_only_break_enters_next_open_and_uses_directional_return():
    signal_rows = _bars()
    execution_rows = _bars()
    signal_rows[10].update({"high": 101.2, "close": 101.0})
    # The signal candle is deliberately not a breakout on the execution venue.
    execution_rows[10].update({"high": 100.2, "close": 100.0})
    # These source-venue prices must not leak into execution returns.
    signal_rows[11]["open"] = 999.0
    signal_rows[12]["close"] = 999.0
    execution_rows[11]["open"] = 100.0
    execution_rows[12]["close"] = 102.0

    events = detect_price_only_events(
        signal_rows,
        execution_rows,
        symbol="BTC",
        lookback_bars=8,
        cooldown_bars=36,
        horizons_bars=(2, 4),
    )

    assert len(events) == 1
    assert events[0]["direction"] == 1
    assert events[0]["entry_ts_ms"] == execution_rows[11]["open_time"]
    assert events[0]["entry_price"] == 100.0
    assert events[0]["gross_bps_2"] == pytest.approx(200.0)


def test_price_only_control_requires_full_maximum_horizon():
    rows = _bars(20)
    rows[10].update({"high": 101.2, "close": 101.0})
    events = detect_price_only_events(
        rows,
        rows,
        symbol="BTC",
        lookback_bars=8,
        cooldown_bars=36,
        horizons_bars=(2, 16),
    )
    # Entry is the next candle; there is no complete 16-bar follow-up.
    assert events == []


def test_price_only_detector_validates_cooldown_and_horizons():
    with pytest.raises(ValueError, match="cooldown"):
        detect_price_only_events(_bars(), _bars(), symbol="BTC", cooldown_bars=-1)
    with pytest.raises(ValueError, match="strictly increasing"):
        detect_price_only_events(
            _bars(), _bars(), symbol="BTC", horizons_bars=(2, 2)
        )


def test_moving_block_bootstrap_is_deterministic_and_clusters_by_date():
    events = []
    for day in range(10):
        for market in ("BTC", "ETH"):
            events.append({
                "signal_date_utc": (date(2026, 10, 1).fromordinal(
                    date(2026, 10, 1).toordinal() + day
                )).isoformat(),
                "gross_bps_2": float(day + (market == "BTC")),
            })

    kwargs = {
        "events": events,
        "horizons_bars": (2,),
        "start_date": date(2026, 10, 1),
        "end_date": date(2026, 10, 10),
        "block_days": 3,
        "resamples": 500,
        "seed": 1234,
    }
    first = moving_block_confidence_intervals(**kwargs)
    second = moving_block_confidence_intervals(**kwargs)
    assert first == second
    assert first["2"]["lower_95_bps"] <= first["2"]["upper_95_bps"]


def test_percentile_linearly_interpolates():
    assert _percentile([0.0, 10.0], 0.25) == pytest.approx(2.5)



def test_event_summaries_separate_each_market_and_exclude_hype():
    events = []
    for day in range(10):
        signal_date = (date(2026, 10, 1).fromordinal(
            date(2026, 10, 1).toordinal() + day
        )).isoformat()
        for symbol, value in (("BTC", 8.0), ("ETH", 4.0), ("HYPE", 30.0)):
            events.append({
                "symbol": symbol,
                "signal_date_utc": signal_date,
                "gross_bps_2": value,
            })

    common = {
        "horizons_bars": (2,),
        "start_date": date(2026, 10, 1),
        "end_date": date(2026, 10, 10),
        "block_days": 3,
        "resamples": 500,
        "seed": 1234,
    }
    pooled = _summarize_events(events, **common)
    btc = _summarize_events(
        [event for event in events if event["symbol"] == "BTC"], **common
    )
    without_hype = _summarize_events(
        [event for event in events if event["symbol"] != "HYPE"], **common
    )

    assert pooled["events"] == 30
    assert pooled["gross_mean_bps"]["2"] == pytest.approx(14.0)
    assert btc["events"] == 10
    assert btc["gross_mean_bps"]["2"] == pytest.approx(8.0)
    assert without_hype["events"] == 20
    assert without_hype["gross_mean_bps"]["2"] == pytest.approx(6.0)
    assert without_hype["net_mean_bps"]["maker_maker"]["2"] == pytest.approx(2.0)
    assert without_hype["net_moving_block_bootstrap_95pct"]["maker_maker"]["2"]["lower_95_bps"] == pytest.approx(
        without_hype["gross_moving_block_bootstrap_95pct"]["2"]["lower_95_bps"] - 4.0
    )



def test_price_only_control_requires_timestamp_aligned_venues():
    signal_rows = _bars()
    execution_rows = _bars()
    execution_rows[10]["open_time"] += 1
    with pytest.raises(ValueError, match="timestamps do not align"):
        detect_price_only_events(
            signal_rows,
            execution_rows,
            symbol="BTC",
            lookback_bars=8,
            cooldown_bars=36,
            horizons_bars=(2,),
        )


def test_signal_timestamp_is_the_closed_bar_time_across_utc_midnight():
    signal_rows = _bars()
    execution_rows = _bars()
    # 21:15 UTC on Oct 3; row 10 opens at 23:45 and closes at midnight.
    base_ms = 1_791_062_100_000
    for index in range(len(signal_rows)):
        signal_rows[index]["open_time"] = base_ms + index * 15 * 60_000
        execution_rows[index]["open_time"] = base_ms + index * 15 * 60_000
    signal_rows[10].update({"high": 101.2, "close": 101.0})
    execution_rows[11]["open"] = 100.0
    execution_rows[12]["close"] = 102.0

    events = detect_price_only_events(
        signal_rows,
        execution_rows,
        symbol="BTC",
        lookback_bars=8,
        cooldown_bars=36,
        horizons_bars=(2,),
    )

    expected_signal_close = base_ms + 11 * 15 * 60_000
    assert len(events) == 1
    assert events[0]["signal_ts_ms"] == expected_signal_close
    assert events[0]["entry_ts_ms"] == expected_signal_close
    assert events[0]["signal_date_utc"] == "2026-10-04"


def test_directional_summaries_keep_long_short_and_1_5x_fee_stress_separate():
    events = []
    for day in range(10):
        signal_date = (date(2026, 10, 1).fromordinal(
            date(2026, 10, 1).toordinal() + day
        )).isoformat()
        events.append({
            "direction": 1,
            "signal_date_utc": signal_date,
            "gross_bps_2": float(10 + day),
        })
        events.append({
            "direction": -1,
            "signal_date_utc": signal_date,
            "gross_bps_2": float(-5 + day),
        })

    result = _directional_summaries(
        events,
        horizons_bars=(2,),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 10),
        block_days=3,
        resamples=500,
        seed=1234,
    )

    assert result["long"]["events"] == 10
    assert result["long"]["gross_mean_bps"]["2"] == pytest.approx(14.5)
    assert result["long"]["net_mean_bps"]["taker_taker"]["2"] == pytest.approx(4.5)
    assert result["long"]["fee_only_1_5x_taker_stress"]["round_trip_fee_bps"] == pytest.approx(15.0)
    assert result["long"]["fee_only_1_5x_taker_stress"]["net_mean_bps"]["2"] == pytest.approx(-0.5)
    assert result["short"]["events"] == 10
    assert result["short"]["gross_mean_bps"]["2"] == pytest.approx(-0.5)
    assert result["short"]["fee_only_1_5x_taker_stress"]["net_mean_bps"]["2"] == pytest.approx(-15.5)
    stress_ci = result["short"]["fee_only_1_5x_taker_stress"]["moving_block_bootstrap_95pct"]["2"]
    gross_ci = result["short"]["gross_moving_block_bootstrap_95pct"]["2"]
    assert stress_ci["lower_95_bps"] == pytest.approx(gross_ci["lower_95_bps"] - 15.0)
    assert stress_ci["upper_95_bps"] == pytest.approx(gross_ci["upper_95_bps"] - 15.0)
