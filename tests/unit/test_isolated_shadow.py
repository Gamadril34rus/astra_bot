from pathlib import Path

import pytest

from astra_bot.research.isolated_shadow import (
    ShadowReplay,
    Signal,
    ZoneState,
    VirtualPortfolio,
    classify_zone,
    select_baseline_signal,
    variant_decision,
)


def signal(
    side="long",
    symbol="BTC-USDT",
    strategy="zeus_channel_boundary",
    signal_id="same",
):
    return Signal(
        signal_id=signal_id,
        symbol=symbol,
        side=side,
        strategy=strategy,
        formed_at_ms=1_000,
        market_received_at_ms=1_000,
        zone_received_at_ms=1_000,
        source_closed_bar_ms=900,
        decision_at_ms=1_000,
        predecision_price=100.0,
        price_source="synthetic",
        entry_reference=100.0,
        stop_reference=98.0,
        target_reference=104.0,
    )


def zone(side="long", price=100.0, zones=None, age=0):
    return classify_zone(
        side=side,
        price=price,
        zones=zones,
        market_received_at_ms=10_000 - age,
        zone_received_at_ms=10_000 - age,
        decision_at_ms=10_000,
    )


def test_long_uses_lower_boundary_and_blocks_exact_one_percent():
    result = zone(zones=[{"lower": 101.0, "upper": 103.0}])
    assert result.state == ZoneState.OUTSIDE_NEAR
    assert result.boundary == 101.0
    assert result.distance_pct == pytest.approx(1.0)
    assert not variant_decision("C", signal(), result).accepted_by_variant


def test_short_uses_upper_boundary():
    result = zone(
        side="short",
        zones=[{"lower": 97.0, "upper": 99.0}],
    )
    assert result.boundary == 99.0
    assert result.distance_pct == pytest.approx(1.0)


def test_more_than_one_percent_is_not_vetoed():
    result = zone(zones=[{"lower": 101.0001, "upper": 103.0}])
    assert result.state == ZoneState.OUTSIDE_FAR
    assert variant_decision("C", signal(), result).accepted_by_variant


def test_zone_states_are_distinct_and_only_near_is_blocked():
    cases = [
        zone(zones=[{"lower": 99.0, "upper": 101.0}]),
        zone(zones=[]),
        zone(zones=[{"lower": 95.0, "upper": 98.0}]),
        classify_zone(
            side="long",
            price=100.0,
            zones=None,
            market_received_at_ms=None,
            zone_received_at_ms=None,
            decision_at_ms=10_000,
        ),
    ]
    expected = [
        ZoneState.IN_ZONE,
        ZoneState.EMPTY_ZONES,
        ZoneState.NO_OPPOSING_ZONE,
        ZoneState.UNKNOWN,
    ]
    for result, state in zip(cases, expected, strict=True):
        assert result.state == state
        assert variant_decision("C", signal(), result).accepted_by_variant


def test_stale_and_future_snapshots_are_unknown():
    assert zone(
        zones=[{"lower": 101.0, "upper": 102.0}],
        age=30_001,
    ).state == ZoneState.UNKNOWN
    result = classify_zone(
        side="long",
        price=100.0,
        zones=[],
        market_received_at_ms=10_001,
        zone_received_at_ms=10_000,
        decision_at_ms=10_000,
    )
    assert result.state == ZoneState.UNKNOWN


def test_variant_b_filters_channel_long_but_keeps_multi_long():
    empty = zone(zones=[])
    assert not variant_decision("B", signal(), empty).accepted_by_variant
    assert (
        variant_decision("B", signal(side="short"), empty).accepted_by_variant
    )
    assert not variant_decision(
        "B",
        signal(side="short", symbol="INJ-USDT"),
        empty,
    ).accepted_by_variant
    assert variant_decision(
        "B",
        signal(strategy="zeus_multi_top_breakdown"),
        empty,
    ).accepted_by_variant


def test_one_baseline_signal_fans_out_without_b_fallback():
    channel_long = {
        "signal_id": "channel-long",
        "strategy": "zeus_channel_boundary",
        "side": "long",
    }
    simultaneous_multi_short = {
        "signal_id": "multi-short",
        "strategy": "zeus_multi_top_breakdown",
        "side": "short",
    }
    baseline = select_baseline_signal(
        channel_long,
        simultaneous_multi_short,
        None,
    )
    assert baseline is channel_long

    source_signal = signal(side="long", signal_id=baseline["signal_id"])
    replay = ShadowReplay(
        portfolios={
            name: VirtualPortfolio(name, initial_capital=1_000.0)
            for name in "ABC"
        }
    )
    result = replay.apply_signal(
        event_id="source-event-1",
        signal=source_signal,
        zone=zone(zones=[]),
        execution_price=100.0,
    )
    assert result["variant_results"]["A"]["reason"] == "OPENED"
    assert result["variant_results"]["C"]["reason"] == "OPENED"
    assert (
        result["variant_results"]["B"]["reason"]
        == "B_CHANNEL_SHORT_ONLY"
    )
    assert all(
        row["signal_id"] == "channel-long"
        for row in result["variant_results"].values()
    )
    assert len(replay.portfolios["B"].positions) == 0
    assert len(replay.portfolios["A"].positions) == 1
    assert len(replay.portfolios["C"].positions) == 1


def test_multi_fallback_order_is_four_hour_then_one_hour_only_if_needed():
    multi_4h = {"signal_id": "multi-4h"}
    multi_1h = {"signal_id": "multi-1h"}
    assert select_baseline_signal(None, multi_4h, multi_1h) is multi_4h
    assert select_baseline_signal(None, None, multi_1h) is multi_1h
    assert select_baseline_signal(None, None, None) is None


def test_portfolios_have_independent_capital_and_max_open():
    replay = ShadowReplay(
        portfolios={
            name: VirtualPortfolio(name, initial_capital=1_000.0, max_open=1)
            for name in "ABC"
        }
    )
    empty = zone(zones=[])
    first = signal(side="short")
    replay.apply_signal(
        event_id="one",
        signal=first,
        zone=empty,
        execution_price=100.0,
    )
    second = signal(
        side="short",
        symbol="ETH-USDT",
        signal_id="second",
    )
    replay.apply_signal(
        event_id="two",
        signal=second,
        zone=empty,
        execution_price=100.0,
    )
    for portfolio in replay.portfolios.values():
        assert len(portfolio.positions) == 1
        assert portfolio.attempts[-1]["reason"] == "MAX_OPEN"
    replay.portfolios["A"].equity_cash -= 10.0
    assert replay.portfolios["B"].equity_cash != replay.portfolios["A"].equity_cash



def test_portfolio_wide_gross_exposure_cap_is_not_per_position():
    portfolio = VirtualPortfolio(
        "A",
        initial_capital=1_000.0,
        risk_fraction=1.0,
        max_open=5,
        max_exposure_fraction=0.30,
        max_leverage=6.0,
    )
    empty = zone(zones=[])
    first_signal = signal(side="short", stop_reference=50.0)
    first = portfolio.attempt(
        first_signal,
        variant_decision("A", first_signal, empty),
        execution_price=100.0,
    )
    assert first["reason"] == "OPENED"
    first_exposure = sum(
        position.remaining_qty * position.entry_price
        for position in portfolio.positions.values()
    )
    assert first_exposure == pytest.approx(300.0)

    second_signal = signal(
        side="short",
        symbol="ETH-USDT",
        signal_id="second-exposure",
        stop_reference=50.0,
    )
    second = portfolio.attempt(
        second_signal,
        variant_decision("A", second_signal, empty),
        execution_price=100.0,
    )
    assert second["reason"] == "ZERO_SIZE"
    assert len(portfolio.positions) == 1


def test_directionally_invalid_stop_is_rejected():
    portfolio = VirtualPortfolio("A", initial_capital=1_000.0)
    empty = zone(zones=[])
    bad_long = signal(side="long", stop_reference=101.0)
    result = portfolio.attempt(
        bad_long,
        variant_decision("A", bad_long, empty),
        execution_price=100.0,
    )
    assert result["reason"] == "INVALID_INITIAL_RISK"
    assert not portfolio.fills

def test_repeated_signal_id_has_distinct_attempts_and_positions():
    portfolio = VirtualPortfolio("A", initial_capital=1_000.0)
    empty = zone(zones=[])
    first_signal = signal(side="short")
    first = portfolio.attempt(
        first_signal,
        variant_decision("A", first_signal, empty),
        execution_price=100.0,
    )
    position = portfolio.positions[first["position_id"]]
    portfolio.close(
        position.position_id,
        event_id="exit-1",
        price=105.0,
        quantity=position.remaining_qty,
        ts_ms=2_000,
        reason="test",
    )
    second = portfolio.attempt(
        first_signal,
        variant_decision("A", first_signal, empty),
        execution_price=100.0,
    )
    assert first["attempt_id"] != second["attempt_id"]
    assert first["position_id"] != second["position_id"]


def test_partial_close_is_idempotent_and_position_pnl_is_aggregated_once():
    portfolio = VirtualPortfolio("A", initial_capital=1_000.0)
    source = signal(side="short")
    empty = zone(zones=[])
    opened = portfolio.attempt(
        source,
        variant_decision("A", source, empty),
        execution_price=100.0,
    )
    position = portfolio.positions[opened["position_id"]]
    half = position.remaining_qty / 2
    first = portfolio.close(
        position.position_id,
        event_id="partial-1",
        price=98.0,
        quantity=half,
        ts_ms=2_000,
        reason="partial",
    )
    cash_after_partial = portfolio.equity_cash
    duplicate = portfolio.close(
        position.position_id,
        event_id="partial-1",
        price=98.0,
        quantity=half,
        ts_ms=2_000,
        reason="partial",
    )
    assert duplicate["duplicate"]
    assert portfolio.equity_cash == pytest.approx(cash_after_partial)
    portfolio.close(
        position.position_id,
        event_id="final-1",
        price=97.0,
        quantity=portfolio.positions[position.position_id].remaining_qty,
        ts_ms=3_000,
        reason="target",
    )
    assert len(portfolio.closed) == 1
    assert len([fill for fill in portfolio.fills if fill.get("event_id")]) == 2
    assert portfolio.metrics()["unknown_R_positions"] == 1
    assert portfolio.metrics()["pf_contributors"][0]["position_id"] == position.position_id
    assert first["position_id"] == position.position_id


def test_replay_state_round_trip_preserves_positions_and_event_idempotency():
    replay = ShadowReplay()
    source = signal(side="short")
    replay.apply_signal(
        event_id="stable-signal-event",
        signal=source,
        zone=zone(zones=[]),
        execution_price=100.0,
    )
    restored = ShadowReplay.import_state(replay.export_state())
    duplicate = restored.apply_signal(
        event_id="stable-signal-event",
        signal=source,
        zone=zone(zones=[]),
        execution_price=100.0,
    )
    assert duplicate["duplicate"]
    for name in "ABC":
        assert len(restored.portfolios[name].positions) == 1
        assert restored.portfolios[name].equity_cash == pytest.approx(
            replay.portfolios[name].equity_cash
        )


def test_missing_execution_price_never_fabricates_a_fill():
    replay = ShadowReplay()
    result = replay.apply_signal(
        event_id="no-fill-price",
        signal=signal(side="short"),
        zone=zone(zones=[]),
        execution_price=None,
    )
    for row in result["variant_results"].values():
        assert row["reason"] == "UNKNOWN_EXECUTION_PRICE"
    assert all(not portfolio.fills for portfolio in replay.portfolios.values())
    assert all(not portfolio.positions for portfolio in replay.portfolios.values())


def test_capture_source_has_no_trading_or_working_state_paths():
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts/zeus_shadow_capture.py").read_text()
    assert ".post(" not in source
    assert ".delete(" not in source
    assert "live_arm_wiring" not in source
    assert "PENDING_PATH" not in source
    assert "zeus_live_journal" not in source
    assert "BINGX_API_SECRET" in source
