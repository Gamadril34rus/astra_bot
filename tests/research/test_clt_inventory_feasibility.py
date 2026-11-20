from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import scripts.research.clt_inventory_feasibility as inventory
from scripts.research.clt_inventory_feasibility import (
    MAX_FILLS_PER_ADDRESS,
    count_net_add_wallets,
    order_timestamp_group,
    order_timestamp_groups_and_check_continuity,
    possible_direction_labels,
    rolling_unique_add_wallet_counts,
    select_wallets,
    split_fill_inventory,
    split_market_cohorts,
)


def test_audit_keeps_every_fill_in_a_multi_fill_timestamp_group(tmp_path, monkeypatch):
    start = datetime(2026, 10, 3, tzinfo=UTC)
    start_ms = int(start.timestamp() * 1000)
    selected = [
        {
            "address": f"0x{index:040x}",
            "manifest_fills": None,
        }
        for index in range(1, 7)
    ]

    history = {}
    for wallet in selected:
        address = wallet["address"]
        history[address] = [
            {
                "time": start_ms,
                "coin": "BTC",
                "startPosition": str(position),
                "sz": "1",
                "px": "100",
                "side": "B",
                "dir": "Open Long",
                "tid": index + 1,
                "liquidation": False,
            }
            for index, position in enumerate((0, 1, 2))
        ]

    monkeypatch.setattr(
        inventory,
        "select_wallets",
        lambda *_args, **_kwargs: selected,
    )
    monkeypatch.setattr(
        inventory,
        "fetch_wallet_history",
        lambda address, **_kwargs: (
            history[address],
            {
                "pages": 1,
                "api_fills": 3,
                "twap_fills": 0,
                "api_cap_reached": False,
                "twap_cap_reached": False,
                "page_boundary_collision": False,
                "boundary_collision_markets": set(),
                "boundary_recovered_markets": set(),
            },
        ),
    )

    report = inventory.audit(
        tmp_path / "unused-manifest.json",
        tmp_path / "out",
        start=start,
        end=start + timedelta(days=8),
        max_wallets=6,
    )

    btc = report["by_market"]["BTC-PERP"]
    assert report["cohort_calibration_candidates_by_market"]["BTC-PERP"] == 6
    # 5 of 6 wallets are assigned to the disjoint small/large cohorts.
    assert btc["calibrated_wallets"] == 5
    assert btc["calibration_fills"] == 15


def test_fill_key_uses_unique_trade_id_for_twap_slices():
    base = {
        "tid": 123,
        "twapId": 7,
        "time": 100,
        "oid": 10,
        "coin": "BTC",
        "side": "B",
        "px": "100",
        "sz": "1",
    }
    duplicate = dict(base)
    different_fill = {**base, "tid": 124}

    assert inventory._fill_key(base, twap=True) == inventory._fill_key(duplicate, twap=True)
    assert inventory._fill_key(base, twap=True) != inventory._fill_key(
        different_fill, twap=True
    )


def test_buy_from_flat_opens_long():
    result = split_fill_inventory(0.0, "B", 2.0, 100.0)
    assert result["open_long_usd"] == 200.0
    assert result["close_short_usd"] == 0.0
    assert result["position_after"] == 2.0


def test_buy_against_short_closes_then_flips_to_long():
    result = split_fill_inventory(-2.0, "B", 3.0, 100.0)
    assert result["close_short_usd"] == 200.0
    assert result["open_long_usd"] == 100.0
    assert result["position_after"] == 1.0


def test_sell_against_long_closes_then_flips_to_short():
    result = split_fill_inventory(2.0, "A", 3.0, 100.0)
    assert result["close_long_usd"] == 200.0
    assert result["open_short_usd"] == 100.0
    assert result["position_after"] == -1.0


def test_add_and_reduce_existing_short():
    add = split_fill_inventory(-2.0, "A", 1.0, 50.0)
    close = split_fill_inventory(-2.0, "B", 1.0, 50.0)
    assert add["open_short_usd"] == 50.0
    assert close["close_short_usd"] == 50.0


def test_invalid_fill_values_fail_closed():
    try:
        split_fill_inventory(0.0, "B", 0.0, 100.0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("zero-size fill must fail")


def test_wallet_selection_respects_public_api_history_ceiling(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        """
        {
          "wallet_summaries": [
            {
              "address": "0x1111111111111111111111111111111111111111",
              "native_perp_fills": 10001,
              "top_markets_by_notional": [{"market": "BTC-PERP"}]
            },
            {
              "address": "0x2222222222222222222222222222222222222222",
              "native_perp_fills": 10000,
              "top_markets_by_notional": [{"market": "ETH-PERP"}]
            },
            {
              "address": "0x3333333333333333333333333333333333333333",
              "native_perp_fills": 0,
              "top_markets_by_notional": [{"market": "SOL-PERP"}]
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    selected = select_wallets(manifest, max_wallets=10)
    assert len(selected) == 1
    assert selected[0]["manifest_fills"] == MAX_FILLS_PER_ADDRESS



def test_direction_labels_allow_open_label_for_add_to_existing_position():
    assert possible_direction_labels(2.0, "B", 1.0) == {"Open Long", "Add Long"}
    assert possible_direction_labels(-2.0, "A", 1.0) == {"Open Short", "Add Short"}


def test_direction_labels_split_close_and_flip():
    assert possible_direction_labels(-2.0, "B", 1.0) == {"Close Short"}
    assert possible_direction_labels(-2.0, "B", 3.0) == {
        "Short > Long", "Open Long", "Add Long"
    }
    assert possible_direction_labels(2.0, "A", 3.0) == {
        "Long > Short", "Open Short", "Add Short"
    }



def test_order_timestamp_group_recovers_position_path_without_tid_order():
    fills = [
        {"startPosition": "1.0", "sz": "2.0", "side": "B", "tid": 99},
        {"startPosition": "0.0", "sz": "1.0", "side": "B", "tid": 1},
        {"startPosition": "3.0", "sz": "1.0", "side": "A", "tid": 7},
    ]
    ordered = order_timestamp_group(fills)
    assert ordered is not None
    assert [float(row["startPosition"]) for row in ordered] == [0.0, 1.0, 3.0]


def test_order_timestamp_group_rejects_ambiguous_parallel_edges():
    fills = [
        {"startPosition": "0.0", "sz": "1.0", "side": "B"},
        {"startPosition": "0.0", "sz": "2.0", "side": "B"},
    ]
    assert order_timestamp_group(fills) is None



def test_ambiguous_timestamp_group_is_excluded_from_continuity_checks():
    fills = [
        {"time": 100, "startPosition": "0", "sz": "1", "side": "B"},
        {"time": 200, "startPosition": "1", "sz": "1", "side": "B"},
        {"time": 200, "startPosition": "1", "sz": "2", "side": "B"},
        {"time": 300, "startPosition": "3", "sz": "1", "side": "B"},
        {"time": 400, "startPosition": "4", "sz": "1", "side": "B"},
    ]

    groups, stats = order_timestamp_groups_and_check_continuity(fills)

    assert stats["ambiguous_timestamp_groups"] == 1
    assert stats["checked_transitions"] == 1
    assert stats["continuity_failures"] == 0
    assert [is_ordered for _, is_ordered in groups] == [True, False, True, True]


def test_unique_timestamp_groups_count_real_position_discontinuity():
    fills = [
        {"time": 100, "startPosition": "0", "sz": "1", "side": "B"},
        {"time": 200, "startPosition": "3", "sz": "1", "side": "B"},
        {"time": 300, "startPosition": "4", "sz": "1", "side": "B"},
    ]

    _, stats = order_timestamp_groups_and_check_continuity(fills)

    assert stats["checked_transitions"] == 2
    assert stats["continuity_failures"] == 1
    assert stats["ambiguous_timestamp_groups"] == 0


def test_fetch_wallet_history_preserves_outer_twap_id_for_deduplication(monkeypatch):
    monkeypatch.setattr(inventory.time, "sleep", lambda _: None)
    fill = {
        "time": 100,
        "tid": 999,
        "oid": 7,
        "coin": "BTC",
        "side": "B",
        "px": "100",
        "sz": "1",
        "startPosition": "0",
        "dir": "Open Long",
    }
    second_fill = {**fill, "tid": 1000}
    twap_rows = [
        {"fill": dict(fill), "twapId": 11},
        {"fill": dict(second_fill), "twapId": 11},
        {"fill": dict(fill), "twapId": 11},
    ]
    calls = iter([[], twap_rows])
    monkeypatch.setattr(inventory, "post_info", lambda payload: next(calls))

    rows, stats = inventory.fetch_wallet_history(
        "0x" + "3" * 40, start_ms=0, end_ms=1_000
    )

    assert len(rows) == 2
    assert stats["twap_fills"] == 2
    assert {row["tid"] for row in rows} == {999, 1000}
    assert {row["twapId"] for row in rows} == {11}


def test_public_leaderboard_sampling_is_stratified_and_deterministic(tmp_path):
    rows = []
    for index in range(100):
        rows.append({
            "ethAddress": f"0x{index + 1:040x}",
            "accountValue": "1000",
            "windowPerformances": [
                ["day", {"pnl": "1", "roi": "0.1", "vlm": "100"}],
                ["month", {"pnl": "10", "roi": "0.2", "vlm": str(index + 1)}],
            ],
        })
    path = tmp_path / "leaderboard.json"
    path.write_text(json.dumps({"leaderboardRows": rows}), encoding="utf-8")
    first = select_wallets(path, max_wallets=10)
    second = select_wallets(path, max_wallets=10)
    assert first == second
    assert len(first) == 10
    assert all(row["manifest_fills"] is None for row in first)
    assert len({row["address"] for row in first}) == 10


def test_public_leaderboard_ignores_zero_volume_and_bad_addresses(tmp_path):
    path = tmp_path / "leaderboard.json"
    path.write_text(json.dumps({"leaderboardRows": [
        {
            "ethAddress": "0x" + "1" * 40,
            "windowPerformances": [["month", {"vlm": "0"}]],
        },
        {
            "ethAddress": "not-an-address",
            "windowPerformances": [["month", {"vlm": "100"}]],
        },
        {
            "ethAddress": "0x" + "2" * 40,
            "windowPerformances": [["month", {"vlm": "100"}]],
        },
    ]}), encoding="utf-8")
    selected = select_wallets(path, max_wallets=10)
    assert len(selected) == 1
    assert selected[0]["month_volume"] == 100.0



def test_market_cohort_split_requires_three_distinct_crowd_wallets():
    assert split_market_cohorts([(1.0, "a"), (2.0, "b"), (3.0, "c"), (4.0, "d"), (5.0, "e")]) == {
        "small": set(), "large": set()
    }
    cohorts = split_market_cohorts([
        (1.0, "a"), (2.0, "b"), (3.0, "c"),
        (4.0, "d"), (5.0, "e"), (6.0, "f"),
    ])
    assert cohorts["small"] == {"a", "b", "c"}
    assert cohorts["large"] == {"e", "f"}
    assert cohorts["small"].isdisjoint(cohorts["large"])



def test_crowd_wallet_count_uses_net_inventory_add_not_any_open_fill():
    values = {
        "small_long_net_wallet_a": 100.0,
        "small_long_net_wallet_b": -20.0,
        "small_long_net_wallet_c": 0.0,
        "small_long_net_wallet_d": 30.0,
        "large_short_net_wallet_e": 90.0,
    }
    assert count_net_add_wallets(values, cohort="small", direction="long") == 2
    assert count_net_add_wallets(values, cohort="large", direction="short") == 1


def test_net_add_wallet_count_rejects_unknown_cohort_or_direction():
    try:
        count_net_add_wallets({}, cohort="retail", direction="long")
    except ValueError as exc:
        assert "cohort" in str(exc)
    else:
        raise AssertionError("unknown cohort must fail")



def test_rolling_unique_add_counts_include_empty_current_bars_and_exclude_current_bar():
    from scripts.research.clt_inventory_feasibility import BAR_MS

    buckets = {
        ("BTC", "test", 0): {
            "small_long_net_wallet_a": 100.0,
            "small_long_net_wallet_b": -20.0,
            "small_short_net_wallet_c": 50.0,
            "large_short_net_wallet_d": 75.0,
        },
        ("BTC", "test", BAR_MS): {
            "small_long_net_wallet_a": 10.0,
            "small_long_net_wallet_e": 25.0,
        },
        ("BTC", "test", 2 * BAR_MS): {
            "small_long_net_wallet_z": 999.0,
        },
        ("BTC", "calibration", 0): {
            "small_long_net_wallet_out_of_phase": 500.0,
        },
    }
    counts = rolling_unique_add_wallet_counts(
        buckets,
        market="BTC",
        phase="test",
        bucket_ms=2 * BAR_MS,
        max_window=2,
    )
    assert counts["small_long_add_wallets_prev_1"] == 2
    assert counts["small_long_add_wallets_prev_2"] == 2
    assert counts["small_short_add_wallets_prev_2"] == 1
    assert counts["large_short_add_wallets_prev_2"] == 1
    counts_12 = rolling_unique_add_wallet_counts(
        buckets,
        market="BTC",
        phase="test",
        bucket_ms=2 * BAR_MS,
        max_window=12,
    )
    assert counts_12["small_long_add_wallets_prev_12"] == 2
    default_counts = rolling_unique_add_wallet_counts(
        buckets,
        market="BTC",
        phase="test",
        bucket_ms=2 * BAR_MS,
    )
    assert default_counts["small_long_add_wallets_prev_12"] == 2


def test_rolling_unique_add_counts_are_phase_scoped_and_validate_window():
    from scripts.research.clt_inventory_feasibility import BAR_MS

    buckets = {
        ("BTC", "calibration", 0): {"small_long_net_wallet_a": 100.0},
    }
    counts = rolling_unique_add_wallet_counts(
        buckets,
        market="BTC",
        phase="test",
        bucket_ms=BAR_MS,
        max_window=1,
    )
    assert counts["small_long_add_wallets_prev_1"] == 0
    try:
        rolling_unique_add_wallet_counts(
            buckets,
            market="BTC",
            phase="test",
            bucket_ms=BAR_MS,
            max_window=13,
        )
    except ValueError as exc:
        assert "between 1 and 12" in str(exc)
    else:
        raise AssertionError("oversized rolling window must fail")



def test_fetch_wallet_history_recovers_all_fills_at_page_boundary(monkeypatch):
    monkeypatch.setattr(inventory, "PAGE_SIZE", 3)
    monkeypatch.setattr(inventory, "MAX_FILLS_PER_ADDRESS", 10)
    monkeypatch.setattr(inventory.time, "sleep", lambda _: None)
    page = [
        {"time": 100, "tid": 1, "coin": "BTC"},
        {"time": 150, "tid": 2, "coin": "BTC"},
        {"time": 200, "tid": 3, "coin": "BTC"},
    ]
    boundary = [
        {"time": 200, "tid": 3, "coin": "BTC"},
        {"time": 200, "tid": 4, "coin": "BTC"},
    ]
    calls = iter([page, boundary, [], []])
    monkeypatch.setattr(inventory, "post_info", lambda payload: next(calls))

    rows, stats = inventory.fetch_wallet_history(
        "0x" + "1" * 40, start_ms=0, end_ms=1_000
    )

    assert {row["tid"] for row in rows} == {1, 2, 3, 4}
    assert stats["page_boundary_collision"] is False
    assert stats["boundary_collision_markets"] == []
    assert stats["boundary_recovered_markets"] == ["BTC"]


def test_fetch_wallet_history_fails_closed_when_boundary_probe_is_capped(monkeypatch):
    monkeypatch.setattr(inventory, "PAGE_SIZE", 3)
    monkeypatch.setattr(inventory, "MAX_FILLS_PER_ADDRESS", 10)
    monkeypatch.setattr(inventory.time, "sleep", lambda _: None)
    page = [
        {"time": 100, "tid": 1, "coin": "BTC"},
        {"time": 200, "tid": 2, "coin": "BTC"},
        {"time": 200, "tid": 3, "coin": "BTC"},
    ]
    boundary = [
        {"time": 200, "tid": index, "coin": "BTC"}
        for index in range(10, 13)
    ]
    calls = iter([page, boundary, []])
    monkeypatch.setattr(inventory, "post_info", lambda payload: next(calls))

    _rows, stats = inventory.fetch_wallet_history(
        "0x" + "2" * 40, start_ms=0, end_ms=1_000
    )

    assert stats["page_boundary_collision"] is True
    assert stats["boundary_collision_markets"] == ["BTC"]
    assert stats["boundary_recovered_markets"] == []
