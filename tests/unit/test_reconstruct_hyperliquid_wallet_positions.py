from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from scripts.reconstruct_hyperliquid_wallet_positions import _positions_match, reconstruct


def test_reconstructs_flat_to_flat_episodes_and_censors_unknown_positions(tmp_path: Path) -> None:
    trades = tmp_path / "wallet_trades.parquet"
    positions = tmp_path / "positions.parquet"
    account_values = tmp_path / "account_values.parquet"
    cohort = tmp_path / "cohort.json"
    output = tmp_path / "out"

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE fills (
            wallet VARCHAR, market VARCHAR, timestamp TIMESTAMP, price DOUBLE,
            size DOUBLE, notional DOUBLE, side VARCHAR, crossed BOOLEAN,
            liquidation BOOLEAN, tid BIGINT, market_class VARCHAR, dex VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("0xaaa", "BTC", datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc), 10, 1, 10, "buy", True, False, 1, "perp", ""),
            ("0xaaa", "BTC", datetime(2026, 10, 19, 0, 0, 30, tzinfo=timezone.utc), 11, 1, 11, "buy", False, False, 2, "perp", ""),
            ("0xaaa", "BTC", datetime(2026, 10, 19, 0, 4, tzinfo=timezone.utc), 12, 1, 12, "sell", True, False, 3, "perp", ""),
            ("0xaaa", "BTC", datetime(2026, 10, 19, 0, 5, tzinfo=timezone.utc), 13, 1, 13, "sell", True, False, 4, "perp", ""),
            ("0xaaa", "BTC", datetime(2026, 10, 19, 0, 6, tzinfo=timezone.utc), 12, 1, 12, "sell", True, False, 5, "perp", ""),
            ("0xaaa", "BTC", datetime(2026, 10, 19, 0, 7, tzinfo=timezone.utc), 11, 2, 22, "buy", True, False, 6, "perp", ""),
            ("0xaaa", "BTC", datetime(2026, 10, 19, 0, 8, tzinfo=timezone.utc), 12, 1, 12, "buy", False, False, 7, "perp", ""),
            ("0xaaa", "BTC", datetime(2026, 10, 19, 0, 9, tzinfo=timezone.utc), 13, 2, 26, "sell", True, False, 8, "perp", ""),
            ("0xbbb", "ETH", datetime(2026, 10, 19, 0, 4, tzinfo=timezone.utc), 22, 3, 66, "sell", True, False, 9, "perp", ""),
            ("0xddd", "BTC", datetime(2026, 10, 19, 0, 2, 30, tzinfo=timezone.utc), 100, 1, 100, "buy", True, False, 10, "perp", ""),
        ],
    )
    con.execute(f"COPY fills TO '{trades.as_posix()}' (FORMAT PARQUET)")
    con.execute("""
        CREATE TABLE anchor_positions (
            user VARCHAR, market VARCHAR, size DOUBLE, notional DOUBLE,
            entry_price DOUBLE, liquidation_price DOUBLE, leverage_type VARCHAR,
            leverage DOUBLE, funding_pnl DOUBLE, account_value DOUBLE, account_mode VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO anchor_positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("0xaaa", "BTC", 2, 22, 10.5, 5, "cross", 3, -0.2, 1000, "standard"),
            ("0xbbb", "ETH", 3, 60, 20, 10, "isolated", 5, 0.1, 1000, "standard"),
            ("0xccc", "SOL", -1, 50, 50, 80, "cross", 2, -0.1, 1000, "standard"),
            ("0xddd", "BTC", 1, 100, 100, 50, "isolated", 4, 0, 1000, "standard"),
        ],
    )
    con.execute(f"COPY anchor_positions TO '{positions.as_posix()}' (FORMAT PARQUET)")
    con.execute("""
        CREATE TABLE accounts (
            user VARCHAR, dex VARCHAR, collateral_token VARCHAR, account_value DOUBLE,
            total_long_notional DOUBLE, total_short_notional DOUBLE,
            account_mode VARCHAR, is_unified BOOLEAN, pm_ratio DOUBLE
        )
    """)
    con.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(address, "", "USDC", 1000, 200, 100, "standard", False, None)
         for address in ("0xaaa", "0xbbb", "0xccc", "0xddd")],
    )
    con.execute(f"COPY accounts TO '{account_values.as_posix()}' (FORMAT PARQUET)")
    con.close()

    cohort.write_text(json.dumps({
        "wallets": [
            {"ethAddress": address, "windowPerformances": [["month", {"pnl": 10, "roi": 0.1, "vlm": 1000}]]}
            for address in ("0xaaa", "0xbbb", "0xccc", "0xddd")
        ]
    }), encoding="utf-8")

    report = reconstruct(trades, positions, account_values, cohort, output)

    with (output / "position_episodes.csv").open(newline="", encoding="utf-8") as handle:
        episodes = list(csv.DictReader(handle))
    btc = [row for row in episodes if row["wallet"] == "0xaaa" and row["market"] == "BTC"]
    assert len(btc) == 3
    assert [row["side"] for row in btc] == ["long", "short", "long"]
    assert float(btc[0]["hold_minutes"]) == 5.0
    assert float(btc[0]["gross_realized_pnl_known_usd"]) == 4.0
    assert float(btc[1]["gross_realized_pnl_known_usd"]) == 1.0
    assert float(btc[2]["gross_realized_pnl_known_usd"]) == 3.0
    assert btc[0]["pnl_complete"] == "True"

    eth = [row for row in episodes if row["wallet"] == "0xbbb"]
    assert len(eth) == 1
    assert eth[0]["left_censored"] == "True"
    assert eth[0]["hold_minutes"] == ""
    assert float(eth[0]["gross_realized_pnl_known_usd"]) == 6.0
    assert eth[0]["pnl_complete"] == "False"

    sol = [row for row in episodes if row["wallet"] == "0xccc"]
    assert len(sol) == 1
    assert sol[0]["left_censored"] == "True"
    assert sol[0]["right_censored"] == "True"

    assert report["counts"]["wallet_market_groups_boundary_ambiguous"] == 1
    assert report["counts"]["anchor_entry_price_matches_005pct"] == 1
    assert report["counts"]["complete_position_episodes"] == 3


def test_position_match_allows_only_small_float_residuals() -> None:
    assert _positions_match(-6.705522537231445e-08, 3.3527612686157227e-08)
    assert not _positions_match(0.0001, 0.0)
