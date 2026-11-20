from __future__ import annotations

import json

from scripts.research.clt_inventory_feasibility import (
    MAX_FILLS_PER_ADDRESS,
    order_timestamp_group,
    possible_direction_labels,
    select_wallets,
    split_fill_inventory,
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
