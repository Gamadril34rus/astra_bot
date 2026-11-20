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
            p1.close AS close_prev_1h,
            p4.close AS close_prev_4h,
            CASE WHEN p1.close > 0
                THEN 100.0 * (b.close / p1.close - 1.0)
                ELSE NULL END AS prior_1h_return_pct,
            CASE WHEN p4.close > 0
                THEN 100.0 * (b.close / p4.close - 1.0)
                ELSE NULL END AS prior_4h_return_pct,
            CASE WHEN c.cohort_participant_notional > 0
                THEN c.cohort_signed_notional / c.cohort_participant_notional
                ELSE NULL END AS flow_ratio,
            CASE WHEN c.cohort_taker_notional > 0
                THEN c.cohort_aggressive_signed_notional
                    / c.cohort_taker_notional
                ELSE NULL END AS aggressive_flow_ratio,
            CASE WHEN c.active_wallets > 0
                THEN (c.net_buy_wallets - c.net_sell_wallets)::DOUBLE
                    / c.active_wallets
                ELSE NULL END AS wallet_consensus_ratio,
            {", ".join(selects)}
        FROM cohort c
        INNER JOIN bars b
          ON b.market = c.market AND b.bucket_ts = c.bucket_ts
        LEFT JOIN bars p1
          ON p1.market = c.market
         AND p1.bucket_ts = c.bucket_ts - INTERVAL '60 minutes'
        LEFT JOIN bars p4
          ON p4.market = c.market
         AND p4.bucket_ts = c.bucket_ts - INTERVAL '240 minutes'
        {" ".join(joins)}
        """
    )

    rows: list[dict[str, Any]] = []
    side_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    for horizon, minutes in HORIZONS.items():
        close_col = f"close_{horizon}"
        for metric in METRICS:
            for sample_type in ("all", "non_overlapping"):
                overlap_clause = ""
                if sample_type == "non_overlapping":
                    bars_per_horizon = minutes // 15
                    overlap_clause = (
                        "QUALIFY row_number() OVER ("
                        "PARTITION BY market ORDER BY bucket_ts) "
                        f"% {bars_per_horizon} = 1"
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
                            + INTERVAL '{minutes} minutes'
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
                            prior_1h_return_pct,
                            prior_4h_return_pct,
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
                    selected_with_gap AS (
                        SELECT *,
                            date_diff(
                                'minute',
                                lag(bucket_ts) OVER (
                                    PARTITION BY market ORDER BY bucket_ts
                                ),
                                bucket_ts
                            ) AS gap_minutes
                        FROM selected
                    ),
                    baseline AS (
                        SELECT b.market,
                            avg(100.0 * (f.close / b.close - 1.0))
                                AS baseline_return_pct
                        FROM bars b
                        INNER JOIN bars f
                          ON f.market = b.market
                         AND f.bucket_ts = b.bucket_ts
                            + INTERVAL '{minutes} minutes'
                        WHERE b.close > 0 AND f.close > 0
                        GROUP BY b.market
                    )
                    SELECT
                        s.market,
                        s.signal_side,
                        count(*) AS observations,
                        avg(abs(s.signal_value)) AS mean_abs_signal_value,
                        avg(s.raw_return_pct) AS mean_raw_return_pct,
                        avg(b.baseline_return_pct) AS baseline_return_pct,
                        avg(s.directional_return_pct)
                            AS mean_directional_return_pct,
                        avg(s.directional_return_pct)
                            - s.signal_side * avg(b.baseline_return_pct)
                            AS excess_directional_return_pct,
                        median(s.directional_return_pct)
                            AS median_directional_return_pct,
                        100.0 * avg(
                            CASE WHEN s.directional_return_pct > 0
                                THEN 1.0 ELSE 0.0 END
                        ) AS directional_hit_rate_pct,
                        min(s.gap_minutes) AS min_gap_minutes
                    FROM selected_with_gap s
                    INNER JOIN baseline b ON b.market = s.market
                    GROUP BY s.market, s.signal_side
                    ORDER BY s.market, s.signal_side
                """
                if sample_type == "non_overlapping":
                    event_query = f"""
                        WITH eligible AS (
                            SELECT
                                market,
                                bucket_ts,
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
                        ),
                        baseline AS (
                            SELECT b.market,
                                avg(100.0 * (f.close / b.close - 1.0))
                                    AS baseline_return_pct
                            FROM bars b
                            INNER JOIN bars f
                              ON f.market = b.market
                             AND f.bucket_ts = b.bucket_ts
                                + INTERVAL '{minutes} minutes'
                            WHERE b.close > 0 AND f.close > 0
                            GROUP BY b.market
                        )
                        SELECT
                            s.market,
                            s.bucket_ts,
                            s.signal_side,
                            s.signal_value,
                            s.prior_1h_return_pct,
                            s.prior_4h_return_pct,
                            s.raw_return_pct,
                            s.directional_return_pct,
                            b.baseline_return_pct,
                            s.directional_return_pct
                                - s.signal_side * b.baseline_return_pct
                                AS excess_directional_return_pct
                        FROM selected s
                        INNER JOIN baseline b ON b.market = s.market
                        ORDER BY s.market, s.bucket_ts
                    """
                    event_result = con.execute(event_query)
                    event_columns = [item[0] for item in event_result.description]
                    for values in event_result.fetchall():
                        event_row = dict(zip(event_columns, values, strict=True))
                        event_rows.append({
                            "horizon": horizon,
                            "metric": metric,
                            "market": event_row["market"],
                            "bucket_ts": event_row["bucket_ts"].isoformat(),
                            "signal_side": (
                                "buy_pressure" if event_row["signal_side"] > 0
                                else "sell_pressure"
                            ),
                            "signal_value": float(event_row["signal_value"]),
                            "prior_1h_return_pct": (
                                float(event_row["prior_1h_return_pct"])
                                if event_row["prior_1h_return_pct"] is not None else None
                            ),
                            "prior_4h_return_pct": (
                                float(event_row["prior_4h_return_pct"])
                                if event_row["prior_4h_return_pct"] is not None else None
                            ),
                            "raw_return_pct": float(event_row["raw_return_pct"]),
                            "directional_return_pct": float(
                                event_row["directional_return_pct"]
                            ),
                            "baseline_return_pct": float(
                                event_row["baseline_return_pct"]
                            ),
                            "excess_directional_return_pct": float(
                                event_row["excess_directional_return_pct"]
                            ),
                        })

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
                        "baseline_return_pct": float(side_row["baseline_return_pct"]),
                        "excess_directional_return_pct": float(
                            side_row["excess_directional_return_pct"]
                        ),
                        "mean_directional_return_pct": float(
                            side_row["mean_directional_return_pct"]
                        ),
                        "median_directional_return_pct": float(
                            side_row["median_directional_return_pct"]
                        ),
                        "directional_hit_rate_pct": float(
                            side_row["directional_hit_rate_pct"]
                        ),
                        "min_gap_minutes": (
                            int(side_row["min_gap_minutes"])
                            if side_row["min_gap_minutes"] is not None else None
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
        "baseline_return_pct", "excess_directional_return_pct",
        "mean_directional_return_pct", "median_directional_return_pct",
        "directional_hit_rate_pct", "min_gap_minutes",
    ]
    with side_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=side_fields)
        writer.writeheader()
        writer.writerows(side_rows)

    event_csv_path = out_dir / "wallet_flow_confirmation_events.csv"
    event_fields = [
        "horizon", "metric", "market", "bucket_ts", "signal_side",
        "signal_value", "prior_1h_return_pct", "prior_4h_return_pct",
        "raw_return_pct", "directional_return_pct", "baseline_return_pct",
        "excess_directional_return_pct",
    ]
    with event_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=event_fields)
        writer.writeheader()
        writer.writerows(event_rows)

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
                "Every Nth eligible 15m observation per market in chronological "
                "order, where N=horizon_minutes/15. Since each market has at most "
                "one row per 15m bucket, selected timestamps are separated by "
                "at least the forward horizon; gaps are also reported."
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
            "event_rows": len(event_rows),
            "events_csv": str(event_csv_path),
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
