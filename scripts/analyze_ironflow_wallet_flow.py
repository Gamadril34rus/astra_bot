#!/usr/bin/env python3
"""Study 15m signed wallet flow and its subsequent native-market price response."""
# ruff: noqa: I001
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


HORIZONS = {
    "15m": "close_15m",
    "30m": "close_30m",
    "1h": "close_1h",
    "4h": "close_4h",
    "9h": "close_9h",
}


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze(wallet_trades_path: Path, market_bars_path: Path, manifest_path: Path, out_dir: Path) -> dict[str, Any]:
    import duckdb

    out_dir.mkdir(parents=True, exist_ok=True)
    wallet_flow_path = out_dir / "wallet_flow_15m.parquet"
    cohort_flow_path = out_dir / "cohort_flow_15m.parquet"
    response_path = out_dir / "cohort_flow_forward_returns.parquet"
    event_study_path = out_dir / "cohort_flow_event_study.csv"
    summary_path = out_dir / "wallet_flow_analysis.json"

    connection = duckdb.connect()
    connection.execute("SET memory_limit='8GB'")
    connection.execute("SET threads=2")
    connection.execute(
        f"CREATE VIEW wallet_trades AS SELECT * FROM read_parquet('{_sql_path(wallet_trades_path)}')"
    )
    connection.execute(
        f"CREATE VIEW market_bars AS SELECT * FROM read_parquet('{_sql_path(market_bars_path)}')"
    )

    connection.execute(
        f"""
        COPY (
            SELECT
                wallet,
                market,
                time_bucket(INTERVAL '15 minutes', timestamp) AS bucket_ts,
                sum(CASE WHEN side = 'buy' THEN notional ELSE -notional END)
                    AS signed_notional,
                sum(notional) AS participant_notional,
                sum(CASE WHEN crossed
                    THEN CASE WHEN side = 'buy' THEN notional ELSE -notional END
                    ELSE 0 END) AS aggressive_signed_notional,
                sum(CASE WHEN crossed THEN notional ELSE 0 END) AS taker_notional,
                count(*) AS fills,
                sum(CASE WHEN crossed THEN 1 ELSE 0 END) AS taker_fills,
                sum(CASE WHEN liquidation THEN 1 ELSE 0 END)
                    AS liquidation_trade_interactions,
                avg(price) AS participant_vwap
            FROM wallet_trades
            GROUP BY wallet, market, bucket_ts
        )
        TO '{_sql_path(wallet_flow_path)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    connection.execute(
        f"CREATE VIEW wallet_flow AS SELECT * FROM read_parquet('{_sql_path(wallet_flow_path)}')"
    )
    connection.execute(
        f"""
        COPY (
            SELECT
                market,
                bucket_ts,
                sum(signed_notional) AS cohort_signed_notional,
                sum(participant_notional) AS cohort_participant_notional,
                sum(aggressive_signed_notional) AS cohort_aggressive_signed_notional,
                sum(taker_notional) AS cohort_taker_notional,
                count(DISTINCT wallet) AS active_wallets,
                sum(CASE WHEN signed_notional > 0 THEN 1 ELSE 0 END) AS net_buy_wallets,
                sum(CASE WHEN signed_notional < 0 THEN 1 ELSE 0 END) AS net_sell_wallets,
                sum(fills) AS wallet_side_fills,
                sum(taker_fills) AS wallet_side_taker_fills,
                sum(liquidation_trade_interactions) AS liquidation_trade_interactions
            FROM wallet_flow
            GROUP BY market, bucket_ts
        )
        TO '{_sql_path(cohort_flow_path)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    connection.execute(
        f"CREATE VIEW cohort_flow AS SELECT * FROM read_parquet('{_sql_path(cohort_flow_path)}')"
    )

    forward_selects = []
    for label in HORIZONS:
        minutes = {"15m": 15, "30m": 30, "1h": 60, "4h": 240, "9h": 540}[label]
        forward_selects.append(
            f"LEFT JOIN market_bars f_{label} "
            f"ON f_{label}.market = c.market "
            f"AND f_{label}.bucket_ts = c.bucket_ts + INTERVAL '{minutes} minutes'"
        )
    connection.execute(
        f"""
        COPY (
            SELECT
                c.*,
                b.close AS close_t,
                f_15m.close AS close_15m,
                f_30m.close AS close_30m,
                f_1h.close AS close_1h,
                f_4h.close AS close_4h,
                f_9h.close AS close_9h,
                CASE WHEN c.cohort_participant_notional > 0
                    THEN c.cohort_signed_notional / c.cohort_participant_notional
                    ELSE NULL END AS flow_ratio,
                CASE WHEN c.cohort_participant_notional > 0
                    THEN c.cohort_aggressive_signed_notional / c.cohort_participant_notional
                    ELSE NULL END AS aggressive_flow_ratio,
                CASE WHEN c.active_wallets > 0
                    THEN (c.net_buy_wallets - c.net_sell_wallets)::DOUBLE / c.active_wallets
                    ELSE NULL END AS wallet_consensus_ratio
            FROM cohort_flow c
            INNER JOIN market_bars b
                ON b.market = c.market AND b.bucket_ts = c.bucket_ts
            {' '.join(forward_selects)}
        )
        TO '{_sql_path(response_path)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    connection.execute(
        f"CREATE VIEW flow_response AS SELECT * FROM read_parquet('{_sql_path(response_path)}')"
    )

    event_rows = []
    for horizon, close_column in HORIZONS.items():
        query = f"""
            WITH sample AS (
                SELECT
                    *,
                    sign(flow_ratio) *
                    100.0 * ({close_column} / close_t - 1.0) AS directional_return_pct
                FROM flow_response
                WHERE active_wallets >= 3
                  AND flow_ratio IS NOT NULL
                  AND abs(flow_ratio) > 0
                  AND close_t > 0
                  AND {close_column} IS NOT NULL
                  AND {close_column} > 0
            ),
            ranked AS (
                SELECT
                    *,
                    ntile(10) OVER (ORDER BY flow_ratio) AS flow_decile
                FROM sample
            )
            SELECT
                flow_decile,
                count(*) AS observations,
                avg(flow_ratio) AS mean_flow_ratio,
                avg(directional_return_pct) AS mean_directional_return_pct,
                median(directional_return_pct) AS median_directional_return_pct,
                100.0 * avg(CASE WHEN directional_return_pct > 0 THEN 1.0 ELSE 0.0 END)
                    AS directional_hit_rate_pct,
                avg(active_wallets) AS mean_active_wallets
            FROM ranked
            GROUP BY flow_decile
            ORDER BY flow_decile
        """
        result = connection.execute(query)
        columns = [item[0] for item in result.description]
        for values in result.fetchall():
            event_rows.append({
                "horizon": horizon,
                **{
                    key: (int(value) if key in {"flow_decile", "observations"} else
                          float(value) if value is not None else None)
                    for key, value in zip(columns, values, strict=True)
                },
            })

    with event_study_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "horizon", "flow_decile", "observations", "mean_flow_ratio",
            "mean_directional_return_pct", "median_directional_return_pct",
            "directional_hit_rate_pct", "mean_active_wallets",
        ])
        writer.writeheader()
        writer.writerows(event_rows)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wallet_flow_rows = connection.execute(
        f"SELECT count(*) FROM read_parquet('{_sql_path(wallet_flow_path)}')"
    ).fetchone()[0]
    cohort_flow_rows = connection.execute(
        f"SELECT count(*) FROM read_parquet('{_sql_path(cohort_flow_path)}')"
    ).fetchone()[0]
    response_rows = connection.execute(
        f"SELECT count(*) FROM read_parquet('{_sql_path(response_path)}')"
    ).fetchone()[0]
    active_market_count = connection.execute(
        f"SELECT count(DISTINCT market) FROM read_parquet('{_sql_path(market_bars_path)}')"
    ).fetchone()[0]
    market_bar_rows = connection.execute(
        f"SELECT count(*) FROM read_parquet('{_sql_path(market_bars_path)}')"
    ).fetchone()[0]
    connection.close()

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": {
            "dataset": manifest.get("source", {}).get("dataset_id"),
            "start_date_utc": manifest.get("source", {}).get("start_date_utc"),
            "end_date_utc": manifest.get("source", {}).get("end_date_utc"),
            "attribution": manifest.get("source", {}).get("attribution"),
            "license": manifest.get("source", {}).get("license"),
            "wallet_trades_sha256": manifest.get("output", {}).get("wallet_trades_sha256"),
            "market_15m_sha256": manifest.get("output", {}).get("market_15m_sha256"),
        },
        "counts": {
            "wallet_flow_15m_rows": wallet_flow_rows,
            "cohort_flow_15m_rows": cohort_flow_rows,
            "cohort_flow_forward_return_rows": response_rows,
            "native_market_count": active_market_count,
            "native_market_15m_bar_rows": market_bar_rows,
        },
        "event_study": event_rows,
        "interpretation_limits": [
            "A wallet-side matched print is not an independently verified position open, add, reduction or close.",
            "The archive does not provide initial inventory, account fees, funding, realised PnL or complete position state.",
            "Cohort net flow is the signed sum of selected wallets only, not exchange-wide order flow; trades where both counterparties are in the cohort cancel directionally but contribute to the activity denominator.",
            "Current leaderboard PnL labels are not historical point-in-time labels and are not used to claim causality.",
            "Forward returns are descriptive event-study outcomes with overlapping observations; they are not independent samples or a validated strategy backtest.",
            "Hyperliquid is cross-venue behavioural evidence, not BingX executions or direct signals.",
        ],
        "outputs": {
            "wallet_flow_15m": {
                "path": str(wallet_flow_path),
                "sha256": _sha256(wallet_flow_path),
                "size_bytes": wallet_flow_path.stat().st_size,
            },
            "cohort_flow_15m": {
                "path": str(cohort_flow_path),
                "sha256": _sha256(cohort_flow_path),
                "size_bytes": cohort_flow_path.stat().st_size,
            },
            "cohort_flow_forward_returns": {
                "path": str(response_path),
                "sha256": _sha256(response_path),
                "size_bytes": response_path.stat().st_size,
            },
            "event_study_csv": {
                "path": str(event_study_path),
                "sha256": _sha256(event_study_path),
                "size_bytes": event_study_path.stat().st_size,
            },
        },
    }
    summary_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet-trades", type=Path, required=True)
    parser.add_argument("--market-bars", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/hyperliquid_wallet_flow"))
    args = parser.parse_args()
    report = analyze(args.wallet_trades, args.market_bars, args.manifest, args.out_dir)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
