#!/usr/bin/env python3
"""Build causal same-wallet taker execution-sequence proxies from cohort trades.

Unlike exact metaorders, the public paired-trade archive has no order IDs.
Sequences are therefore a bounded trade-level proxy. Only cohort taker
addresses are included; no future-volume fields or wallet identifiers are
written to output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

CORE_MARKETS = ("BTC-PERP", "ETH-PERP", "SOL-PERP", "HYPE-PERP", "INJ-PERP")


def build_sequences(
    trades_path: Path,
    manifest_path: Path,
    out_dir: Path,
    *,
    gap_minutes: int = 30,
    min_trades: int = 10,
    max_duration_hours: float = 24.0,
) -> dict[str, Any]:
    if gap_minutes < 1 or min_trades < 2 or max_duration_hours <= 0:
        raise ValueError("gap, minimum trades and maximum duration must be positive")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    addresses = sorted({
        str(row.get("address", "")).lower()
        for row in manifest.get("wallet_summaries", [])
        if str(row.get("address", "")).lower().startswith("0x")
    })
    if not addresses:
        raise ValueError("wallet archive manifest contains no cohort addresses")
    address_sql = ", ".join("'" + address.replace("'", "''") + "'" for address in addresses)
    market_sql = ", ".join("'" + market + "'" for market in CORE_MARKETS)

    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / "cohort_taker_sequences.parquet"
    csv_path = out_dir / "cohort_taker_sequences.csv"
    con = duckdb.connect()
    con.execute("SET threads=2")
    sql = f"""
        CREATE TEMP TABLE sequences AS
        WITH raw AS (
            SELECT
                CAST(epoch_ms(timestamp) AS BIGINT) AS ts_ms,
                market,
                CASE WHEN lower(taker_side)='buy' THEN buyer ELSE seller END AS address,
                lower(taker_side) AS side,
                CAST(price AS DOUBLE) AS price,
                CAST(size AS DOUBLE) AS size,
                CAST(notional AS DOUBLE) AS notional,
                block,
                tid
            FROM read_parquet('{trades_path.as_posix()}', union_by_name=true)
            WHERE market_class='perp'
              AND market IN ({market_sql})
              AND lower(taker_side) IN ('buy','sell')
              AND CASE WHEN lower(taker_side)='buy' THEN lower(buyer)
                       ELSE lower(seller) END IN ({address_sql})
        ),
        ordered AS (
            SELECT *,
                lag(ts_ms) OVER (
                    PARTITION BY address, market, side
                    ORDER BY ts_ms, block, tid
                ) AS prev_ts
            FROM raw
        ),
        marked AS (
            SELECT *,
                CASE WHEN prev_ts IS NULL
                       OR ts_ms-prev_ts > {gap_minutes}*60000
                     THEN 1 ELSE 0 END AS new_sequence
            FROM ordered
        ),
        numbered AS (
            SELECT *,
                sum(new_sequence) OVER (
                    PARTITION BY address, market, side
                    ORDER BY ts_ms, block, tid
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS sequence_no
            FROM marked
        ),
        parents AS (
            SELECT address, market, side, sequence_no,
                min(ts_ms) AS start_ms,
                max(ts_ms) AS end_ms,
                count(*) AS trade_count,
                sum(notional) AS q_notional,
                sum(size) AS base_size,
                sum(price*size)/nullif(sum(size),0) AS vwap,
                arg_min(price, ts_ms) AS first_price,
                arg_max(price, ts_ms) AS last_price
            FROM numbered
            GROUP BY address, market, side, sequence_no
            HAVING count(*) >= {min_trades}
               AND max(ts_ms)-min(ts_ms) <= {max_duration_hours}*3600000
        ),
        shape AS (
            SELECT n.address, n.market, n.side, n.sequence_no,
                sum(n.notional) FILTER (
                    WHERE p.end_ms > p.start_ms
                      AND (n.ts_ms-p.start_ms)::DOUBLE
                          /(p.end_ms-p.start_ms) <= 0.20
                ) AS first20_notional,
                sum(n.notional) FILTER (
                    WHERE p.end_ms > p.start_ms
                      AND (n.ts_ms-p.start_ms)::DOUBLE
                          /(p.end_ms-p.start_ms) >= 0.80
                ) AS last20_notional,
                sum(n.notional) FILTER (
                    WHERE p.end_ms > p.start_ms
                      AND (n.ts_ms-p.start_ms)::DOUBLE
                          /(p.end_ms-p.start_ms) > 0.20
                      AND (n.ts_ms-p.start_ms)::DOUBLE
                          /(p.end_ms-p.start_ms) < 0.80
                ) AS middle60_notional
            FROM numbered n
            INNER JOIN parents p
                USING (address, market, side, sequence_no)
            GROUP BY n.address, n.market, n.side, n.sequence_no
        )
        SELECT
            p.market,
            p.side,
            p.start_ms,
            p.end_ms,
            p.trade_count,
            p.q_notional,
            p.base_size,
            (p.end_ms-p.start_ms)/60000.0 AS duration_minutes,
            p.vwap,
            p.first_price,
            p.last_price,
            sh.first20_notional/nullif(p.q_notional,0) AS first20_share,
            sh.last20_notional/nullif(p.q_notional,0) AS last20_share,
            sh.middle60_notional/nullif(p.q_notional,0) AS middle60_share,
            p.last_price/p.first_price-1 AS raw_price_move,
            CASE WHEN p.side='buy' THEN p.last_price/p.first_price-1
                 ELSE p.first_price/p.last_price-1 END AS signed_price_move
        FROM parents p
        INNER JOIN shape sh USING (address, market, side, sequence_no)
    """
    con.execute(sql)
    con.execute(
        f"COPY sequences TO '{parquet_path.as_posix()}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    con.execute(
        f"COPY (SELECT * FROM sequences ORDER BY market, end_ms, side) "
        f"TO '{csv_path.as_posix()}' (HEADER, DELIMITER ',')"
    )

    summary_rows = con.execute(
        """
        SELECT market, side, count(*) AS sequences,
            sum(trade_count) AS child_fills,
            sum(q_notional) AS total_notional_usd,
            median(q_notional) AS median_sequence_notional_usd,
            quantile_cont(q_notional, 0.75) AS p75_sequence_notional_usd,
            quantile_cont(q_notional, 0.90) AS p90_sequence_notional_usd,
            median(duration_minutes) AS median_duration_minutes,
            median(trade_count) AS median_child_fills,
            median(signed_price_move) AS median_signed_in_sequence_move
        FROM sequences
        GROUP BY market, side
        ORDER BY market, side
        """
    ).fetchall()
    unique_wallets = con.execute(
        f"""
        WITH cohort AS (
            SELECT lower(address) AS address
            FROM read_json_auto('{manifest_path.as_posix()}')
            """
    ) if False else None
    total = con.execute("SELECT count(*) FROM sequences").fetchone()[0]
    con.close()

    rows = []
    for row in summary_rows:
        rows.append({
            "market": row[0],
            "side": row[1],
            "sequences": int(row[2]),
            "child_fills": int(row[3]),
            "total_notional_usd": float(row[4]),
            "median_sequence_notional_usd": float(row[5]),
            "p75_sequence_notional_usd": float(row[6]),
            "p90_sequence_notional_usd": float(row[7]),
            "median_duration_minutes": float(row[8]),
            "median_child_fills": float(row[9]),
            "median_signed_in_sequence_move": float(row[10]),
        })
    report = {
        "status": "EXPLORATORY_TAKER_SEQUENCE_PROXY",
        "source": "37-day paired-trade Parquet archive",
        "cohort_wallets": len(addresses),
        "core_markets": list(CORE_MARKETS),
        "rules": {
            "taker_wallet_only": True,
            "same_wallet_market_side_gap_minutes": gap_minutes,
            "minimum_child_fills": min_trades,
            "maximum_sequence_duration_hours": max_duration_hours,
            "future_volume_features": False,
            "wallet_addresses_exported": False,
            "order_id_available": False,
        },
        "counts": {
            "sequences": int(total),
            "markets_with_sequences": len({row[0] for row in summary_rows}),
        },
        "by_market_side": rows,
        "limitations": [
            "A sequence is a same-wallet, same-market, same-taker-side trade cluster, not a confirmed parent order.",
            "The public paired-trade archive has no order ID, position direction, maker fills, fees or realized PnL.",
            "The sequence side is execution aggressor direction, not Open Long/Open Short intent.",
            "Sequence rules are fixed discovery parameters and require untouched OOS validation.",
        ],
        "outputs": {
            "parquet": str(parquet_path),
            "csv": str(csv_path),
        },
    }
    (out_dir / "cohort_taker_sequence_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/taker_sequences"))
    parser.add_argument("--gap-minutes", type=int, default=30)
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--max-duration-hours", type=float, default=24.0)
    args = parser.parse_args()
    report = build_sequences(
        args.trades,
        args.manifest,
        args.out_dir,
        gap_minutes=args.gap_minutes,
        min_trades=args.min_trades,
        max_duration_hours=args.max_duration_hours,
    )
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
