#!/usr/bin/env python3
"""Reconstruct taker-side trade-sequence proxies from Ironflow paired-trade Parquet.

This is a trade-level proxy, not an exact reproduction of market-order metaorders.
The public Parquet has the taker address but no order ID (oid), direction, fees or
realized PnL. Multiple trade rows can belong to one order. Use Ironflow hl.wallet_fills
and group by oid first for order-level reconstruction; retain direction and maker/taker
role to distinguish opening, closing and inventory-management activity.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="data/*.parquet", help="Parquet glob or file")
    ap.add_argument("--out", default="results/taker_trade_sequences.parquet")
    ap.add_argument("--gap-minutes", type=int, default=30)
    ap.add_argument("--min-trades", "--min-children", dest="min_trades", type=int, default=10)
    ap.add_argument("--max-duration-hours", type=float, default=24.0)
    ap.add_argument("--market-class", default="perp", choices=["perp", "spot", "all"])
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    if args.gap_minutes < 1 or args.min_trades < 2 or args.max_duration_hours <= 0:
        ap.error("gap/min-trades/duration must be positive; min-trades >= 2")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET threads={int(args.threads)}")
    # Dataset rows are paired trades: one buyer, one seller, and the side that
    # crossed the book. This is a trade-level proxy because oid is not published.
    class_filter = (
        "WHERE true"
        if args.market_class == "all"
        else f"WHERE market_class = '{args.market_class}'"
    )
    sql = f"""
    COPY (
      WITH raw AS (
        SELECT
          CAST(epoch_ms(timestamp) AS BIGINT) AS ts_ms,
          market,
          CASE WHEN lower(taker_side)='buy' THEN buyer ELSE seller END AS address,
          lower(taker_side) AS side,
          CAST(price AS DOUBLE) AS price,
          CAST(size AS DOUBLE) AS size,
          CAST(notional AS DOUBLE) AS notional,
          block, tid
        FROM read_parquet('{args.glob}', union_by_name=true)
        {class_filter}
        AND lower(taker_side) IN ('buy','sell')
      ),
      ordered AS (
        SELECT *,
          lag(ts_ms) OVER (
            PARTITION BY address, market, side ORDER BY ts_ms, block, tid
          ) AS prev_ts
        FROM raw
        WHERE address IS NOT NULL AND address <> ''
      ),
      marked AS (
        SELECT *,
          CASE WHEN prev_ts IS NULL OR ts_ms-prev_ts > {args.gap_minutes}*60000
            THEN 1 ELSE 0 END AS new_parent
        FROM ordered
      ),
      numbered AS (
        SELECT *,
          sum(new_parent) OVER (
            PARTITION BY address, market, side ORDER BY ts_ms, block, tid
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
          ) AS parent_no
        FROM marked
      ),
      parents AS (
        SELECT address, market, side, parent_no,
          min(ts_ms) AS start_ms, max(ts_ms) AS end_ms,
          count(*) AS trade_count,
          sum(notional) AS q_notional,
          sum(size) AS base_size,
          sum(price*size)/nullif(sum(size),0) AS vwap,
          arg_min(price, ts_ms) AS first_price,
          arg_max(price, ts_ms) AS last_price,
          min(block) AS first_block, max(block) AS last_block
        FROM numbered
        GROUP BY address, market, side, parent_no
        HAVING count(*) >= {args.min_trades}
          AND max(ts_ms)-min(ts_ms) <= {args.max_duration_hours}*3600000
      ),
      shape AS (
        SELECT n.address, n.market, n.side, n.parent_no,
          sum(n.notional) FILTER (WHERE p.end_ms > p.start_ms AND
            (n.ts_ms-p.start_ms)::DOUBLE/(p.end_ms-p.start_ms) <= 0.20)
              AS first20_notional,
          sum(n.notional) FILTER (WHERE p.end_ms > p.start_ms AND
            (n.ts_ms-p.start_ms)::DOUBLE/(p.end_ms-p.start_ms) >= 0.80)
              AS last20_notional,
          sum(n.notional) FILTER (WHERE p.end_ms > p.start_ms AND
            (n.ts_ms-p.start_ms)::DOUBLE/(p.end_ms-p.start_ms) > 0.20 AND
            (n.ts_ms-p.start_ms)::DOUBLE/(p.end_ms-p.start_ms) < 0.80)
              AS middle60_notional
        FROM numbered n
        JOIN parents p USING (address, market, side, parent_no)
        GROUP BY n.address, n.market, n.side, n.parent_no
      ),
      market_24h AS (
        SELECT market, ts_ms,
          sum(notional) OVER (
            PARTITION BY market ORDER BY ts_ms
            RANGE BETWEEN CURRENT ROW AND 86400000 FOLLOWING
          ) AS volume_next_24h
        FROM raw
      ),
      starts AS (
        SELECT market, ts_ms, volume_next_24h,
          row_number() OVER (
            PARTITION BY market, ts_ms ORDER BY volume_next_24h DESC
          ) AS rn
        FROM market_24h
      ),
      market_minute AS (
        SELECT market, floor(ts_ms/60000.0)*60000 AS minute_ms,
          sum(notional) AS minute_notional
        FROM raw
        GROUP BY market, minute_ms
      ),
      exec_volume AS (
        SELECT p.address, p.market, p.side, p.parent_no,
          sum(m.minute_notional) AS market_exec_volume_1m_approx
        FROM parents p
        JOIN market_minute m ON m.market=p.market
          AND m.minute_ms BETWEEN floor(p.start_ms/60000.0)*60000
                              AND floor(p.end_ms/60000.0)*60000
        GROUP BY p.address, p.market, p.side, p.parent_no
      )
      SELECT p.*,
        (p.end_ms-p.start_ms)/60000.0 AS duration_minutes,
        s.volume_next_24h,
        p.q_notional/nullif(s.volume_next_24h,0) AS traded_fraction_24h,
        ev.market_exec_volume_1m_approx,
        p.q_notional/nullif(ev.market_exec_volume_1m_approx,0) AS participation_rate_1m_approx,
        ev.market_exec_volume_1m_approx/nullif(s.volume_next_24h,0) AS volume_time_fraction_1m_approx,
        sh.first20_notional/nullif(p.q_notional,0) AS first20_share,
        sh.last20_notional/nullif(p.q_notional,0) AS last20_share,
        sh.middle60_notional/nullif(p.q_notional,0) AS middle60_share,
        p.last_price/p.first_price-1 AS raw_price_move,
        CASE WHEN p.side='buy' THEN p.last_price/p.first_price-1
             ELSE p.first_price/p.last_price-1 END AS signed_price_move,
        '{args.gap_minutes}m' AS gap_rule
      FROM parents p
      LEFT JOIN starts s
        ON s.market=p.market AND s.ts_ms=p.start_ms AND s.rn=1
      LEFT JOIN shape sh USING (address, market, side, parent_no)
      LEFT JOIN exec_volume ev USING (address, market, side, parent_no)
    ) TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """
    con.execute(sql)
    count = con.execute(
        f"SELECT count(*) FROM read_parquet('{out.as_posix()}')"
    ).fetchone()[0]
    print(f"wrote {count} taker-side trade-sequence proxies to {out}")
    print(
        "LIMITATION: trade rows are not order IDs; multiple trades may be one "
        "market order. This proxy cannot identify direction, maker fills, TWAP "
        "parents, fees or realized PnL."
    )


if __name__ == "__main__":
    main()
