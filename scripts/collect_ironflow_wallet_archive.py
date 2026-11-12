#!/usr/bin/env python3
"""Filter Ironflow's public Hyperliquid trade archive for a frozen wallet cohort."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DATASET_ID = "ironflow/hyperliquid-trades"
DATASET_URL = "https://huggingface.co/datasets/ironflow/hyperliquid-trades"
FILE_URL = "https://huggingface.co/datasets/ironflow/hyperliquid-trades/resolve/main/data/{day}.parquet"
START_DATE = date(2026, 10, 3)
END_DATE = date(2026, 11, 8)
EXPECTED_SOURCE_ROWS = 210_257_579
BATCH_SIZE = 500_000
USER_AGENT = "ASTRA-BOT-research/1.0 (public-data audit)"


def days_inclusive(start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError("start date must be <= end date")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def download_file(url: str, path: Path, retries: int = 5) -> tuple[int, str]:
    last_error: Exception | None = None
    for attempt in range(retries):
        digest = hashlib.sha256()
        size = 0
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=180) as response, path.open("wb") as handle:
                while True:
                    chunk = response.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size <= 0:
                raise ValueError(f"empty Parquet download from {url}")
            return size, digest.hexdigest()
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            path.unlink(missing_ok=True)
            if attempt + 1 < retries:
                time.sleep((5, 15, 30, 60, 90)[attempt])
    raise RuntimeError(f"failed to download {url}: {last_error}") from last_error


def _blank_dex_mask(values: Any) -> Any:
    import pyarrow.compute as pc

    return pc.fill_null(pc.or_(pc.is_null(values), pc.equal(values, "")), True)


def _new_stats() -> dict[str, Any]:
    return {
        "fills": 0,
        "notional": 0.0,
        "buy_notional": 0.0,
        "sell_notional": 0.0,
        "taker_fills": 0,
        "maker_fills": 0,
        "liquidation_trade_interactions": 0,
        "active_days": set(),
        "active_hours": set(),
        "markets": Counter(),
        "hour_utc": Counter(),
        "notional_samples": [],
    }


def _add_stats(stats: dict[str, Any], table: Any) -> None:
    columns = table.select([
        "wallet", "side", "crossed", "notional", "market", "timestamp", "liquidation"
    ]).to_pylist()
    for row in columns:
        wallet_stats = stats[row["wallet"]]
        notional = float(row["notional"] or 0.0)
        timestamp = row["timestamp"]
        day_key = timestamp.date().isoformat()
        hour_key = timestamp.strftime("%Y-%m-%dT%H")
        wallet_stats["fills"] += 1
        wallet_stats["notional"] += notional
        wallet_stats["markets"][row["market"]] += notional
        wallet_stats["active_days"].add(day_key)
        wallet_stats["active_hours"].add(hour_key)
        wallet_stats["hour_utc"][timestamp.hour] += 1
        wallet_stats["notional_samples"].append(notional)
        if row["side"] == "buy":
            wallet_stats["buy_notional"] += notional
        else:
            wallet_stats["sell_notional"] += notional
        if row["crossed"]:
            wallet_stats["taker_fills"] += 1
        else:
            wallet_stats["maker_fills"] += 1
        if row["liquidation"]:
            wallet_stats["liquidation_trade_interactions"] += 1


def _make_fill_table(table: Any, side: str, day_text: str) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    wallet_column = table["buyer"] if side == "buy" else table["seller"]
    crossed = pc.equal(table["taker_side"], side)
    table = table.append_column("wallet", wallet_column)
    table = table.append_column("side", pa.array([side] * table.num_rows, type=pa.string()))
    table = table.append_column("crossed", crossed)
    table = table.append_column(
        "source", pa.array(["ironflow_public_archive"] * table.num_rows, type=pa.string())
    )
    table = table.append_column("date", pa.array([day_text] * table.num_rows, type=pa.string()))
    return table


def _serialize_wallet_stats(wallets: list[dict], stats: dict[str, Any]) -> list[dict]:
    output = []
    cohort_map = {row["address"]: row for row in wallets}
    for address, item in stats.items():
        cohort = cohort_map[address]
        samples = item.pop("notional_samples")
        total = item["fills"]
        buy = item["buy_notional"]
        sell = item["sell_notional"]
        output.append({
            **cohort,
            "native_perp_fills": total,
            "total_notional": round(item["notional"], 6),
            "buy_notional": round(buy, 6),
            "sell_notional": round(sell, 6),
            "net_buy_notional": round(buy - sell, 6),
            "taker_share": round(item["taker_fills"] / total, 6) if total else None,
            "maker_share": round(item["maker_fills"] / total, 6) if total else None,
            "liquidation_trade_interaction_share": (
                round(item["liquidation_trade_interactions"] / total, 6) if total else None
            ),
            "active_days": len(item["active_days"]),
            "active_hours": len(item["active_hours"]),
            "fills_per_active_day": (
                round(total / len(item["active_days"]), 3) if item["active_days"] else 0
            ),
            "average_fill_notional": round(item["notional"] / total, 6) if total else None,
            "median_fill_notional": round(float(statistics.median(samples)), 6) if samples else None,
            "p90_fill_notional": round(float(sorted(samples)[min(len(samples) - 1, int(len(samples) * 0.9))]), 6) if samples else None,
            "top_markets_by_notional": [
                {"market": market, "notional": round(value, 6)}
                for market, value in item["markets"].most_common(10)
            ],
            "fills_by_utc_hour": {
                str(hour): item["hour_utc"].get(hour, 0) for hour in range(24)
            },
        })
    output.sort(key=lambda row: row["native_perp_fills"], reverse=True)
    return output


def _group_comparison(wallet_summaries: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in wallet_summaries:
        pnl = float(row.get("month_pnl") or 0)
        bucket = "positive_month_pnl" if pnl > 0 else "negative_month_pnl" if pnl < 0 else "flat_month_pnl"
        groups[bucket].append(row)
    result = {}
    for name, rows in groups.items():
        active = [row for row in rows if row["native_perp_fills"] > 0]
        result[name] = {
            "wallets": len(rows),
            "wallets_with_native_perp_fills": len(active),
            "median_fills": float(__import__("statistics").median(
                row["native_perp_fills"] for row in rows
            )),
            "median_taker_share_active": float(__import__("statistics").median(
                row["taker_share"] for row in active if row["taker_share"] is not None
            )) if active else None,
            "median_active_days": float(__import__("statistics").median(
                row["active_days"] for row in active
            )) if active else None,
            "median_net_buy_notional_active": float(__import__("statistics").median(
                row["net_buy_notional"] for row in active
            )) if active else None,
        }
    return result


def collect(
    cohort_path: Path,
    out_dir: Path,
    start: date = START_DATE,
    end: date = END_DATE,
    cohort_manifest_path: Path | None = None,
) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    cohort_snapshot = json.loads(cohort_path.read_text(encoding="utf-8"))
    cohort_manifest = (
        json.loads(cohort_manifest_path.read_text(encoding="utf-8"))
        if cohort_manifest_path and cohort_manifest_path.exists()
        else {}
    )
    strata_map = {
        row["address"].lower(): row.get("cohort_strata") or []
        for row in cohort_manifest.get("wallet_results", [])
    }
    cohort = cohort_snapshot["wallets"]
    addresses = [str(row["ethAddress"]).lower() for row in cohort] if "ethAddress" in cohort[0] else [
        str(row["address"]).lower() for row in cohort
    ]
    cohort_rows = []
    for row in cohort:
        if "ethAddress" in row:
            perf = dict(row.get("windowPerformances") or [])
            cohort_rows.append({
                "address": str(row["ethAddress"]).lower(),
                "cohort_strata": row.get("cohort_strata") or strata_map.get(str(row["ethAddress"]).lower(), []),
                "account_value": row.get("accountValue"),
                "day_pnl": (perf.get("day") or {}).get("pnl"),
                "week_pnl": (perf.get("week") or {}).get("pnl"),
                "month_pnl": (perf.get("month") or {}).get("pnl"),
                "month_roi": (perf.get("month") or {}).get("roi"),
                "month_volume": (perf.get("month") or {}).get("vlm"),
            })
        else:
            cohort_rows.append(row)
    addresses = sorted(set(addresses))
    address_array = pa.array(addresses, type=pa.string())
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = out_dir / "download_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / "wallet_trades.parquet"
    if output_path.exists():
        output_path.unlink()
    writer = None
    writer_schema = None
    stats = {address: _new_stats() for address in addresses}
    source_files = []
    raw_paths = []
    total_source_rows = 0
    filtered_trade_rows = 0
    wallet_fill_rows = 0
    both_cohort_sides = 0
    same_wallet_both_sides = 0
    market_fill_counts = Counter()

    try:
        for day in days_inclusive(start, end):
            day_text = day.isoformat()
            path = temp_dir / f"{day_text}.parquet"
            url = FILE_URL.format(day=day_text)
            size_bytes, sha256 = download_file(url, path)
            parquet = pq.ParquetFile(path)
            source_rows = parquet.metadata.num_rows
            total_source_rows += source_rows
            batch_offset = 0
            file_filtered = 0
            file_wallet_fills = 0
            for batch in parquet.iter_batches(
                batch_size=BATCH_SIZE,
                columns=[
                    "timestamp", "market", "coin", "dex", "market_class", "price",
                    "size", "notional", "taker_side", "buyer", "seller", "liquidation",
                    "block", "tid",
                ],
            ):
                table = pa.Table.from_batches([batch])
                row_order = pa.array(
                    range(batch_offset, batch_offset + batch.num_rows), type=pa.int64()
                )
                table = table.append_column("row_order", row_order)
                batch_offset += batch.num_rows
                perp_mask = pc.equal(table["market_class"], "perp")
                dex_mask = _blank_dex_mask(table["dex"])
                buyer_mask = pc.is_in(table["buyer"], value_set=address_array)
                seller_mask = pc.is_in(table["seller"], value_set=address_array)
                selected_mask = pc.and_(
                    pc.and_(perp_mask, dex_mask),
                    pc.or_(buyer_mask, seller_mask),
                )
                selected = table.filter(selected_mask)
                if selected.num_rows == 0:
                    continue
                file_filtered += selected.num_rows
                filtered_trade_rows += selected.num_rows
                buyer_selected = pc.is_in(selected["buyer"], value_set=address_array)
                seller_selected = pc.is_in(selected["seller"], value_set=address_array)
                both_cohort_sides += int(pc.sum(pc.cast(
                    pc.and_(buyer_selected, seller_selected), pa.int64()
                )).as_py() or 0)
                same_wallet_both_sides += int(pc.sum(pc.cast(
                    pc.and_(pc.equal(selected["buyer"], selected["seller"]), buyer_selected),
                    pa.int64(),
                )).as_py() or 0)
                buyer_table = selected.filter(buyer_selected)
                seller_table = selected.filter(seller_selected)
                fill_tables = []
                if buyer_table.num_rows:
                    fill_tables.append(_make_fill_table(buyer_table, "buy", day_text))
                if seller_table.num_rows:
                    fill_tables.append(_make_fill_table(seller_table, "sell", day_text))
                if not fill_tables:
                    continue
                output_table = pa.concat_tables(fill_tables)
                output_table = output_table.sort_by([("row_order", "ascending"), ("side", "ascending")])
                output_table = output_table.select([
                    "wallet", "timestamp", "market", "price", "size", "notional",
                    "taker_side", "side", "crossed", "liquidation", "block", "tid",
                ])
                if writer is None:
                    writer_schema = output_table.schema
                    writer = pq.ParquetWriter(
                        output_path,
                        writer_schema,
                        compression="zstd",
                        compression_level=9,
                        use_dictionary=["wallet", "market", "taker_side", "side"],
                    )
                elif output_table.schema != writer_schema:
                    output_table = output_table.cast(writer_schema)
                writer.write_table(output_table, row_group_size=250_000)
                wallet_fill_rows += output_table.num_rows
                file_wallet_fills += output_table.num_rows
                _add_stats(stats, output_table)
                for market, count in Counter(output_table["market"].to_pylist()).items():
                    market_fill_counts[market] += count
            source_files.append({
                "date": day_text,
                "url": url,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "source_rows": source_rows,
                "cohort_native_perp_trade_rows": file_filtered,
                "cohort_wallet_fill_rows": file_wallet_fills,
            })
            raw_paths.append(path)
            print(json.dumps({
                "date": day_text,
                "source_rows": source_rows,
                "cohort_native_perp_trade_rows": file_filtered,
                "cohort_wallet_fill_rows": file_wallet_fills,
                "download_mb": round(size_bytes / 1_000_000, 2),
            }))
    finally:
        if writer is not None:
            writer.close()
    if not output_path.exists():
        raise RuntimeError("no native-perp trades matched the frozen wallet cohort")

    market_bars_path = out_dir / "market_15m.parquet"
    import duckdb

    connection = duckdb.connect()
    connection.execute("SET memory_limit='6GB'")
    connection.execute("SET threads=2")
    raw_files_sql = "[" + ", ".join(
        "'" + str(path.resolve()).replace("'", "''") + "'"
        for path in raw_paths
    ) + "]"
    connection.execute(
        f"CREATE VIEW raw_trades AS SELECT * FROM read_parquet({raw_files_sql})"
    )
    market_bars_target = str(market_bars_path).replace("'", "''")
    connection.execute(
        f"""
        COPY (
            SELECT
                market,
                coin,
                time_bucket(INTERVAL '15 minutes', timestamp) AS bucket_ts,
                first(price ORDER BY timestamp, tid) AS open,
                max(price) AS high,
                min(price) AS low,
                last(price ORDER BY timestamp, tid) AS close,
                sum(size) AS base_volume,
                sum(notional) AS quote_volume,
                count(*) AS trade_count,
                sum(CASE WHEN taker_side = 'buy' THEN notional ELSE 0 END)
                    AS taker_buy_notional,
                sum(CASE WHEN taker_side = 'sell' THEN notional ELSE 0 END)
                    AS taker_sell_notional
            FROM raw_trades
            WHERE market_class = 'perp'
              AND coalesce(dex, '') = ''
            GROUP BY market, coin, bucket_ts
            ORDER BY market, bucket_ts
        )
        TO '{market_bars_target}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    connection.close()
    market_bars_rows = pq.ParquetFile(market_bars_path).metadata.num_rows
    for raw_path in raw_paths:
        raw_path.unlink(missing_ok=True)

    wallet_summaries = _serialize_wallet_stats(cohort_rows, stats)
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": {
            "dataset_id": DATASET_ID,
            "dataset_url": DATASET_URL,
            "file_url_pattern": FILE_URL,
            "start_date_utc": start.isoformat(),
            "end_date_utc": end.isoformat(),
            "file_count": len(source_files),
            "expected_file_count": 37 if start == START_DATE and end == END_DATE else len(source_files),
            "expected_total_source_rows": EXPECTED_SOURCE_ROWS if start == START_DATE and end == END_DATE else None,
            "total_source_rows": total_source_rows,
            "source_row_count_matches_dataset_card": (
                total_source_rows == EXPECTED_SOURCE_ROWS
                if start == START_DATE and end == END_DATE else None
            ),
            "license": "CC BY 4.0 for files published before 2026-11-10",
            "attribution": "Ironflow (ironflow.sh)",
            "license_note": "Only 2026-10-03 through 2026-11-08 files are used; newer files are excluded because their license changed to CC BY-NC 4.0.",
            "source_files": source_files,
        },
        "cohort": {
            "snapshot_time_utc": cohort_snapshot.get("generated_at_utc"),
            "cohort_manifest_sha256": (
                hashlib.sha256(cohort_manifest_path.read_bytes()).hexdigest()
                if cohort_manifest_path and cohort_manifest_path.exists() else None
            ),
            "source": cohort_snapshot.get("source"),
            "selection": cohort_snapshot.get("cohort_selection"),
            "survivorship_warning": cohort_snapshot.get("survivorship_warning"),
            "wallet_count": len(cohort_rows),
            "month_pnl_buckets": {
                "positive": sum(float(row.get("month_pnl") or 0) > 0 for row in cohort_rows),
                "negative": sum(float(row.get("month_pnl") or 0) < 0 for row in cohort_rows),
                "flat": sum(float(row.get("month_pnl") or 0) == 0 for row in cohort_rows),
            },
        },
        "filters": {
            "market_class": "perp",
            "dex": "empty string only (native Hyperliquid perps; excludes HIP-3)",
            "wallet_side": "one output row for each cohort wallet that is buyer or seller",
            "spot_excluded": True,
            "hip3_excluded": True,
        },
        "counts": {
            "cohort_native_perp_trade_rows": filtered_trade_rows,
            "cohort_wallet_fill_rows": wallet_fill_rows,
            "trades_with_both_sides_in_cohort": both_cohort_sides,
            "trades_with_same_wallet_on_both_sides": same_wallet_both_sides,
            "market_fill_counts": dict(market_fill_counts),
        },
        "wallet_summaries": wallet_summaries,
        "group_comparison_by_current_month_pnl": _group_comparison(wallet_summaries),
        "limitations": [
            "This archive has one matched trade row with buyer/seller and taker_side, not the full per-wallet fill schema.",
            "It does not include per-wallet startPosition, dir, fee, closedPnl, twapId or current position state.",
            "Therefore it supports side/size/aggression/market/time-of-day behaviour analysis, but not exact position holding-time or realized-PnL reconstruction.",
            "Current leaderboard PnL labels are a prospective snapshot, not a historical point-in-time cohort; group comparisons are descriptive and not causal.",
            "This is Hyperliquid cross-venue behaviour evidence, not BingX signals or executions.",
        ],
        "output": {
            "wallet_trades_parquet": str(output_path),
            "wallet_trades_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            "wallet_trades_size_bytes": output_path.stat().st_size,
            "market_15m_parquet": str(market_bars_path),
            "market_15m_sha256": hashlib.sha256(market_bars_path.read_bytes()).hexdigest(),
            "market_15m_rows": market_bars_rows,
            "market_15m_size_bytes": market_bars_path.stat().st_size,
        },
    }
    (out_dir / "wallet_archive_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-path", type=Path, required=True)
    parser.add_argument("--cohort-manifest-path", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/hyperliquid_wallet_archive"))
    parser.add_argument("--start-date", type=date.fromisoformat, default=START_DATE)
    parser.add_argument("--end-date", type=date.fromisoformat, default=END_DATE)
    args = parser.parse_args()
    report = collect(
        args.cohort_path, args.out_dir, args.start_date, args.end_date,
        cohort_manifest_path=args.cohort_manifest_path,
    )
    print(json.dumps({
        "total_source_rows": report["source"]["total_source_rows"],
        "cohort_native_perp_trade_rows": report["counts"]["cohort_native_perp_trade_rows"],
        "cohort_wallet_fill_rows": report["counts"]["cohort_wallet_fill_rows"],
        "wallet_trades_size_bytes": report["output"]["wallet_trades_size_bytes"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
