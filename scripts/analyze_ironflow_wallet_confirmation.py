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
            {", ".join(selects)}
        FROM cohort c
        INNER JOIN bars b
          ON b.market = c.market AND b.bucket_ts = c.bucket_ts
        {" ".join(joins)}
        """
    )

    rows: list[dict[str, Any]] = []
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
                    ranked AS (
                        SELECT *,
                            ntile(10) OVER (
                                PARTITION BY market ORDER BY signal_value
                            ) AS signal_decile
                        FROM selected
                    )
                    SELECT
                        market,
                        count(*) AS observations,
                        signal_decile,
                        avg(signal_value) AS mean_signal_value,
                        avg(directional_return_pct)
                            AS mean_directional_return_pct,
                        median(directional_return_pct)
                            AS median_directional_return_pct,
                        100.0 * avg(
                            CASE WHEN directional_return_pct > 0
                                THEN 1.0 ELSE 0.0 END
                        ) AS directional_hit_rate_pct,
                        avg(active_wallets) AS mean_active_wallets
                    FROM ranked
                    GROUP BY market, signal_decile
                    ORDER BY market, signal_decile
                """
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
        "observations", "mean_signal_value",
        "mean_directional_return_pct", "median_directional_return_pct",
        "directional_hit_rate_pct", "mean_active_wallets",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

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
