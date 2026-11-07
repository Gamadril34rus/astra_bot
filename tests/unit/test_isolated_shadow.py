from datetime import datetime, timedelta, timezone

import pytest
from astra_bot.research.isolated_shadow import (
    PORTFOLIO_NAMES,
    ExecutionQuote,
    ReplayBar,
    ShadowReplay,
    Signal,
    VirtualPortfolio,
    ZoneState,
    classify_zone,
    next_available_quote,
    select_baseline_signal,
    session_profile,
    variant_decision,
)

MSK = timezone(timedelta(hours=3))


def signal(
    side="long",
    symbol="BTC-USDT",
    strategy="zeus_channel_boundary",
    signal_id="same",
    stop_reference=None,
    target_reference=None,
    confidence=0.75,
    metadata=None,
    decision_at_ms=1_000,
    market_received_at_ms=1_000,
    formed_at_ms=900,
    source_closed_bar_ms=900,
):
    stop = stop_reference if stop_reference is not None else (
        102.0 if side == "short" else 98.0
    )
    target = target_reference if target_reference is not None else (
        96.0 if side == "short" else 104.0
    )
    return Signal(
        signal_id=signal_id,
        symbol=symbol,
        side=side,
        strategy=strategy,
        formed_at_ms=formed_at_ms,
        market_received_at_ms=market_received_at_ms,
        zone_received_at_ms=market_received_at_ms,
        source_closed_bar_ms=source_closed_bar_ms,
        decision_at_ms=decision_at_ms,
        predecision_price=100.0,
        price_source="synthetic",
        entry_reference=100.0,
        stop_reference=stop,
        target_reference=target,
        confidence=confidence,
        metadata=metadata or {},
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


def opened(
    portfolio=None,
    source=None,
    *,
    price=100.0,
    timestamp=1_100,
    available=10_000.0,
):
    portfolio = portfolio or VirtualPortfolio("A", initial_capital=1_000.0)
    source = source or signal(side="short")
    decision = variant_decision("A", source, zone(zones=[]))
    row = portfolio.attempt(
        source,
        decision,
        execution_price=price,
        execution_quantity=available,
        execution_timestamp_ms=timestamp,
    )
    assert row["reason"] in {"OPENED", "OPENED_PARTIAL"}
    return portfolio, row, portfolio.positions[row["position_id"]]


def quote(
    quote_id,
    timestamp,
    *,
    bid=99.0,
    ask=101.0,
    bid_size=1_000.0,
    ask_size=1_000.0,
    received=None,
):
    return ExecutionQuote(
        quote_id=quote_id,
        timestamp_ms=timestamp,
        received_at_ms=timestamp if received is None else received,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )


def bar(
    *,
    symbol="BTC-USDT",
    opened=2_000,
    closed=3_000,
    open_price=100.0,
    high=101.0,
    low=97.0,
    close=99.0,
    received=None,
):
    return ReplayBar(
        symbol=symbol,
        timeframe="4h",
        open_time_ms=opened,
        close_time_ms=closed,
        received_at_ms=closed if received is None else received,
        open=open_price,
        high=high,
        low=low,
        close=close,
    )


def test_long_uses_lower_boundary_and_exact_one_percent_is_near():
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
        decision = variant_decision("C", signal(), result)
        if state == ZoneState.UNKNOWN:
            assert not decision.accepted_by_variant
            assert decision.reason == "UNKNOWN_C_ZONE_DATA"
        else:
            assert decision.accepted_by_variant


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
    assert variant_decision(
        "B", signal(side="short"), empty
    ).accepted_by_variant
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

    source = signal(side="long", signal_id=baseline["signal_id"])
    replay = ShadowReplay(
        portfolios={
            name: VirtualPortfolio(name, initial_capital=1_000.0)
            for name in PORTFOLIO_NAMES
        }
    )
    result = replay.apply_signal(
        event_id="source-event-1",
        signal=source,
        zone=zone(zones=[]),
        execution_price=100.0,
        execution_quantity=10_000.0,
        execution_timestamp_ms=1_100,
    )
    assert result["variant_results"]["A"]["reason"] == "OPENED"
    assert result["variant_results"]["C"]["reason"] == "OPENED"
    assert result["variant_results"]["B"]["reason"] == "B_CHANNEL_SHORT_ONLY"
    assert result["variant_results"]["2_OF_3"]["reason"] == "OPENED"
    assert result["variant_results"]["3_OF_3"]["reason"] == "CONSENSUS_3_OF_3_REJECT"
    assert all(
        row["signal_id"] == "channel-long"
        for row in result["variant_results"].values()
    )
    assert result["zone_state"] == ZoneState.EMPTY_ZONES.value
    assert not replay.portfolios["B"].positions
    assert not replay.portfolios["3_OF_3"].positions
    assert len(replay.portfolios["A"].positions) == 1
    assert len(replay.portfolios["B"].attempts) == 1
    assert len(replay.portfolios["C"].positions) == 1
    assert len(replay.portfolios["2_OF_3"].positions) == 1
    assert len(replay.portfolios["3_OF_3"].attempts) == 1


def test_consensus_majority_can_open_when_unanimous_is_unknown():
    replay = ShadowReplay(
        portfolios={
            name: VirtualPortfolio(name, initial_capital=1_000.0)
            for name in PORTFOLIO_NAMES
        }
    )
    source = signal(strategy="zeus_multi_top_breakdown", side="long")
    unknown_zone = zone(zones=None)
    assert unknown_zone.state == ZoneState.UNKNOWN

    result = replay.apply_signal(
        event_id="majority-with-unknown",
        signal=source,
        zone=unknown_zone,
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )

    assert result["source_variant_decisions"]["A"]["accepted"]
    assert result["source_variant_decisions"]["B"]["accepted"]
    assert result["source_variant_decisions"]["C"]["reason"] == "UNKNOWN_C_ZONE_DATA"
    assert result["consensus_decisions"]["2_OF_3"]["accepted"]
    assert result["consensus_decisions"]["3_OF_3"]["reason"] == "UNKNOWN_CONSENSUS_3_OF_3"
    assert len(replay.portfolios["2_OF_3"].positions) == 1
    assert not replay.portfolios["3_OF_3"].positions
    assert all(len(portfolio.attempts) == 1 for portfolio in replay.portfolios.values())


def test_consensus_majority_is_unknown_when_one_vote_cannot_be_resolved():
    replay = ShadowReplay()
    source = signal(side="long", strategy="zeus_channel_boundary")
    unknown_zone = zone(zones=None)

    result = replay.apply_signal(
        event_id="majority-unknown-tie",
        signal=source,
        zone=unknown_zone,
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )

    # A accepts, B rejects channel LONG, C is UNKNOWN: neither two votes pass
    # nor two votes definitively reject.
    assert result["source_variant_decisions"]["A"]["accepted"]
    assert not result["source_variant_decisions"]["B"]["accepted"]
    assert result["source_variant_decisions"]["C"]["reason"] == "UNKNOWN_C_ZONE_DATA"
    assert result["consensus_decisions"]["2_OF_3"]["reason"] == "UNKNOWN_CONSENSUS_2_OF_3"
    assert result["consensus_decisions"]["3_OF_3"]["reason"] == "CONSENSUS_3_OF_3_REJECT"
    assert not replay.portfolios["2_OF_3"].positions
    assert not replay.portfolios["3_OF_3"].positions


def test_multi_fallback_order_is_four_hour_then_one_hour_only_if_needed():
    multi_4h = {"signal_id": "multi-4h"}
    multi_1h = {"signal_id": "multi-1h"}
    assert select_baseline_signal(None, multi_4h, multi_1h) is multi_4h
    assert select_baseline_signal(None, None, multi_1h) is multi_1h
    assert select_baseline_signal(None, None, None) is None


def test_next_entry_uses_first_quote_strictly_after_signal():
    quotes = [
        quote("same-time", 1_000),
        quote("next", 1_001, ask=101.5, ask_size=2.0),
        quote("later", 1_002, ask=102.0),
    ]
    selected = next_available_quote(quotes, strictly_after_ms=1_000)
    assert selected.quote_id == "next"
    replay = ShadowReplay()
    result = replay.apply_signal_from_quotes(
        event_id="entry-event",
        signal=signal(side="long"),
        zone=zone(zones=[]),
        quotes=quotes,
    )
    row = result["variant_results"]["A"]
    assert row["fill_price"] == pytest.approx(101.5)
    assert row["quantity"] == pytest.approx(2.0)
    assert row["reason"] == "OPENED_PARTIAL"


def test_missing_or_unusable_next_quote_is_unknown_not_a_fill():
    replay = ShadowReplay()
    missing = replay.apply_signal_from_quotes(
        event_id="missing-quote",
        signal=signal(side="long"),
        zone=zone(zones=[]),
        quotes=[],
    )
    assert missing["variant_results"]["A"]["reason"] == "UNKNOWN_EXECUTION_TIMESTAMP"
    assert not replay.portfolios["A"].fills

    invalid = replay.apply_signal_from_quotes(
        event_id="no-ask-size",
        signal=signal(side="long"),
        zone=zone(zones=[]),
        quotes=[quote("bad", 1_001, ask_size=None)],
    )
    assert invalid["variant_results"]["A"]["reason"] == "UNKNOWN_EXECUTION_LIQUIDITY"
    assert not replay.portfolios["A"].positions


def test_future_or_stale_quote_is_not_skipped_for_a_better_later_price():
    selected = next_available_quote(
        [
            quote("stale-first", 1_001, received=50_000),
            quote("fresh-second", 1_002),
        ],
        strictly_after_ms=1_000,
    )
    assert selected is None


def test_open_close_and_max_open_are_independent_per_portfolio():
    replay = ShadowReplay(
        portfolios={
            name: VirtualPortfolio(name, initial_capital=1_000.0, max_open=1)
            for name in PORTFOLIO_NAMES
        }
    )
    empty = zone(zones=[])
    first = signal(side="short")
    replay.apply_signal(
        event_id="one",
        signal=first,
        zone=empty,
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
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
        execution_quantity=100.0,
        execution_timestamp_ms=1_200,
    )
    for portfolio in replay.portfolios.values():
        assert len(portfolio.positions) == 1
        assert portfolio.attempts[-1]["reason"] == "MAX_OPEN"
    replay.portfolios["A"].equity_cash -= 10.0
    assert replay.portfolios["B"].equity_cash != replay.portfolios["A"].equity_cash


def test_session_profiles_match_weekday_weekend_and_off_buckets():
    weekday_fat = int(datetime(2026, 11, 9, 10, tzinfo=MSK).timestamp() * 1000)
    weekday_off = int(datetime(2026, 11, 9, 8, tzinfo=MSK).timestamp() * 1000)
    weekend_normal = int(datetime(2026, 11, 7, 17, tzinfo=MSK).timestamp() * 1000)
    assert session_profile(weekday_fat)["name"] == "fat"
    assert not session_profile(weekday_off)["arm"]
    assert session_profile(weekend_normal)["name"] == "normal"


def test_session_gate_blocks_new_entries_but_does_not_touch_open_positions():
    off_time = int(datetime(2026, 11, 7, 7, tzinfo=MSK).timestamp() * 1000)
    portfolio = VirtualPortfolio("A")
    source = signal(side="short", decision_at_ms=off_time)
    result = portfolio.attempt(
        source,
        variant_decision("A", source, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=off_time + 1,
    )
    assert result["reason"] == "SESSION_OFF"
    assert not portfolio.positions


def test_cooldown_blocks_same_strategy_symbol_side_after_close():
    portfolio, _row, position = opened()
    portfolio.set_funding_coverage(
        position.position_id,
        through_ms=2_000,
        cumulative_cash_amount=0.0,
        source_verified=True,
    )
    portfolio.close(
        position.position_id,
        event_id="close-first",
        price=99.0,
        quantity=position.remaining_qty,
        ts_ms=2_000,
        reason="manual",
    )
    repeat = signal(side="short", signal_id="again")
    blocked = portfolio.attempt(
        repeat,
        variant_decision("A", repeat, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=3_000,
    )
    assert blocked["reason"] == "ENTRY_COOLDOWN"


def test_inj_reserve_and_same_side_hard_cap_are_applied():
    portfolio = VirtualPortfolio("A", initial_capital=100_000.0)
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT"]
    for index, symbol in enumerate(symbols):
        side = "long" if index % 2 == 0 else "short"
        source = signal(side=side, symbol=symbol, signal_id=f"open-{index}")
        row = portfolio.attempt(
            source,
            variant_decision("A", source, zone(zones=[])),
            execution_price=100.0,
            execution_quantity=1_000.0,
            execution_timestamp_ms=1_100 + index,
        )
        assert row["reason"] == "OPENED"
    inj = signal(side="short", symbol="INJ-USDT", signal_id="inj")
    blocked = portfolio.attempt(
        inj,
        variant_decision("A", inj, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=1_000.0,
        execution_timestamp_ms=2_000,
    )
    assert blocked["reason"] == "INJ_SLOT_RESERVE"

    same_side = VirtualPortfolio("A", initial_capital=100_000.0)
    for index, symbol in enumerate(symbols):
        source = signal(side="long", symbol=symbol, signal_id=f"long-{index}")
        same_side.attempt(
            source,
            variant_decision("A", source, zone(zones=[])),
            execution_price=100.0,
            execution_quantity=1_000.0,
            execution_timestamp_ms=1_100 + index,
        )
    extra = signal(side="long", symbol="XRP-USDT", signal_id="fifth-long")
    result = same_side.attempt(
        extra,
        variant_decision("A", extra, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=1_000.0,
        execution_timestamp_ms=2_000,
    )
    assert result["reason"] == "SAME_SIDE_CLUSTER_MAX"


def test_heat_halves_risk_tier_and_virtual_halt_blocks_entries():
    portfolio = VirtualPortfolio(
        "A",
        initial_capital=1_000.0,
        cooldown_ms=0,
        same_side_cluster_max=10,
        day_loss_limit=1.0,
        week_loss_limit=1.0,
        halt_pf_min_n=5,
    )
    for index in range(3):
        source = signal(
            side="short",
            symbol=f"COIN{index}-USDT",
            signal_id=f"loss-{index}",
            confidence=0.75,
        )
        row = portfolio.attempt(
            source,
            variant_decision("A", source, zone(zones=[])),
            execution_price=100.0,
            execution_quantity=100.0,
            execution_timestamp_ms=1_100 + index * 10_000,
        )
        position = portfolio.positions[row["position_id"]]
        portfolio.set_funding_coverage(
            position.position_id,
            through_ms=2_000 + index * 10_000,
            cumulative_cash_amount=0.0,
            source_verified=True,
        )
        portfolio.close(
            position.position_id,
            event_id=f"loss-exit-{index}",
            price=104.0,
            quantity=position.remaining_qty,
            ts_ms=2_000 + index * 10_000,
            reason="synthetic_loss",
        )
    assert portfolio.heat_active
    heated = signal(
        side="short",
        symbol="COIN3-USDT",
        signal_id="heated",
        confidence=0.75,
    )
    row = portfolio.attempt(
        heated,
        variant_decision("A", heated, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=40_000,
    )
    assert row["risk_fraction"] == pytest.approx(0.0075)

    # Verify virtual PF kill separately without day/week loss masking it.
    halt = VirtualPortfolio(
        "A",
        initial_capital=1_000.0,
        cooldown_ms=0,
        same_side_cluster_max=10,
        day_loss_limit=1.0,
        week_loss_limit=1.0,
        halt_pf_min_n=5,
        heat_loss_threshold=99,
    )
    for index in range(5):
        source = signal(
            side="short",
            symbol=f"LOSER{index}-USDT",
            signal_id=f"halt-{index}",
        )
        row = halt.attempt(
            source,
            variant_decision("A", source, zone(zones=[])),
            execution_price=100.0,
            execution_quantity=100.0,
            execution_timestamp_ms=1_100 + index * 10_000,
        )
        position = halt.positions[row["position_id"]]
        halt.set_funding_coverage(
            position.position_id,
            through_ms=2_000 + index * 10_000,
            cumulative_cash_amount=0.0,
            source_verified=True,
        )
        halt.close(
            position.position_id,
            event_id=f"halt-exit-{index}",
            price=104.0,
            quantity=position.remaining_qty,
            ts_ms=2_000 + index * 10_000,
            reason="synthetic_loss",
        )
    assert halt.halted
    blocked = halt.attempt(
        signal(side="short", symbol="NEXT-USDT", signal_id="after-halt"),
        variant_decision(
            "A",
            signal(side="short", symbol="NEXT-USDT", signal_id="after-halt"),
            zone(zones=[]),
        ),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=100_000,
    )
    assert blocked["reason"] == "VIRTUAL_HALT"


def test_stop_touch_creates_intent_but_does_not_invent_fill():
    portfolio, _row, position = opened(source=signal(side="long"))
    intents = portfolio.process_bar(bar())
    assert len(intents) == 1
    assert intents[0].reason == "STOP"
    assert portfolio.positions[position.position_id].remaining_qty == position.quantity
    assert not [fill for fill in portfolio.fills if fill["type"] == "EXIT"]
    result = portfolio.execute_pending_exit(position.position_id, [])
    assert result["reason"] == "UNKNOWN_EXIT_EXECUTION_PRICE"


def test_stop_gap_uses_adverse_bar_open_as_trigger_reference_then_next_quote():
    portfolio, _, position = opened(source=signal(side="long"))
    intents = portfolio.process_bar(
        bar(open_price=95.0, high=99.0, low=94.0, close=97.0)
    )
    assert intents[0].reason == "STOP_GAP"
    assert intents[0].reference_price == 95.0
    fill = portfolio.execute_pending_exit(
        position.position_id,
        [quote("exit-gap", 3_001, bid=94.5, ask=94.7)],
    )
    assert fill["filled_quantity"] == pytest.approx(position.quantity)
    assert position.position_id not in portfolio.positions


def test_simultaneous_stop_and_tp_touch_is_resolved_stop_first():
    portfolio, _, position = opened(source=signal(side="long"))
    intents = portfolio.process_bar(
        bar(high=105.0, low=97.0, close=101.0)
    )
    assert intents[0].reason == "STOP_FIRST_AMBIGUOUS_BAR"
    assert intents[0].reference_price == position.stop_price


def test_take_profit_partial_execution_resumes_without_double_count():
    schedule = {
        "take_profit_levels": [
            {"price": 102.0, "fraction": 0.3},
            {"price": 104.0, "fraction": 0.7},
        ]
    }
    source = signal(side="long", metadata=schedule)
    portfolio, _row, position = opened(source=source)
    intents = portfolio.process_bar(
        bar(high=103.0, low=99.0, close=102.5)
    )
    assert intents[0].reason == "TAKE_PROFIT"
    target_qty = position.quantity * 0.3
    first = portfolio.execute_pending_exit(
        position.position_id,
        [quote("tp-small", 3_001, bid=102.0, bid_size=target_qty / 2)],
    )
    assert first["filled_quantity"] == pytest.approx(target_qty / 2)
    assert first["partial"]
    assert position.position_id in portfolio.positions
    second = portfolio.execute_pending_exit(
        position.position_id,
        [quote("tp-rest", 3_002, bid=102.0, bid_size=target_qty)],
    )
    assert second["filled_quantity"] == pytest.approx(target_qty / 2)
    assert portfolio.positions[position.position_id].pending_exit is None
    assert portfolio.positions[position.position_id].tp_filled_qty[0] == pytest.approx(
        target_qty
    )
    exit_fills = [
        fill for fill in portfolio.fills
        if fill["type"] == "EXIT" and fill["position_id"] == position.position_id
    ]
    assert len(exit_fills) == 2
    assert sum(fill["quantity"] for fill in exit_fills) == pytest.approx(target_qty)
    assert position.position_id in portfolio.positions


def test_fees_funding_coverage_and_net_r_are_explicit():
    portfolio, _, position = opened()
    portfolio.set_funding_coverage(
        position.position_id,
        through_ms=2_000,
        cumulative_cash_amount=-2.0,
        source_verified=True,
    )
    portfolio.close(
        position.position_id,
        event_id="full-close",
        price=105.0,
        quantity=position.remaining_qty,
        ts_ms=2_000,
        reason="tp",
    )
    completed = portfolio.closed[0]
    assert completed.funding_complete
    assert completed.fees > completed.entry_fee
    assert completed.funding == pytest.approx(-2.0)
    assert completed.initial_risk_cash > 0
    metrics = portfolio.metrics()
    assert metrics["closed_R_values"]
    assert metrics["closed_pf_sample_complete"]


def test_missing_funding_is_unknown_and_does_not_enter_pf_as_zero():
    portfolio, _, position = opened()
    portfolio.close(
        position.position_id,
        event_id="missing-funding-close",
        price=105.0,
        quantity=position.remaining_qty,
        ts_ms=2_000,
        reason="tp",
    )
    metrics = portfolio.metrics()
    assert not portfolio.closed[0].funding_complete
    assert metrics["incomplete_funding_closed_positions"] == 1
    assert not metrics["closed_pf_sample_complete"]
    assert metrics["closed_pf_complete_positions_only"] is None
    assert portfolio.risk_data_unknown


def test_mark_to_market_includes_open_position_and_tracks_drawdown():
    portfolio, _, position = opened(source=signal(side="long"))
    coverage = portfolio.set_funding_coverage(
        position.position_id,
        through_ms=2_000,
        cumulative_cash_amount=0.0,
        source_verified=True,
    )
    assert coverage["covered"]
    good = portfolio.mark_to_market({"BTC-USDT": 110.0}, timestamp_ms=2_000)
    assert good["known"]
    assert good["equity"] > portfolio.initial_capital
    portfolio.set_funding_coverage(
        position.position_id,
        through_ms=2_500,
        cumulative_cash_amount=0.0,
        source_verified=True,
    )
    bad = portfolio.mark_to_market({"BTC-USDT": 90.0}, timestamp_ms=2_500)
    assert bad["known"]
    assert bad["equity"] < good["equity"]
    metrics = portfolio.metrics(
        {"BTC-USDT": 90.0},
        mark_timestamp_ms=2_500,
    )
    assert metrics["mark_to_market_status"] == "KNOWN"
    assert metrics["max_drawdown_pct_marked"] > 0
    stale = portfolio.metrics(
        {"BTC-USDT": 90.0},
        mark_timestamp_ms=2_600,
    )
    assert stale["mark_to_market_status"] == "UNKNOWN"


def test_repeated_signal_id_uses_distinct_attempt_and_position_ids():
    portfolio, first, position = opened()
    portfolio.set_funding_coverage(
        position.position_id,
        through_ms=2_000,
        cumulative_cash_amount=0.0,
        source_verified=True,
    )
    portfolio.close(
        position.position_id,
        event_id="first-exit",
        price=95.0,
        quantity=position.remaining_qty,
        ts_ms=2_000,
        reason="test",
    )
    # The cooldown is a separate live constraint; advance beyond it.
    second_signal = signal(side="short", decision_at_ms=1_300_000)
    second = portfolio.attempt(
        second_signal,
        variant_decision("A", second_signal, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_300_100,
    )
    assert first["attempt_id"] != second["attempt_id"]
    assert first["position_id"] != second["position_id"]


def test_replay_state_round_trip_preserves_portfolios_and_idempotency():
    replay = ShadowReplay()
    source = signal(side="short")
    replay.apply_signal(
        event_id="stable-signal-event",
        signal=source,
        zone=zone(zones=[]),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )
    restored = ShadowReplay.import_state(replay.export_state())
    duplicate = restored.apply_signal(
        event_id="stable-signal-event",
        signal=source,
        zone=zone(zones=[]),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )
    assert duplicate["duplicate"]
    for name in PORTFOLIO_NAMES:
        assert len(restored.portfolios[name].positions) == 1
        assert restored.portfolios[name].equity_cash == pytest.approx(
            replay.portfolios[name].equity_cash
        )


def test_partial_exit_restart_state_does_not_duplicate_fills():
    portfolio, _, position = opened(
        source=signal(
            side="long",
            metadata={"take_profit_levels": [{"price": 102.0, "fraction": 0.5}]},
        )
    )
    portfolio.process_bar(bar(high=103.0, low=99.0, close=102.5))
    partial = portfolio.execute_pending_exit(
        position.position_id,
        [quote("partial-before-restart", 3_001, bid=102.0, bid_size=1.0)],
    )
    assert partial["partial"]
    restored = VirtualPortfolio.import_state(portfolio.export_state())
    restored_position = restored.positions[position.position_id]
    before = len(restored.fills)
    duplicate = restored.close(
        position.position_id,
        event_id="duplicate-event",
        price=102.0,
        quantity=1.0,
        ts_ms=3_001,
        reason="duplicate",
    )
    # This is a new event id so it is intentionally a new fill; a replayed quote
    # with the original id is blocked by the persisted seen-event ledger.
    assert not duplicate.get("duplicate")
    repeated = restored.close(
        position.position_id,
        event_id=(
            f"{restored_position.pending_exit['intent_id']}:partial-before-restart"
        ),
        price=102.0,
        quantity=1.0,
        ts_ms=3_001,
        reason="duplicate",
    )
    assert repeated["duplicate"]
    assert len(restored.fills) == before + 1



def test_take_profit_limit_does_not_fill_when_next_bid_reverses_below_target():
    schedule = {"take_profit_levels": [{"price": 102.0, "fraction": 1.0}]}
    source = signal(side="long", metadata=schedule)
    portfolio, _, position = opened(source=source)
    portfolio.process_bar(bar(high=103.0, low=99.0, close=102.5))
    no_fill = portfolio.execute_pending_exit(
        position.position_id,
        [quote("reversed-after-tp", 3_001, bid=101.5)],
    )
    assert no_fill["reason"] == "TP_LIMIT_NOT_MARKETABLE_YET"
    assert position.position_id in portfolio.positions
    assert not [fill for fill in portfolio.fills if fill["type"] == "EXIT"]

    fill = portfolio.execute_pending_exit(
        position.position_id,
        [quote("marketable-tp", 3_002, bid=102.2)],
    )
    assert fill["filled_quantity"] == pytest.approx(position.quantity)
    assert position.position_id not in portfolio.positions


def test_take_profit_gap_is_first_touch_even_if_bar_later_reverses():
    schedule = {"take_profit_levels": [{"price": 102.0, "fraction": 1.0}]}
    source = signal(side="long", metadata=schedule)
    portfolio, _, position = opened(source=source)
    intents = portfolio.process_bar(
        bar(open_price=103.0, high=104.0, low=97.0, close=99.0)
    )
    assert len(intents) == 1
    assert intents[0].reason == "TAKE_PROFIT_GAP"
    assert intents[0].reference_price == 102.0
    result = portfolio.execute_pending_exit(
        position.position_id,
        [quote("after-gap-reversal", 3_001, bid=101.0)],
    )
    assert result["reason"] == "TP_LIMIT_NOT_MARKETABLE_YET"


def test_bar_straddling_entry_is_not_used_for_stop_or_tp_lookahead():
    portfolio, _, position = opened(source=signal(side="long"))
    intents = portfolio.process_bar(
        bar(opened=1_000, closed=3_000, low=90.0, high=120.0)
    )
    assert intents == []
    assert position.pending_exit is None


def test_funding_unknown_can_be_reconciled_after_position_close():
    portfolio, _, position = opened()
    portfolio.apply_funding_event(
        position.position_id,
        event_id="funding-missing",
        cash_amount=None,
        ts_ms=1_500,
    )
    portfolio.close(
        position.position_id,
        event_id="close-before-reconciliation",
        price=105.0,
        quantity=position.remaining_qty,
        ts_ms=2_000,
        reason="closed",
    )
    assert portfolio.risk_data_unknown
    coverage = portfolio.set_funding_coverage(
        position.position_id,
        through_ms=2_000,
        cumulative_cash_amount=-1.25,
        source_verified=True,
    )
    assert coverage["covered"]
    assert portfolio.closed[0].funding_complete
    assert not portfolio.risk_data_unknown
    assert portfolio.metrics()["closed_pf_sample_complete"]


def test_daily_and_weekly_realized_loss_limits_are_virtual_and_independent():
    portfolio = VirtualPortfolio(
        "A",
        initial_capital=1_000.0,
        cooldown_ms=0,
        same_side_cluster_max=10,
        day_loss_limit=0.04,
        week_loss_limit=0.06,
        heat_loss_threshold=99,
        halt_pf_min_n=99,
    )
    source = signal(
        side="short",
        stop_reference=101.0,
        target_reference=98.0,
        symbol="LOSS-USDT",
    )
    row = portfolio.attempt(
        source,
        variant_decision("A", source, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )
    position = portfolio.positions[row["position_id"]]
    portfolio.set_funding_coverage(
        position.position_id,
        through_ms=2_000,
        cumulative_cash_amount=0.0,
        source_verified=True,
    )
    portfolio.close(
        position.position_id,
        event_id="day-week-loss",
        price=110.0,
        quantity=position.remaining_qty,
        ts_ms=2_000,
        reason="synthetic_loss",
    )
    next_signal = signal(
        side="short",
        symbol="OTHER-USDT",
        signal_id="after-loss",
    )
    blocked = portfolio.attempt(
        next_signal,
        variant_decision("A", next_signal, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=3_000,
    )
    assert blocked["reason"] in {
        "VIRTUAL_DAY_LOSS_LIMIT",
        "VIRTUAL_WEEK_LOSS_LIMIT",
    }



def test_same_funding_source_event_is_applied_once_to_each_variant():
    replay = ShadowReplay()
    source = signal(side="short")
    replay.apply_signal(
        event_id="funding-fanout-entry",
        signal=source,
        zone=zone(zones=[]),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )
    for variant in "ABC":
        position = next(iter(replay.portfolios[variant].positions.values()))
        applied = replay.apply_funding_event(
            event_id="bingx-funding-row-001",
            variant=variant,
            position_id=position.position_id,
            cash_amount=-0.25,
            ts_ms=1_500,
        )
        assert applied["applied"]
        coverage = replay.apply_funding_coverage(
            event_id="funding-history-reconciled-001",
            variant=variant,
            position_id=position.position_id,
            through_ms=1_600,
            cumulative_cash_amount=-0.25,
            source_verified=True,
        )
        assert coverage["covered"]

    duplicate = replay.apply_funding_event(
        event_id="bingx-funding-row-001",
        variant="A",
        position_id=next(iter(replay.portfolios["A"].positions)),
        cash_amount=-0.25,
        ts_ms=1_500,
    )
    assert duplicate["duplicate"]
    duplicate_coverage = replay.apply_funding_coverage(
        event_id="funding-history-reconciled-001",
        variant="A",
        position_id=next(iter(replay.portfolios["A"].positions)),
        through_ms=1_600,
        cumulative_cash_amount=-0.25,
        source_verified=True,
    )
    assert duplicate_coverage["duplicate"]
    assert all(
        next(iter(replay.portfolios[variant].positions.values())).funding
        == pytest.approx(-0.25)
        for variant in "ABC"
    )
    funding_fill_ids = [
        next(fill["fill_id"] for fill in replay.portfolios[variant].fills
             if fill["type"] == "FUNDING")
        for variant in "ABC"
    ]
    assert len(set(funding_fill_ids)) == 3




def test_funding_event_inside_reconciled_coverage_is_not_double_counted():
    portfolio, _row, position = opened()
    covered = portfolio.set_funding_coverage(
        position.position_id,
        through_ms=2_000,
        cumulative_cash_amount=-0.75,
        source_verified=True,
    )
    assert covered["covered"]
    duplicate = portfolio.apply_funding_event(
        position.position_id,
        event_id="funding-row-already-covered",
        cash_amount=-0.75,
        ts_ms=1_800,
    )
    assert duplicate["duplicate"]
    assert position.funding == pytest.approx(-0.75)
    assert portfolio.equity_cash == pytest.approx(1_000.0 - position.entry_fee - 0.75)


def test_corrected_funding_event_can_resolve_prior_unknown_with_same_id():
    replay = ShadowReplay()
    source = signal(side="short")
    replay.apply_signal(
        event_id="funding-correction-entry",
        signal=source,
        zone=zone(zones=[]),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )
    position_id = next(iter(replay.portfolios["A"].positions))
    missing = replay.apply_funding_event(
        event_id="funding-row-corrected-later",
        variant="A",
        position_id=position_id,
        cash_amount=None,
        ts_ms=1_500,
    )
    assert missing["unknown"]
    assert replay.portfolios["A"].risk_data_unknown

    corrected = replay.apply_funding_event(
        event_id="funding-row-corrected-later",
        variant="A",
        position_id=position_id,
        cash_amount=-0.10,
        ts_ms=1_500,
    )
    assert corrected["applied"]
    assert not replay.portfolios["A"].risk_data_unknown
    assert replay.portfolios["A"].positions[position_id].funding == pytest.approx(-0.10)


def test_unknown_funding_on_open_position_survives_other_position_close():
    portfolio = VirtualPortfolio(
        "A",
        initial_capital=10_000.0,
        cooldown_ms=0,
        same_side_cluster_max=10,
    )
    first_signal = signal(side="short", symbol="BTC-USDT", signal_id="first")
    first = portfolio.attempt(
        first_signal,
        variant_decision("A", first_signal, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=1_000.0,
        execution_timestamp_ms=1_100,
    )
    second_signal = signal(side="short", symbol="ETH-USDT", signal_id="second")
    second = portfolio.attempt(
        second_signal,
        variant_decision("A", second_signal, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=1_000.0,
        execution_timestamp_ms=1_200,
    )
    first_position_id = first["position_id"]
    second_position_id = second["position_id"]
    portfolio.apply_funding_event(
        first_position_id,
        event_id="missing-on-first",
        cash_amount=None,
        ts_ms=1_500,
    )
    portfolio.set_funding_coverage(
        second_position_id,
        through_ms=2_000,
        cumulative_cash_amount=0.0,
        source_verified=True,
    )
    portfolio.close(
        second_position_id,
        event_id="close-second-only",
        price=95.0,
        quantity=portfolio.positions[second_position_id].remaining_qty,
        ts_ms=2_000,
        reason="profitable_close",
    )
    assert portfolio.risk_data_unknown
    next_signal = signal(side="short", symbol="SOL-USDT", signal_id="third")
    blocked = portfolio.attempt(
        next_signal,
        variant_decision("A", next_signal, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=1_000.0,
        execution_timestamp_ms=3_000,
    )
    assert blocked["reason"] == "UNKNOWN_RISK_DATA"
    assert first_position_id in portfolio.positions



def test_exit_quote_must_arrive_after_bar_was_observed():
    source = signal(side="long")
    portfolio, _row, position = opened(
        source=source,
        price=100.0,
        timestamp=1_100,
    )
    intents = portfolio.process_bar(bar(received=3_020, low=97.0))
    assert intents
    assert intents[0].triggered_at_ms == 3_020
    result = portfolio.execute_pending_exit(
        position.position_id,
        [
            quote("before-bar-received", 3_010, bid=97.0),
            quote("after-bar-received", 3_021, bid=97.0),
        ],
    )
    assert result["quote_id"] == "after-bar-received"


def test_stale_closed_bar_is_explicit_unknown_and_cannot_trigger_exit():
    replay = ShadowReplay()
    source = signal(side="short")
    replay.apply_signal(
        event_id="bar-unknown-entry",
        signal=source,
        zone=zone(zones=[]),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )
    stale = bar(received=33_001)
    result = replay.apply_bar_event(
        event_id="late-candle-event",
        bar=stale,
        quotes=[quote("later-quote", 3_001)],
    )
    assert result["status"] == "UNKNOWN_BAR_DATA"
    assert result["reason"] == "bar_stale"
    assert not result["exit_intents"]
    assert all(
        len(replay.portfolios[name].positions) == 1
        for name in PORTFOLIO_NAMES
    )





def test_future_funding_coverage_cannot_be_used_for_earlier_mark():
    portfolio, _row, position = opened()
    portfolio.set_funding_coverage(
        position.position_id,
        through_ms=3_000,
        cumulative_cash_amount=-0.50,
        source_verified=True,
    )
    mark = portfolio.mark_to_market(
        {"BTC-USDT": 99.0},
        timestamp_ms=2_000,
    )
    assert not mark["known"]
    assert "BTC-USDT:funding" in mark["unknown"]


def test_drawdown_unknown_until_observed_equity_or_mark():
    portfolio = VirtualPortfolio("A")
    assert portfolio.metrics()["drawdown_status"] == "UNKNOWN"
    source = signal(side="short")
    result = portfolio.attempt(
        source,
        variant_decision("A", source, zone(zones=[])),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )
    position_id = result["position_id"]
    portfolio.set_funding_coverage(
        position_id,
        through_ms=2_000,
        cumulative_cash_amount=0.0,
        source_verified=True,
    )
    marked = portfolio.mark_to_market(
        {"BTC-USDT": 101.0},
        timestamp_ms=2_000,
    )
    assert marked["known"]
    assert portfolio.metrics()["drawdown_status"] == "KNOWN"
    assert portfolio.metrics()["drawdown_observations"] == 1



def test_malformed_zone_payload_is_unknown_not_empty_zones():
    result = zone(
        zones=[{"lower": 99.0, "upper": None}],
    )
    assert result.state == ZoneState.UNKNOWN
    assert result.reason == "invalid_zone_payload"


def test_stale_or_future_source_signal_is_unknown_for_all_variants():
    replay = ShadowReplay()
    stale = signal(
        side="short",
        market_received_at_ms=1_000,
        decision_at_ms=32_001,
    )
    result = replay.apply_signal(
        event_id="stale-source",
        signal=stale,
        zone=zone(zones=[]),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=32_002,
    )
    assert all(
        result["variant_results"][name]["reason"]
        == "UNKNOWN_SIGNAL_DATA:stale_market_snapshot"
        for name in ("A", "B", "C")
    )
    assert all(
        result["consensus_decisions"][name]["reason"].startswith(
            "UNKNOWN_CONSENSUS"
        )
        for name in ("2_OF_3", "3_OF_3")
    )
    assert all(not portfolio.positions for portfolio in replay.portfolios.values())

    future_bar = signal(
        side="short",
        source_closed_bar_ms=2_000,
        decision_at_ms=1_000,
    )
    replay2 = ShadowReplay()
    result2 = replay2.apply_signal(
        event_id="future-source-bar",
        signal=future_bar,
        zone=zone(zones=[]),
        execution_price=100.0,
        execution_quantity=100.0,
        execution_timestamp_ms=1_100,
    )
    assert all(
        result2["variant_results"][name]["reason"]
        == "UNKNOWN_SIGNAL_DATA:invalid_or_future_source_bar"
        for name in ("A", "B", "C")
    )
    assert all(
        result2["consensus_decisions"][name]["reason"].startswith(
            "UNKNOWN_CONSENSUS"
        )
        for name in ("2_OF_3", "3_OF_3")
    )
    assert all(not portfolio.positions for portfolio in replay2.portfolios.values())


def test_quote_without_id_and_exit_before_entry_cannot_create_fills():
    assert next_available_quote(
        [ExecutionQuote("", 1_001, 1_001, 99.0, 101.0, 10.0, 10.0)],
        strictly_after_ms=1_000,
    ) is None
    portfolio, _row, position = opened()
    before_entry = portfolio.close(
        position.position_id,
        event_id="impossible-early-exit",
        price=95.0,
        quantity=position.remaining_qty,
        ts_ms=position.opened_at_ms - 1,
        reason="invalid",
    )
    assert before_entry["unknown"]
    assert position.position_id in portfolio.positions


def test_static_isolation_guards_and_offline_container_profile():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    capture = (root / "scripts/zeus_shadow_capture.py").read_text()
    compose = (root / "deploy/shadow/compose.offline.yml").read_text()
    launcher = (root / "deploy/shadow/run-offline.sh").read_text()
    assert ".post(" not in capture
    assert ".delete(" not in capture
    assert "live_arm_wiring" not in capture
    assert "PENDING_PATH" not in capture
    assert "zeus_live_journal" not in capture
    assert "BINGX_API_SECRET" in capture
    assert "network_mode: none" in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert ":ro" in compose
    assert "network_mode: none" in compose
    assert "realpath -m" in launcher
    assert "Refusing shadow data directory inside the repository" in launcher
    assert "Refusing hard-linked shadow data file" in launcher
    assert "run --rm --pull never" in launcher
