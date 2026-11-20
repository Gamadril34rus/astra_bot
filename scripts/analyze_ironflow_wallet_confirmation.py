#!/usr/bin/env python3
"""Non-overlapping, market-level confirmation study for public-wallet flow."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HORIZONS = {"30m": 30, "1h": 60, "4h": 240, "9h": 540}
METRICS = (
    "flow_ratio",
    "aggressive_flow_ratio",
    "wallet_consensus_ratio",
)


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def analyze(
    cohort_path: Path,
    market_bars_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    import duckdb

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "wallet_flow_confirmation_study.csv"
    json_path = out_dir / "wallet_flow_confirmation_summary.json"
    con = duckdb.connect()
    con.execute("SET threads=2")
    con.execute(
        f"CREATE VIEW cohort AS SELECT * FROM read_parquet('{_sql_path(cohort_path)}')"
    )
    con.execute(
        f"CREATE VIEW bars AS SELECT * FROM read_parquet('{_sql_path(market_bars_path)}')"
    )

    joins = []
    selects = []
    for label, minutes in HORIZONS.items():
        joins.append(
            f"LEFT JOIN bars f_{label} ON f_{label}.market = c.market "
            f"AND f_{label}.bucket_ts = c.bucket_ts + "
            f"INTERVAL '{minutes} minutes'"
        )
        selects.append(f"f_{label}.close AS close_{label}")
    con.execute(
        f"""
        CREATE VIEW response AS
        SELECT
            c.*,
            b.close AS close_t,
            CASE WHEN c.cohort_participant_notional > 0
                THEN c.cohort_signed_notional / c.cohort_participant_notional
                ELSE NULL END AS flow_ratio,
            CASE WHEN c.cohort_participant_notional > 0
                THEN c.cohort_aggressive_signed_notional
                    / c.cohort_participant_notional
                ELSE NULL END AS aggressive_flow_ratio,
            CASE WHEN c.active_wallets > 0
                THEN (c.net_buy_wallets - c.net_sell_wallets)::DOUBLE
                    / c.active_wallets
                ELSE NULL END AS wallet_consensus_ratio,
            {", ".join(selects)}
        FROM cohort c
        INNER JOIN bars b
          ON b.market = c.market AND b.bucket_ts = c.bucket_ts
        {" ".join(joins)}
        """
    )

    rows: list[dict[str, Any]] = []
    side_rows: list[dict[str, Any]] = []
    for horizon, minutes in HORIZONS.items():
        close_col = f"close_{horizon}"
        seconds = minutes * 60
        for metric in METRICS:
            for sample_type in ("all", "non_overlapping"):
                overlap_clause = ""
                if sample_type == "non_overlapping":
                    overlap_clause = (
                        "QUALIFY row_number() OVER ("
                        "PARTITION BY market, "
                        f"floor(epoch(bucket_ts) / {seconds}) "
                        "ORDER BY bucket_ts) = 1"
                    )
                query = f"""
                    WITH eligible AS (
                        SELECT
                            market,
                            bucket_ts,
                            active_wallets,
                            {metric} AS signal_value,
                            100.0 * ({close_col} / close_t - 1.0)
                                AS raw_return_pct,
                            sign({metric}) * 100.0 *
                                ({close_col} / close_t - 1.0)
                                AS directional_return_pct
                        FROM response
                        WHERE active_wallets >= 3
                          AND {metric} IS NOT NULL
                          AND abs({metric}) > 0
                          AND close_t > 0
                          AND {close_col} IS NOT NULL
                          AND {close_col} > 0
                    ),
                    selected AS (
                        SELECT * FROM eligible
                        {overlap_clause}
                    ),
                    baseline AS (
                        SELECT b.market,
                            avg(100.0 * (f.close / b.close - 1.0))
                                AS baseline_return_pct
                        FROM bars b
                        INNER JOIN bars f
                          ON f.market = b.market
                         AND f.bucket_ts = b.bucket_ts
                            + INTERVAL '${minutes} minutes'
                        WHERE b.close > 0 AND f.close > 0
                        GROUP BY b.market
                    ),
                    ranked AS (
                        SELECT *,
                            ntile(10) OVER (
                                PARTITION BY market ORDER BY signal_value
                            ) AS signal_decile
                        FROM selected
                    )
                    SELECT
                        r.market,
                        count(*) AS observations,
                        r.signal_decile,
                        avg(r.signal_value) AS mean_signal_value,
                        avg(r.raw_return_pct) AS mean_raw_return_pct,
                        avg(b.baseline_return_pct) AS baseline_return_pct,
                        sum(CASE WHEN r.signal_value < 0 THEN 1 ELSE 0 END)
                            AS negative_signal_observations,
                        sum(CASE WHEN r.signal_value > 0 THEN 1 ELSE 0 END)
                            AS positive_signal_observations,
                        avg(r.directional_return_pct)
                            AS mean_directional_return_pct,
                        median(r.directional_return_pct)
                            AS median_directional_return_pct,
                        100.0 * avg(
                            CASE WHEN r.directional_return_pct > 0
                                THEN 1.0 ELSE 0.0 END
                        ) AS directional_hit_rate_pct,
                        avg(r.active_wallets) AS mean_active_wallets
                    FROM ranked r
                    INNER JOIN baseline b ON b.market = r.market
                    GROUP BY r.market, r.signal_decile
                    ORDER BY r.market, r.signal_decile
                """
                side_query = f"""
                    WITH eligible AS (
                        SELECT
                            market,
                            bucket_ts,
                            active_wallets,
                            {metric} AS signal_value,
                            sign({metric}) AS signal_side,
                            100.0 * ({close_col} / close_t - 1.0)
                                AS raw_return_pct,
                            sign({metric}) * 100.0 *
                                ({close_col} / close_t - 1.0)
                                AS directional_return_pct
                        FROM response
                        WHERE active_wallets >= 3
                          AND {metric} IS NOT NULL
                          AND abs({metric}) > 0
                          AND close_t > 0
                          AND {close_col} IS NOT NULL
                          AND {close_col} > 0
                    ),
                    selected AS (
                        SELECT * FROM eligible
                        {overlap_clause}
                    )
                    SELECT
                        market,
                        signal_side,
                        count(*) AS observations,
                        avg(abs(signal_value)) AS mean_abs_signal_value,
                        avg(raw_return_pct) AS mean_raw_return_pct,
                        avg(directional_return_pct)
                            AS mean_directional_return_pct,
                        median(directional_return_pct)
                            AS median_directional_return_pct,
                        100.0 * avg(
                            CASE WHEN directional_return_pct > 0
                                THEN 1.0 ELSE 0.0 END
                        ) AS directional_hit_rate_pct
                    FROM selected
                    GROUP BY market, signal_side
                    ORDER BY market, signal_side
                """
                side_result = con.execute(side_query)
                side_columns = [item[0] for item in side_result.description]
                for values in side_result.fetchall():
                    side_row = dict(zip(side_columns, values, strict=True))
                    side_rows.append({
                        "horizon": horizon,
                        "metric": metric,
                        "sample_type": sample_type,
                        "market": side_row["market"],
                        "signal_side": (
                            "buy_pressure" if side_row["signal_side"] > 0
                            else "sell_pressure"
                        ),
                        "observations": int(side_row["observations"]),
                        "mean_abs_signal_value": float(
                            side_row["mean_abs_signal_value"]
                        ),
                        "mean_raw_return_pct": float(side_row["mean_raw_return_pct"]),
                        "mean_directional_return_pct": float(
                            side_row["mean_directional_return_pct"]
                        ),
                        "median_directional_return_pct": float(
                            side_row["median_directional_return_pct"]
                        ),
                        "directional_hit_rate_pct": float(
                            side_row["directional_hit_rate_pct"]
                        ),
                    })

                result = con.execute(query)
                columns = [item[0] for item in result.description]
                for values in result.fetchall():
                    row = dict(zip(columns, values, strict=True))
                    rows.append({
                        "horizon": horizon,
                        "metric": metric,
                        "sample_type": sample_type,
                        "market": row["market"],
                        "signal_decile": int(row["signal_decile"]),
                        "observations": int(row["observations"]),
                        "mean_signal_value": float(row["mean_signal_value"]),
                        "mean_raw_return_pct": float(row["mean_raw_return_pct"]),
                        "baseline_return_pct": float(row["baseline_return_pct"]),
                        "negative_signal_observations": int(
                            row["negative_signal_observations"]
                        ),
                        "positive_signal_observations": int(
                            row["positive_signal_observations"]
                        ),
                        "mean_directional_return_pct": float(
                            row["mean_directional_return_pct"]
                        ),
                        "median_directional_return_pct": float(
                            row["median_directional_return_pct"]
                        ),
                        "directional_hit_rate_pct": float(
                            row["directional_hit_rate_pct"]
                        ),
                        "mean_active_wallets": float(row["mean_active_wallets"]),
                    })

    fields = [
        "horizon", "metric", "sample_type", "market", "signal_decile",
        "observations", "mean_signal_value", "mean_raw_return_pct",
        "baseline_return_pct", "negative_signal_observations",
        "positive_signal_observations", "mean_directional_return_pct",
        "median_directional_return_pct",
        "directional_hit_rate_pct", "mean_active_wallets",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    side_csv_path = out_dir / "wallet_flow_confirmation_sides.csv"
    side_fields = [
        "horizon", "metric", "sample_type", "market", "signal_side",
        "observations", "mean_abs_signal_value", "mean_raw_return_pct",
        "mean_directional_return_pct", "median_directional_return_pct",
        "directional_hit_rate_pct",
    ]
    with side_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=side_fields)
        writer.writeheader()
        writer.writerows(side_rows)

    markets = con.execute(
        "SELECT count(DISTINCT market) FROM cohort"
    ).fetchone()[0]
    cohort_rows = con.execute("SELECT count(*) FROM cohort").fetchone()[0]
    con.close()
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "input": {
            "cohort_path": str(cohort_path),
            "market_bars_path": str(market_bars_path),
        },
        "counts": {
            "cohort_rows": int(cohort_rows),
            "markets": int(markets),
            "study_rows": len(rows),
        },
        "method": {
            "horizons": HORIZONS,
            "metrics": list(METRICS),
            "sample_types": ["all", "non_overlapping"],
            "non_overlapping_rule": (
                "One earliest eligible 15m observation per market and "
                "fixed UTC horizon-sized bucket; selected forward windows "
                "cannot overlap within a market."
            ),
            "deciles": "Ranked separately within each market.",
        },
        "limitations": [
            "This is a descriptive event study, not a strategy backtest.",
            "Hyperliquid wallet prints do not prove position opens or closes.",
            "The source lacks complete inventory, realized PnL, fees and funding.",
            "Returns are gross and do not include BingX execution costs or funding.",
            "The 37-day sample is not an untouched out-of-sample validation set.",
            "Per-market deciles with small samples must not be interpreted as robust.",
        ],
        "outputs": {
            "csv": str(csv_path),
            "rows": len(rows),
            "side_rows": len(side_rows),
            "sides_csv": str(side_csv_path),
        },
    }
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-flow", type=Path, required=True)
    parser.add_argument("--market-bars", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/wallet_flow_confirmation"))
    args = parser.parse_args()
    report = analyze(args.cohort_flow, args.market_bars, args.out_dir)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
