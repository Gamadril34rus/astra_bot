#!/usr/bin/env python3
"""Summarise historical public-wallet trading behaviour without inventing positions or PnL."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def analyze(input_path: Path, manifest_path: Path, out_dir: Path) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads = 4")
    source = sql_path(input_path)

    schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{source}')").fetchall()
    columns = {row[0] for row in schema}
    required = {
        "timestamp", "market", "price", "size", "notional", "side",
        "crossed", "wallet", "liquidation", "tid",
    }
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"wallet trade archive missing columns: {missing}")

    row_count = int(con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{source}')"
    ).fetchone()[0])
    wallet_count, market_count = con.execute(
        f"SELECT COUNT(DISTINCT wallet), COUNT(DISTINCT market) FROM read_parquet('{source}')"
    ).fetchone()

    wallet_market_query = f"""
        WITH ordered AS (
            SELECT
                wallet, market, timestamp, side, crossed, notional, price,
                LAG(timestamp) OVER (
                    PARTITION BY wallet, market ORDER BY timestamp, tid
                ) AS previous_timestamp,
                LAG(side) OVER (
                    PARTITION BY wallet, market ORDER BY timestamp, tid
                ) AS previous_side
            FROM read_parquet('{source}')
        ),
        gaps AS (
            SELECT
                wallet, market,
                date_diff('microseconds', previous_timestamp, timestamp) / 1000000.0 AS gap_seconds,
                side, previous_side
            FROM ordered
            WHERE previous_timestamp IS NOT NULL
        ),
        gap_stats AS (
            SELECT
                wallet, market,
                COUNT(*) AS intervals_observed,
                quantile_cont(gap_seconds, 0.5) AS median_gap_seconds,
                quantile_cont(gap_seconds, 0.9) AS p90_gap_seconds,
                AVG(CASE WHEN gap_seconds < 5 THEN 1.0 ELSE 0.0 END) AS share_gaps_under_5s,
                SUM(CASE WHEN side <> previous_side THEN 1 ELSE 0 END) AS side_flips
            FROM gaps
            GROUP BY wallet, market
        )
        SELECT
            t.wallet,
            t.market,
            COUNT(*) AS trade_rows,
            COUNT(DISTINCT CAST(t.timestamp AS DATE)) AS active_days,
            COUNT(DISTINCT date_trunc('hour', t.timestamp)) AS active_hours,
            SUM(t.notional) AS gross_notional,
            SUM(CASE WHEN t.side = 'buy' THEN t.notional ELSE 0 END) AS buy_notional,
            SUM(CASE WHEN t.side = 'sell' THEN t.notional ELSE 0 END) AS sell_notional,
            SUM(CASE WHEN t.side = 'buy' THEN t.notional ELSE -t.notional END) AS net_buy_notional,
            SUM(CASE WHEN t.side = 'buy' THEN t.size ELSE -t.size END) AS net_buy_size_change,
            AVG(CASE WHEN t.crossed THEN 1.0 ELSE 0.0 END) AS taker_share,
            AVG(CASE WHEN NOT t.crossed THEN 1.0 ELSE 0.0 END) AS maker_share,
            AVG(CASE WHEN t.liquidation THEN 1.0 ELSE 0.0 END) AS liquidation_interaction_share,
            AVG(t.notional) AS mean_trade_notional,
            quantile_cont(t.notional, 0.5) AS median_trade_notional,
            quantile_cont(t.notional, 0.9) AS p90_trade_notional,
            SUM(t.price * t.size) FILTER (WHERE t.side = 'buy')
                / NULLIF(SUM(t.size) FILTER (WHERE t.side = 'buy'), 0) AS buy_vwap,
            SUM(t.price * t.size) FILTER (WHERE t.side = 'sell')
                / NULLIF(SUM(t.size) FILTER (WHERE t.side = 'sell'), 0) AS sell_vwap,
            g.intervals_observed,
            g.median_gap_seconds,
            g.p90_gap_seconds,
            g.share_gaps_under_5s,
            g.side_flips
        FROM read_parquet('{source}') t
        LEFT JOIN gap_stats g USING (wallet, market)
        GROUP BY t.wallet, t.market, g.intervals_observed, g.median_gap_seconds,
                 g.p90_gap_seconds, g.share_gaps_under_5s, g.side_flips
        ORDER BY gross_notional DESC
    """
    wallet_market_path = out_dir / "wallet_market_behavior.csv"
    con.execute(
        f"COPY ({wallet_market_query}) TO '{sql_path(wallet_market_path)}' "
        "(HEADER, DELIMITER ',')"
    )

    wallet_bin_query = f"""
        SELECT
            wallet,
            market,
            time_bucket(INTERVAL '5 minutes', timestamp) AS bucket_5m_utc,
            SUM(CASE WHEN side = 'buy' THEN notional ELSE -notional END) AS net_flow,
            SUM(notional) AS gross_notional,
            COUNT(*) AS trade_rows,
            SUM(CASE WHEN crossed THEN 1 ELSE 0 END) AS taker_rows
        FROM read_parquet('{source}')
        GROUP BY wallet, market, bucket_5m_utc
    """
    wallet_bin_path = out_dir / "wallet_market_flow_5m.parquet"
    con.execute(
        f"COPY ({wallet_bin_query}) TO '{sql_path(wallet_bin_path)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )

    market_bin_query = f"""
        SELECT
            market,
            bucket_5m_utc,
            SUM(net_flow) AS cohort_net_flow,
            SUM(gross_notional) AS cohort_gross_notional,
            SUM(trade_rows) AS trade_rows,
            COUNT(*) AS active_wallet_market_rows,
            SUM(CASE WHEN net_flow > 0 THEN 1 ELSE 0 END) AS net_buy_wallets,
            SUM(CASE WHEN net_flow < 0 THEN 1 ELSE 0 END) AS net_sell_wallets,
            SUM(CASE WHEN net_flow = 0 THEN 1 ELSE 0 END) AS balanced_wallets
        FROM ({wallet_bin_query})
        GROUP BY market, bucket_5m_utc
        ORDER BY market, bucket_5m_utc
    """
    market_bin_path = out_dir / "market_cohort_flow_5m.csv"
    con.execute(
        f"COPY ({market_bin_query}) TO '{sql_path(market_bin_path)}' "
        "(HEADER, DELIMITER ',')"
    )

    pair_query = f"""
        WITH wallet_bins AS ({wallet_bin_query}),
        directional AS (
            SELECT wallet, market, bucket_5m_utc, SIGN(net_flow) AS direction
            FROM wallet_bins
            WHERE net_flow <> 0
        )
        SELECT
            a.market,
            a.wallet AS wallet_a,
            b.wallet AS wallet_b,
            COUNT(*) AS shared_directional_5m_bins,
            SUM(CASE WHEN a.direction = b.direction THEN 1 ELSE 0 END) AS same_direction_bins,
            SUM(CASE WHEN a.direction <> b.direction THEN 1 ELSE 0 END) AS opposite_direction_bins,
            AVG(CASE WHEN a.direction = b.direction THEN 1.0 ELSE 0.0 END) AS directional_concordance
        FROM directional a
        JOIN directional b
          ON a.market = b.market
         AND a.bucket_5m_utc = b.bucket_5m_utc
         AND a.wallet < b.wallet
        GROUP BY a.market, a.wallet, b.wallet
        HAVING COUNT(*) >= 50
        ORDER BY shared_directional_5m_bins DESC
    """
    pair_path = out_dir / "wallet_synchrony_pairs.csv"
    con.execute(
        f"COPY ({pair_query}) TO '{sql_path(pair_path)}' "
        "(HEADER, DELIMITER ',')"
    )

    per_market = con.execute(f"""
        SELECT market, COUNT(*) AS trade_rows,
               COUNT(DISTINCT wallet) AS wallets,
               SUM(notional) AS gross_notional,
               SUM(CASE WHEN side = 'buy' THEN notional ELSE -notional END) AS net_buy_notional
        FROM read_parquet('{source}')
        GROUP BY market
        ORDER BY gross_notional DESC
    """).fetchall()
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "input": {
            "path": input_path.name,
            "sha256": sha256_file(input_path),
            "rows": row_count,
            "wallets": int(wallet_count),
            "markets": int(market_count),
        },
        "source_window_utc": {
            "start": manifest.get("source", {}).get("start_date_utc"),
            "end": manifest.get("source", {}).get("end_date_utc"),
        },
        "outputs": {
            "wallet_market_behavior": wallet_market_path.name,
            "wallet_market_flow_5m": wallet_bin_path.name,
            "market_cohort_flow_5m": market_bin_path.name,
            "wallet_synchrony_pairs": pair_path.name,
        },
        "market_summary": [
            {
                "market": row[0],
                "trade_rows": int(row[1]),
                "wallets": int(row[2]),
                "gross_notional": float(row[3]),
                "net_buy_notional": float(row[4]),
            }
            for row in per_market
        ],
        "interpretation_limits": [
            "The trade tape identifies buyer, seller and taker side; it does not identify each wallet's starting position.",
            "net_buy_size_change is only cumulative signed flow over the archive window, not a reconstructed position or exposure.",
            "Trade rows do not provide per-wallet closed PnL, fees, funding, leverage, margin or liquidation price.",
            "Five-minute directional concordance measures simultaneous cohort flow, not common ownership or copyable signals.",
            "Current leaderboard PnL strata are a snapshot label and are not a historical point-in-time profitability label.",
            "This is Hyperliquid cross-venue evidence, not BingX fills, prices or execution costs.",
        ],
    }
    (out_dir / "wallet_behavior_analysis_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    con.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/hyperliquid_wallet_behavior"))
    args = parser.parse_args()
    report = analyze(args.input, args.manifest, args.out_dir)
    print(json.dumps({
        "rows": report["input"]["rows"],
        "wallets": report["input"]["wallets"],
        "markets": report["input"]["markets"],
        "outputs": report["outputs"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
