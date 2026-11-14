#!/usr/bin/env python3
"""Reconstruct cohort position episodes from a dated Hyperliquid position anchor."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

ANCHOR_UTC = datetime(2026, 10, 19, 0, 2, tzinfo=UTC)
BOUNDARY_GUARD_SECONDS = 60
EPSILON = 1e-9
POSITION_MATCH_ABS_TOLERANCE = 1e-6
POSITIONS_URL = (
    "https://huggingface.co/datasets/Hydromancer/hyperliquid-reservoir/"
    "resolve/main/snapshots/positions_2026-10-19.parquet"
)
ACCOUNT_VALUES_URL = (
    "https://huggingface.co/datasets/Hydromancer/hyperliquid-reservoir/"
    "resolve/main/snapshots/account_values_2026-10-19.parquet"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _positions_match(left: float, right: float) -> bool:
    return math.isclose(
        left, right, rel_tol=1e-9, abs_tol=POSITION_MATCH_ABS_TOLERANCE
    )


def _load_cohort(path: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    wallets = payload.get("wallets", [])
    addresses = []
    labels: dict[str, dict[str, Any]] = {}
    for row in wallets:
        address = str(row.get("ethAddress") or row.get("address") or "").lower()
        if not address:
            continue
        perf = dict(row.get("windowPerformances") or [])
        month = perf.get("month") or {}
        week = perf.get("week") or {}
        labels[address] = {
            "cohort_strata": row.get("cohort_strata") or [],
            "snapshot_account_value": row.get("accountValue"),
            "snapshot_month_pnl": month.get("pnl"),
            "snapshot_month_roi": month.get("roi"),
            "snapshot_month_volume": month.get("vlm"),
            "snapshot_week_pnl": week.get("pnl"),
        }
        addresses.append(address)
    if not addresses:
        raise ValueError("cohort snapshot contains no wallet addresses")
    return sorted(set(addresses)), labels


def _weighted_average_entry(
    position: float, average_entry: float | None, delta: float, price: float
) -> tuple[float, float | None, float | None, bool]:
    """Return new size, new average entry, realized gross PnL, and PnL-known flag."""
    if abs(position) <= EPSILON:
        return delta, price if abs(delta) > EPSILON else None, 0.0, True

    if abs(delta) <= EPSILON:
        return position, average_entry, 0.0, True

    if position * delta > 0:
        if average_entry is None:
            return position + delta, None, None, False
        new_size = position + delta
        new_average = (
            abs(position) * average_entry + abs(delta) * price
        ) / abs(new_size)
        return new_size, new_average, 0.0, True

    closed_size = min(abs(position), abs(delta))
    realized = None
    known = average_entry is not None
    if known:
        realized = closed_size * (price - float(average_entry)) * (1.0 if position > 0 else -1.0)

    new_size = position + delta
    if abs(new_size) <= EPSILON:
        return 0.0, None, realized, known
    if position * new_size < 0:
        return new_size, price, realized, known
    return new_size, average_entry, realized, known


def _new_episode(
    wallet: str,
    market: str,
    size: float,
    opened_at: datetime | None,
    left_censored: bool,
    anchor: dict[str, Any],
) -> dict[str, Any]:
    return {
        "wallet": wallet,
        "market": market,
        "side": "long" if size > 0 else "short",
        "opened_at_utc": opened_at.isoformat() if opened_at else None,
        "closed_at_utc": None,
        "hold_minutes": None,
        "left_censored": left_censored,
        "right_censored": False,
        "anchor_snapshot_present": anchor.get("anchor_present", False),
        "anchor_size": anchor.get("anchor_size", 0.0),
        "anchor_entry_price": anchor.get("anchor_entry_price"),
        "anchor_leverage_type": anchor.get("anchor_leverage_type"),
        "anchor_leverage": anchor.get("anchor_leverage"),
        "anchor_funding_pnl": anchor.get("anchor_funding_pnl"),
        "anchor_account_value": anchor.get("anchor_account_value"),
        "anchor_liquidation_price": anchor.get("anchor_liquidation_price"),
        "initial_size": abs(size),
        "max_abs_position_size": abs(size),
        "max_notional_at_fill": 0.0,
        "fill_count": 0,
        "add_count": 0,
        "reduce_count": 0,
        "reverse_count": 0,
        "liquidation_fill_count": 0,
        "taker_fill_count": 0,
        "maker_fill_count": 0,
        "gross_fill_notional": 0.0,
        "gross_realized_pnl_known_usd": 0.0,
        "pnl_complete": not left_censored,
        "pnl_unknown_close_count": 0,
        "anchor_entry_price_error_pct": None,
        "anchor_entry_price_validated": False,
    }


def _finish_episode(
    episode: dict[str, Any],
    closed_at: datetime | None,
    right_censored: bool,
) -> dict[str, Any]:
    episode["closed_at_utc"] = closed_at.isoformat() if closed_at else None
    episode["right_censored"] = right_censored
    if episode["opened_at_utc"] and episode["closed_at_utc"]:
        opened = datetime.fromisoformat(episode["opened_at_utc"])
        closed = datetime.fromisoformat(episode["closed_at_utc"])
        episode["hold_minutes"] = round((closed - opened).total_seconds() / 60.0, 6)
    else:
        episode["hold_minutes"] = None
    if episode["left_censored"] or episode["right_censored"]:
        episode["pnl_complete"] = False
    return episode


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def reconstruct(
    wallet_trades_path: Path,
    positions_path: Path,
    account_values_path: Path,
    cohort_path: Path,
    out_dir: Path,
    anchor_utc: datetime = ANCHOR_UTC,
    guard_seconds: int = BOUNDARY_GUARD_SECONDS,
) -> dict[str, Any]:
    for path in (wallet_trades_path, positions_path, account_values_path, cohort_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    addresses, cohort_labels = _load_cohort(cohort_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads = 4")
    con.execute("SET memory_limit = '4GB'")
    con.execute(f"SET temp_directory = '{sql_path(out_dir / 'duckdb_tmp')}'")

    trade_file = sql_path(wallet_trades_path)
    position_file = sql_path(positions_path)
    account_file = sql_path(account_values_path)
    address_values = ",".join("'" + address.replace("'", "''") + "'" for address in addresses)
    anchor_literal = anchor_utc.strftime("%Y-%m-%d %H:%M:%S")
    guard = int(guard_seconds)

    con.execute(f"""
        CREATE TEMP VIEW cohort_fills AS
        SELECT
            lower(wallet) AS wallet,
            upper(market) AS market,
            timestamp,
            CAST(price AS DOUBLE) AS price,
            CAST(size AS DOUBLE) AS size,
            CAST(notional AS DOUBLE) AS notional,
            lower(side) AS side,
            CAST(crossed AS BOOLEAN) AS crossed,
            CAST(liquidation AS BOOLEAN) AS liquidation,
            CAST(tid AS BIGINT) AS tid,
            CASE WHEN lower(side) = 'buy' THEN CAST(size AS DOUBLE)
                 ELSE -CAST(size AS DOUBLE) END AS delta
        FROM read_parquet('{trade_file}')
        WHERE lower(wallet) IN ({address_values})
          -- wallet_trades.parquet is already filtered to native perps by the collector.
          AND market IS NOT NULL
          AND lower(side) IN ('buy', 'sell')
    """)

    con.execute(f"""
        CREATE TEMP VIEW anchor_positions AS
        SELECT
            lower(user) AS wallet,
            upper(market) AS market,
            CAST(size AS DOUBLE) AS anchor_size,
            CAST(entry_price AS DOUBLE) AS anchor_entry_price,
            CAST(liquidation_price AS DOUBLE) AS anchor_liquidation_price,
            leverage_type AS anchor_leverage_type,
            CAST(leverage AS DOUBLE) AS anchor_leverage,
            CAST(funding_pnl AS DOUBLE) AS anchor_funding_pnl,
            CAST(account_value AS DOUBLE) AS anchor_account_value,
            account_mode AS anchor_account_mode
        FROM read_parquet('{position_file}')
        WHERE lower(user) IN ({address_values})
          AND strpos(market, ':') = 0
    """)

    con.execute(f"""
        CREATE TEMP VIEW anchor_accounts AS
        SELECT
            lower(user) AS wallet,
            CAST(account_value AS DOUBLE) AS account_value_2026_10_19,
            CAST(total_long_notional AS DOUBLE) AS total_long_notional_2026_10_19,
            CAST(total_short_notional AS DOUBLE) AS total_short_notional_2026_10_19,
            account_mode AS account_mode_2026_10_19,
            is_unified,
            CAST(pm_ratio AS DOUBLE) AS pm_ratio
        FROM read_parquet('{account_file}')
        WHERE lower(user) IN ({address_values})
          AND coalesce(dex, '') IN ('', 'hyperliquid')
    """)

    con.execute(f"""
        CREATE TEMP VIEW group_stats AS
        SELECT
            f.wallet,
            f.market,
            max(CASE WHEN a.wallet IS NOT NULL THEN 1 ELSE 0 END) AS anchor_present,
            coalesce(max(a.anchor_size), 0.0) AS anchor_size,
            max(a.anchor_entry_price) AS anchor_entry_price,
            max(a.anchor_liquidation_price) AS anchor_liquidation_price,
            max(a.anchor_leverage_type) AS anchor_leverage_type,
            max(a.anchor_leverage) AS anchor_leverage,
            max(a.anchor_funding_pnl) AS anchor_funding_pnl,
            max(a.anchor_account_value) AS anchor_account_value,
            count(*) AS fill_count,
            sum(CASE WHEN f.timestamp < TIMESTAMP '{anchor_literal}' - INTERVAL '{guard} seconds'
                     THEN f.delta ELSE 0 END) AS delta_pre_anchor,
            sum(CASE WHEN f.timestamp > TIMESTAMP '{anchor_literal}' + INTERVAL '{guard} seconds'
                     THEN f.delta ELSE 0 END) AS delta_post_anchor,
            sum(CASE WHEN f.timestamp BETWEEN
                     TIMESTAMP '{anchor_literal}' - INTERVAL '{guard} seconds'
                     AND TIMESTAMP '{anchor_literal}' + INTERVAL '{guard} seconds'
                     THEN 1 ELSE 0 END) AS boundary_fill_count
        FROM cohort_fills f
        LEFT JOIN anchor_positions a USING (wallet, market)
        GROUP BY f.wallet, f.market
    """)

    groups = con.execute("""
        SELECT
            wallet, market, anchor_present, anchor_size, anchor_entry_price,
            anchor_liquidation_price, anchor_leverage_type, anchor_leverage,
            anchor_funding_pnl, anchor_account_value, fill_count,
            delta_pre_anchor, delta_post_anchor, boundary_fill_count,
            anchor_size - delta_pre_anchor AS start_size,
            anchor_size + delta_post_anchor AS end_size
        FROM group_stats
        ORDER BY wallet, market
    """).fetchdf()

    group_meta: dict[tuple[str, str], dict[str, Any]] = {}
    ambiguous_groups: list[dict[str, Any]] = []
    for row in groups.to_dict("records"):
        key = (row["wallet"], row["market"])
        meta = {
            "anchor_present": bool(row["anchor_present"]),
            "anchor_size": float(row["anchor_size"] or 0.0),
            "anchor_entry_price": (
                float(row["anchor_entry_price"])
                if row["anchor_entry_price"] is not None else None
            ),
            "anchor_liquidation_price": (
                float(row["anchor_liquidation_price"])
                if row["anchor_liquidation_price"] is not None else None
            ),
            "anchor_leverage_type": row["anchor_leverage_type"],
            "anchor_leverage": (
                float(row["anchor_leverage"])
                if row["anchor_leverage"] is not None else None
            ),
            "anchor_funding_pnl": (
                float(row["anchor_funding_pnl"])
                if row["anchor_funding_pnl"] is not None else None
            ),
            "anchor_account_value": (
                float(row["anchor_account_value"])
                if row["anchor_account_value"] is not None else None
            ),
            "start_size": float(row["start_size"] or 0.0),
            "end_size": float(row["end_size"] or 0.0),
            "boundary_fill_count": int(row["boundary_fill_count"] or 0),
            "fill_count": int(row["fill_count"] or 0),
        }
        group_meta[key] = meta
        if meta["boundary_fill_count"]:
            ambiguous_groups.append({
                "wallet": key[0],
                "market": key[1],
                "boundary_fill_count": meta["boundary_fill_count"],
                "fill_count": meta["fill_count"],
            })

    episodes: list[dict[str, Any]] = []
    entry_validation: list[dict[str, Any]] = []
    current_key: tuple[str, str] | None = None
    current_episode: dict[str, Any] | None = None
    current_size = 0.0
    average_entry: float | None = None
    anchor_checked = False
    anchor_position_error_count = 0

    def check_anchor(key: tuple[str, str]) -> None:
        nonlocal average_entry, anchor_checked, anchor_position_error_count
        meta = group_meta[key]
        if anchor_checked:
            return
        size_error = current_size - meta["anchor_size"]
        size_ok = abs(size_error) <= 1e-7 * max(1.0, abs(meta["anchor_size"]))
        if not size_ok:
            anchor_position_error_count += 1
        entry_error_pct = None
        entry_ok = None
        if meta["anchor_size"] and meta["anchor_entry_price"]:
            if meta["start_size"] == 0 and average_entry is not None:
                entry_error_pct = (
                    abs(average_entry / meta["anchor_entry_price"] - 1.0) * 100.0
                )
                entry_ok = entry_error_pct <= 0.05
                if current_episode is not None:
                    current_episode["anchor_entry_price_error_pct"] = entry_error_pct
                    current_episode["anchor_entry_price_validated"] = entry_ok
            elif meta["start_size"] != 0:
                entry_ok = None
        entry_validation.append({
            "wallet": key[0],
            "market": key[1],
            "start_size": meta["start_size"],
            "anchor_size": meta["anchor_size"],
            "reconstructed_anchor_size": current_size,
            "anchor_size_match": size_ok,
            "computed_entry_price": average_entry if meta["start_size"] == 0 else None,
            "snapshot_entry_price": meta["anchor_entry_price"],
            "entry_price_error_pct": entry_error_pct,
            "entry_price_match": entry_ok,
        })
        if meta["anchor_size"] and meta["anchor_entry_price"] is not None:
            average_entry = meta["anchor_entry_price"]
        else:
            average_entry = None
        anchor_checked = True

    def finish_group() -> None:
        nonlocal current_episode
        if current_key is None:
            return
        check_anchor(current_key)
        if current_episode is not None:
            episodes.append(_finish_episode(current_episode, None, True))
            current_episode = None

    query = f"""
        WITH base AS (
            SELECT
                wallet, market,
                anchor_size,
                anchor_entry_price,
                anchor_liquidation_price,
                anchor_leverage_type,
                anchor_leverage,
                anchor_funding_pnl,
                anchor_account_value,
                anchor_size - delta_pre_anchor AS start_size
            FROM group_stats
            WHERE boundary_fill_count = 0
        ),
        path AS (
            SELECT
                f.*,
                b.start_size,
                b.anchor_size,
                b.anchor_entry_price,
                b.anchor_liquidation_price,
                b.anchor_leverage_type,
                b.anchor_leverage,
                b.anchor_funding_pnl,
                b.anchor_account_value,
                b.start_size + coalesce(
                    sum(f.delta) OVER (
                        PARTITION BY f.wallet, f.market
                        ORDER BY f.timestamp, f.tid
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ), 0.0
                ) AS position_before,
                b.start_size + sum(f.delta) OVER (
                    PARTITION BY f.wallet, f.market
                    ORDER BY f.timestamp, f.tid
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS position_after
            FROM cohort_fills f
            JOIN base b USING (wallet, market)
            WHERE f.timestamp < TIMESTAMP '{anchor_literal}' - INTERVAL '{guard} seconds'
               OR f.timestamp > TIMESTAMP '{anchor_literal}' + INTERVAL '{guard} seconds'
        )
        SELECT *
        FROM path
        ORDER BY wallet, market, timestamp, tid
    """
    reader = con.sql(query).to_arrow_reader(batch_size=100_000)
    for batch in reader:
        for row in batch.to_pylist():
            key = (row["wallet"], row["market"])
            if key != current_key:
                finish_group()
                current_key = key
                meta = group_meta[key]
                current_size = meta["start_size"]
                average_entry = None
                anchor_checked = False
                current_episode = None
                if abs(current_size) > EPSILON:
                    current_episode = _new_episode(
                        key[0], key[1], current_size, None, True, meta
                    )
            timestamp = row["timestamp"]
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            else:
                timestamp = timestamp.astimezone(UTC)
            if timestamp > anchor_utc + timedelta(seconds=guard) and not anchor_checked:
                check_anchor(key)

            before = float(row["position_before"] or 0.0)
            after = float(row["position_after"] or 0.0)
            delta = float(row["delta"] or 0.0)
            price = float(row["price"] or 0.0)
            if not _positions_match(current_size, before):
                raise RuntimeError(
                    f"position path discontinuity for {key}: "
                    f"python={current_size}, sql_before={before}"
                )

            if abs(before) <= EPSILON and abs(after) > EPSILON:
                current_episode = _new_episode(
                    key[0], key[1], after, timestamp, False, group_meta[key]
                )
            elif abs(before) > EPSILON and current_episode is None:
                current_episode = _new_episode(
                    key[0], key[1], before, None, True, group_meta[key]
                )

            action = "hold"
            if abs(before) <= EPSILON and abs(after) > EPSILON:
                action = "open"
            elif abs(before) > EPSILON and abs(after) <= EPSILON:
                action = "close"
            elif before * after < -EPSILON:
                action = "reverse"
            elif abs(after) > abs(before):
                action = "add"
            elif abs(after) < abs(before):
                action = "reduce"

            if current_episode is not None:
                current_episode["fill_count"] += 1
                current_episode["gross_fill_notional"] += float(row["notional"] or 0.0)
                current_episode["max_abs_position_size"] = max(
                    current_episode["max_abs_position_size"], abs(after)
                )
                current_episode["max_notional_at_fill"] = max(
                    current_episode["max_notional_at_fill"], abs(after) * price
                )
                if row["crossed"]:
                    current_episode["taker_fill_count"] += 1
                else:
                    current_episode["maker_fill_count"] += 1
                if row["liquidation"]:
                    current_episode["liquidation_fill_count"] += 1

                if action == "add":
                    current_episode["add_count"] += 1
                elif action in {"reduce", "close", "reverse"}:
                    current_episode["reduce_count"] += 1

            _new_size, new_average, realized, pnl_known = _weighted_average_entry(
                before, average_entry, delta, price
            )
            if current_episode is not None and action in {"reduce", "close", "reverse"}:
                if realized is None or not pnl_known:
                    current_episode["pnl_unknown_close_count"] += 1
                    current_episode["pnl_complete"] = False
                else:
                    current_episode["gross_realized_pnl_known_usd"] += realized

            if action == "close":
                if current_episode is not None:
                    episodes.append(_finish_episode(current_episode, timestamp, False))
                current_episode = None
                new_average = None
            elif action == "reverse":
                if current_episode is not None:
                    current_episode["reverse_count"] = current_episode.get("reverse_count", 0) + 1
                    episodes.append(_finish_episode(current_episode, timestamp, False))
                current_episode = _new_episode(
                    key[0], key[1], after, timestamp, False, group_meta[key]
                )
                current_episode["fill_count"] = 1
                current_episode["gross_fill_notional"] = float(row["notional"] or 0.0)
                current_episode["max_abs_position_size"] = abs(after)
                current_episode["max_notional_at_fill"] = abs(after) * price
                current_episode["taker_fill_count"] = 1 if row["crossed"] else 0
                current_episode["maker_fill_count"] = 0 if row["crossed"] else 1
                current_episode["liquidation_fill_count"] = 1 if row["liquidation"] else 0
                current_episode["reverse_count"] = 1
                current_episode["gross_realized_pnl_known_usd"] = 0.0
                current_episode["pnl_unknown_close_count"] = 0
                current_episode["pnl_complete"] = True
                new_average = price
            current_size = after
            average_entry = new_average

    finish_group()

    anchor_only = con.execute("""
        SELECT a.*
        FROM anchor_positions a
        LEFT JOIN group_stats g USING (wallet, market)
        WHERE g.wallet IS NULL
        ORDER BY a.wallet, a.market
    """).fetchall()
    for row in anchor_only:
        wallet, market, size, entry, liq, lev_type, lev, funding, account_value, _account_mode = row
        if abs(float(size)) <= EPSILON:
            continue
        meta = {
            "anchor_present": True,
            "anchor_size": float(size),
            "anchor_entry_price": float(entry) if entry is not None else None,
            "anchor_liquidation_price": float(liq) if liq is not None else None,
            "anchor_leverage_type": lev_type,
            "anchor_leverage": float(lev) if lev is not None else None,
            "anchor_funding_pnl": float(funding) if funding is not None else None,
            "anchor_account_value": float(account_value) if account_value is not None else None,
        }
        episode = _new_episode(wallet, market, float(size), None, True, meta)
        episode["right_censored"] = True
        episodes.append(_finish_episode(episode, None, True))

    account_rows = con.execute("""
        SELECT
            wallet,
            max(account_value_2026_10_19) AS account_value_2026_10_19,
            max(total_long_notional_2026_10_19) AS total_long_notional_2026_10_19,
            max(total_short_notional_2026_10_19) AS total_short_notional_2026_10_19,
            max(account_mode_2026_10_19) AS account_mode_2026_10_19,
            max(is_unified) AS is_unified,
            max(pm_ratio) AS pm_ratio
        FROM anchor_accounts
        GROUP BY wallet
    """).fetchall()
    account_map = {
        row[0]: {
            "account_value_2026_10_19": row[1],
            "total_long_notional_2026_10_19": row[2],
            "total_short_notional_2026_10_19": row[3],
            "account_mode_2026_10_19": row[4],
            "is_unified": row[5],
            "pm_ratio": row[6],
            "gross_exposure_to_equity": (
                (abs(float(row[2] or 0)) + abs(float(row[3] or 0))) / float(row[1])
                if row[1] not in (None, 0) else None
            ),
        }
        for row in account_rows
    }

    wallet_acc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    market_acc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        wallet_acc[episode["wallet"]].append(episode)
        market_acc[episode["market"]].append(episode)

    wallet_summary = []
    for address in addresses:
        items = wallet_acc.get(address, [])
        holds = [row["hold_minutes"] for row in items if row["hold_minutes"] is not None]
        wallet_summary.append({
            "wallet": address,
            **cohort_labels.get(address, {}),
            **account_map.get(address, {}),
            "position_episodes": len(items),
            "complete_episodes": sum(
                not row["left_censored"] and not row["right_censored"] for row in items
            ),
            "left_censored_episodes": sum(row["left_censored"] for row in items),
            "right_censored_episodes": sum(row["right_censored"] for row in items),
            "long_episodes": sum(row["side"] == "long" for row in items),
            "short_episodes": sum(row["side"] == "short" for row in items),
            "median_hold_minutes": (
                statistics.median(holds) if holds else None
            ),
            "mean_hold_minutes": sum(holds) / len(holds) if holds else None,
            "gross_realized_pnl_known_usd": sum(
                row["gross_realized_pnl_known_usd"] for row in items
            ),
            "episodes_with_complete_pnl": sum(row["pnl_complete"] for row in items),
            "episodes_with_partial_pnl": sum(
                not row["pnl_complete"] for row in items
            ),
            "taker_share": (
                sum(row["taker_fill_count"] for row in items)
                / max(1, sum(row["fill_count"] for row in items))
                if items else None
            ),
        })

    market_summary = []
    for market, items in market_acc.items():
        holds = [row["hold_minutes"] for row in items if row["hold_minutes"] is not None]
        market_summary.append({
            "market": market,
            "wallets": len({row["wallet"] for row in items}),
            "episodes": len(items),
            "complete_episodes": sum(
                not row["left_censored"] and not row["right_censored"] for row in items
            ),
            "median_hold_minutes": (
                sorted(holds)[len(holds) // 2] if holds else None
            ),
            "long_episodes": sum(row["side"] == "long" for row in items),
            "short_episodes": sum(row["side"] == "short" for row in items),
        })
    market_summary.sort(key=lambda row: row["episodes"], reverse=True)

    episodes_path = out_dir / "position_episodes.csv"
    wallet_path = out_dir / "wallet_position_summary.csv"
    market_path = out_dir / "market_position_summary.csv"
    validation_path = out_dir / "anchor_entry_price_validation.csv"
    ambiguous_path = out_dir / "anchor_boundary_ambiguous_groups.csv"

    episode_fields = [
        "wallet", "market", "side", "opened_at_utc", "closed_at_utc", "hold_minutes",
        "left_censored", "right_censored", "anchor_snapshot_present", "anchor_size",
        "anchor_entry_price", "anchor_leverage_type", "anchor_leverage",
        "anchor_funding_pnl", "anchor_account_value", "anchor_liquidation_price",
        "initial_size", "max_abs_position_size", "max_notional_at_fill", "fill_count",
        "add_count", "reduce_count", "reverse_count", "liquidation_fill_count",
        "taker_fill_count", "maker_fill_count", "gross_fill_notional",
        "gross_realized_pnl_known_usd", "pnl_complete", "pnl_unknown_close_count",
        "anchor_entry_price_error_pct", "anchor_entry_price_validated",
    ]
    _write_csv(episodes_path, episodes, episode_fields)
    _write_csv(wallet_path, wallet_summary, sorted({
        key for row in wallet_summary for key in row
    }))
    _write_csv(market_path, market_summary, [
        "market", "wallets", "episodes", "complete_episodes",
        "median_hold_minutes", "long_episodes", "short_episodes",
    ])
    _write_csv(validation_path, entry_validation, [
        "wallet", "market", "start_size", "anchor_size", "reconstructed_anchor_size",
        "anchor_size_match", "computed_entry_price", "snapshot_entry_price",
        "entry_price_error_pct", "entry_price_match",
    ])
    _write_csv(ambiguous_path, ambiguous_groups, [
        "wallet", "market", "boundary_fill_count", "fill_count",
    ])

    complete_holds = [
        row["hold_minutes"] for row in episodes
        if row["hold_minutes"] is not None
        and not row["left_censored"] and not row["right_censored"]
    ]
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": {
            "trade_archive_sha256": sha256_file(wallet_trades_path),
            "positions_snapshot_url": POSITIONS_URL,
            "positions_snapshot_sha256": sha256_file(positions_path),
            "account_values_snapshot_url": ACCOUNT_VALUES_URL,
            "account_values_snapshot_sha256": sha256_file(account_values_path),
            "snapshot_dataset": "Hydromancer/hyperliquid-reservoir",
            "snapshot_license": "CC BY 4.0",
            "snapshot_attribution": "Hydromancer Reservoir",
            "anchor_time_utc_rounded": anchor_utc.isoformat(),
            "anchor_boundary_guard_seconds": guard_seconds,
            "cohort_wallet_count": len(addresses),
        },
        "counts": {
            "wallet_trade_rows": int(con.execute(
                f"SELECT count(*) FROM read_parquet('{trade_file}')"
            ).fetchone()[0]),
            "anchor_position_rows_for_cohort": int(con.execute(
                "SELECT count(*) FROM anchor_positions"
            ).fetchone()[0]),
            "wallets_with_anchor_positions": int(con.execute(
                "SELECT count(DISTINCT wallet) FROM anchor_positions"
            ).fetchone()[0]),
            "wallet_market_groups_with_fills": len(group_meta),
            "wallet_market_groups_boundary_ambiguous": len(ambiguous_groups),
            "wallet_market_groups_size_mismatch": anchor_position_error_count,
            "position_episodes": len(episodes),
            "complete_position_episodes": sum(
                not row["left_censored"] and not row["right_censored"] for row in episodes
            ),
            "complete_episode_hold_observations": len(complete_holds),
            "anchor_entry_price_validation_rows": len(entry_validation),
            "anchor_entry_price_validated_rows": sum(
                row["entry_price_match"] is not None for row in entry_validation
            ),
            "anchor_entry_price_matches_005pct": sum(
                row["entry_price_match"] is True for row in entry_validation
            ),
            "accounts_with_native_account_value_snapshot": len(account_map),
        },
        "holding_time_minutes": {
            "median_complete_episode": (
                statistics.median(complete_holds)
                if complete_holds else None
            ),
            "p90_complete_episode": (
                sorted(complete_holds)[min(len(complete_holds) - 1, int(len(complete_holds) * 0.9))]
                if complete_holds else None
            ),
        },
        "outputs": {
            "position_episodes": episodes_path.name,
            "wallet_position_summary": wallet_path.name,
            "market_position_summary": market_path.name,
            "anchor_entry_price_validation": validation_path.name,
            "anchor_boundary_ambiguous_groups": ambiguous_path.name,
        },
        "limitations": [
            "The Hydromancer sample is one position/account-value snapshot at 2026-10-19 00:02 UTC, not a daily history.",
            "The published snapshot time is rounded to the minute; wallet-market groups with fills inside the +/- guard are excluded from episode reconstruction.",
            "Position paths are retrospective reconstructions anchored with a mid-window snapshot. They are not a point-in-time signal history and must not be used as if the future anchor were available to a live strategy.",
            "Ironflow matched trades do not include per-wallet fees or closedPnl. Realized PnL here is a gross average-entry estimate only; funding is recorded only for positions open at the anchor.",
            "Episodes touching the start or end of the 37-day window are censored; their full holding time is unknown.",
            "The sample is Hyperliquid cross-venue behaviour evidence, not BingX executions or costs.",
        ],
    }
    (out_dir / "position_reconstruction_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    con.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet-trades", type=Path, required=True)
    parser.add_argument("--positions", type=Path, required=True)
    parser.add_argument("--account-values", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/hyperliquid_position_reconstruction"))
    args = parser.parse_args()
    report = reconstruct(
        args.wallet_trades,
        args.positions,
        args.account_values,
        args.cohort,
        args.out_dir,
    )
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
