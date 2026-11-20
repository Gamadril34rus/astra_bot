#!/usr/bin/env python3
"""Feasibility audit for direction-aware Hyperliquid inventory reconstruction.

Only wallets whose archived fill count is <= the public API's 10,000-fill
history ceiling are queried. Raw addresses/fills are never written to outputs.
The output is a small/large *relative cohort* pilot, not a representative
retail-vs-whale sample or a strategy backtest.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.hyperliquid.xyz/info"
CORE_COINS = {"BTC", "ETH", "SOL", "HYPE", "INJ"}
PAGE_SIZE = 2_000
MAX_FILLS_PER_ADDRESS = 10_000
BAR_MS = 15 * 60 * 1_000
CALIBRATION_DAYS = 7
MIN_COHORT_WALLETS = 6


def post_info(payload: dict[str, Any], *, timeout: int = 20) -> list[dict[str, Any]]:
    request = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    for attempt in range(4):
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected Hyperliquid response: {str(data)[:500]}")
            return data
        except HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Hyperliquid Info API HTTP {exc.code}: {detail[:500]}") from exc
            time.sleep((5, 15, 30)[attempt])
        except URLError as exc:
            if attempt == 3:
                raise RuntimeError(f"Hyperliquid Info API connection error: {exc}") from exc
            time.sleep((2, 5, 10)[attempt])
    raise RuntimeError("Hyperliquid Info API retry loop exhausted")


def _leaderboard_month_volume(row: dict[str, Any]) -> float:
    performances = row.get("windowPerformances", [])
    if isinstance(performances, dict):
        month = performances.get("month", {})
    else:
        month = next(
            (
                item[1] for item in performances
                if isinstance(item, list) and len(item) == 2 and item[0] == "month"
                and isinstance(item[1], dict)
            ),
            {},
        )
    try:
        volume = float(month.get("vlm") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return volume if math.isfinite(volume) and volume > 0 else 0.0


def select_wallets(manifest_path: Path, *, max_wallets: int) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summaries = manifest.get("wallet_summaries")
    if isinstance(summaries, list):
        selected = []
        for row in summaries:
            address = str(row.get("address", "")).lower()
            fills = int(row.get("native_perp_fills") or 0)
            markets = {
                item.get("market")
                for item in row.get("top_markets_by_notional", [])
                if isinstance(item, dict)
            }
            if (
                len(address) == 42
                and address.startswith("0x")
                and 0 < fills <= MAX_FILLS_PER_ADDRESS
                and markets.intersection({f"{coin}-PERP" for coin in CORE_COINS})
            ):
                selected.append({
                    "address": address,
                    "manifest_fills": fills,
                    "month_volume": float(row.get("month_volume") or 0.0),
                })
        selected.sort(key=lambda row: row["manifest_fills"])
        return selected[:max_wallets]

    leaderboard_rows = manifest.get("leaderboardRows")
    if not isinstance(leaderboard_rows, list):
        raise ValueError("manifest must contain wallet_summaries or leaderboardRows")
    candidates = []
    for row in leaderboard_rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("ethAddress", "")).lower()
        volume = _leaderboard_month_volume(row)
        if len(address) == 42 and address.startswith("0x") and volume > 0:
            candidates.append({
                "address": address,
                "manifest_fills": None,
                "month_volume": volume,
            })
    candidates.sort(key=lambda row: (row["month_volume"], row["address"]))
    if len(candidates) <= max_wallets:
        return candidates

    # Deterministic, evenly spaced sampling across monthly-volume deciles.
    sample: list[dict[str, Any]] = []
    decile_count = 10
    for decile in range(decile_count):
        lo = decile * len(candidates) // decile_count
        hi = (decile + 1) * len(candidates) // decile_count
        group = candidates[lo:hi]
        quota = max_wallets // decile_count + (1 if decile < max_wallets % decile_count else 0)
        for slot in range(min(quota, len(group))):
            index = min(len(group) - 1, int((slot + 0.5) * len(group) / min(quota, len(group))))
            sample.append(group[index])
    # Stable de-duplication protects against small deciles and rounding collisions.
    unique = {row["address"]: row for row in sample}
    return sorted(unique.values(), key=lambda row: (row["month_volume"], row["address"]))


def _fill_key(fill: dict[str, Any], *, twap: bool = False) -> tuple[Any, ...]:
    if fill.get("tid") is not None and not twap:
        return ("fill", str(fill["tid"]))
    return (
        "twap" if twap else "fill-fallback",
        fill.get("twapId"),
        fill.get("time"),
        fill.get("oid"),
        fill.get("coin"),
        fill.get("side"),
        fill.get("px"),
        fill.get("sz"),
    )


def fetch_wallet_history(
    address: str,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cursor = start_ms
    fills: dict[tuple[Any, ...], dict[str, Any]] = {}
    pages = 0
    capped = False
    boundary_collision = False
    boundary_collision_markets: set[str] = set()
    while pages < math.ceil(MAX_FILLS_PER_ADDRESS / PAGE_SIZE):
        page = post_info({
            "type": "userFillsByTime",
            "user": address,
            "startTime": cursor,
            "endTime": end_ms,
            "aggregateByTime": False,
        })
        pages += 1
        for fill in page:
            fills[_fill_key(fill)] = fill
        weight = 20 + math.ceil(len(page) / 20)
        time.sleep(weight / 20.0 + 0.1)
        if len(page) < PAGE_SIZE:
            break
        last_time = max(int(fill["time"]) for fill in page)
        boundary_rows = [fill for fill in page if int(fill["time"]) == last_time]
        if len(boundary_rows) > 1:
            boundary_collision = True
            boundary_collision_markets.update(
                str(fill.get("coin") or "") for fill in boundary_rows if fill.get("coin")
            )
        if last_time < cursor:
            raise RuntimeError("Hyperliquid fills pagination moved backwards")
        cursor = last_time + 1
        if len(fills) >= MAX_FILLS_PER_ADDRESS:
            capped = True
            break
    if len(fills) >= MAX_FILLS_PER_ADDRESS:
        capped = True

    twap_rows = post_info({"type": "userTwapSliceFills", "user": address})
    twap_capped = len(twap_rows) >= 2_000
    twap_weight = 20 + math.ceil(len(twap_rows) / 20)
    time.sleep(twap_weight / 20.0 + 0.1)
    for item in twap_rows:
        fill = item.get("fill", item)
        timestamp = int(fill.get("time") or 0)
        if start_ms <= timestamp <= end_ms:
            fills[_fill_key(fill, twap=True)] = fill
    ordered = sorted(
        fills.values(),
        key=lambda fill: (
            int(fill.get("time") or 0),
            int(fill.get("tid") or fill.get("oid") or 0),
            str(fill.get("coin") or ""),
        ),
    )
    return ordered, {
        "pages": pages,
        "api_fills": len(fills) - sum(1 for key in fills if key[0] == "twap"),
        "twap_fills": sum(1 for key in fills if key[0] == "twap"),
        "api_cap_reached": capped,
        "twap_cap_reached": twap_capped,
        "page_boundary_collision": boundary_collision,
        "boundary_collision_markets": sorted(boundary_collision_markets),
    }


def split_fill_inventory(
    start_position: float,
    side: str,
    size: float,
    price: float,
) -> dict[str, float]:
    """Split a net-mode fill into explicit open/add and close quantities."""
    if not all(math.isfinite(value) for value in (start_position, size, price)):
        raise ValueError("fill values must be finite")
    if size <= 0 or price <= 0:
        raise ValueError("fill size and price must be positive")
    open_long = close_long = open_short = close_short = 0.0
    if side == "B":
        close_short = min(size, abs(start_position)) if start_position < 0 else 0.0
        open_long = size - close_short
    elif side == "A":
        close_long = min(size, start_position) if start_position > 0 else 0.0
        open_short = size - close_long
    else:
        raise ValueError(f"unknown fill side: {side!r}")
    return {
        "open_long_usd": open_long * price,
        "close_long_usd": close_long * price,
        "open_short_usd": open_short * price,
        "close_short_usd": close_short * price,
        "position_after": start_position + (size if side == "B" else -size),
    }


def _position_tolerance(left: float, right: float) -> float:
    return max(1e-8, abs(left) * 1e-7, abs(right) * 1e-7)


def order_timestamp_group(fills: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Find a unique position-transition path; tid is not an execution sequence."""
    if len(fills) <= 1:
        return fills
    edges = []
    for fill in fills:
        try:
            start = float(fill["startPosition"])
            size = float(fill["sz"])
            side = str(fill["side"])
            if size <= 0 or not math.isfinite(start) or not math.isfinite(size):
                return None
            if side == "B":
                after = start + size
            elif side == "A":
                after = start - size
            else:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        edges.append((start, after, fill))
    starts = []
    for index, (start, _, _) in enumerate(edges):
        has_predecessor = any(
            other != index and abs(after - start) <= _position_tolerance(after, start)
            for other, (_, after, _) in enumerate(edges)
        )
        if not has_predecessor:
            starts.append(index)
    if len(starts) != 1:
        return None
    remaining = set(range(len(edges)))
    current = starts[0]
    ordered = []
    while remaining:
        if current not in remaining:
            return None
        remaining.remove(current)
        start, after, fill = edges[current]
        ordered.append(fill)
        if not remaining:
            break
        candidates = [
            index for index in remaining
            if abs(edges[index][0] - after) <= _position_tolerance(edges[index][0], after)
        ]
        if len(candidates) != 1:
            return None
        current = candidates[0]
    return ordered


def possible_direction_labels(
    start_position: float,
    side: str,
    size: float,
) -> set[str]:
    """Return API direction labels compatible with the position transition.

    Hyperliquid examples use "Open Long/Short" for some fills that add to an
    already-open position, so both Open and Add labels are accepted there.
    """
    if size <= 0:
        raise ValueError("fill size must be positive")
    if side == "B":
        if start_position < 0:
            if size <= abs(start_position):
                return {"Close Short"}
            return {"Short > Long", "Open Long", "Add Long"}
        return {"Open Long", "Add Long"}
    if side == "A":
        if start_position > 0:
            if size <= start_position:
                return {"Close Long"}
            return {"Long > Short", "Open Short", "Add Short"}
        return {"Open Short", "Add Short"}
    raise ValueError(f"unknown fill side: {side!r}")


def count_net_add_wallets(
    bucket_values: dict[str, float],
    *,
    cohort: str,
    direction: str,
) -> int:
    """Count distinct wallets whose net position inventory increased in-bar."""
    if cohort not in {"small", "large"} or direction not in {"long", "short"}:
        raise ValueError("cohort must be small/large and direction long/short")
    prefix = f"{cohort}_{direction}_net_wallet_"
    return sum(
        1 for key, value in bucket_values.items()
        if key.startswith(prefix) and value > 0
    )


def split_market_cohorts(
    candidates: list[tuple[float, str]],
) -> dict[str, set[str]]:
    """Split prior-only median exposure ranks into disjoint small/large cohorts."""
    ordered = sorted(candidates)
    if len(ordered) < MIN_COHORT_WALLETS:
        return {"small": set(), "large": set()}
    small_count = max(3, len(ordered) // 2)
    large_count = max(1, math.ceil(len(ordered) * 0.25))
    if small_count + large_count > len(ordered):
        return {"small": set(), "large": set()}
    return {
        "small": {address for _, address in ordered[:small_count]},
        "large": {address for _, address in ordered[-large_count:]},
    }


def audit(
    manifest_path: Path,
    out_dir: Path,
    *,
    start: datetime,
    end: datetime,
    max_wallets: int = 40,
) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("start/end must be timezone-aware and start before end")
    selected = select_wallets(manifest_path, max_wallets=max_wallets)
    if not selected:
        raise ValueError("no eligible wallets under the 10,000-fill API ceiling")
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    calibration_end_ms = start_ms + CALIBRATION_DAYS * 24 * 60 * 60 * 1000

    wallet_market_fills: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    wallet_stats = []
    incomplete_addresses: set[str] = set()
    total_pages = total_api_fills = total_twap_fills = 0
    for wallet in selected:
        rows, stats = fetch_wallet_history(
            wallet["address"], start_ms=start_ms, end_ms=end_ms
        )
        total_pages += stats["pages"]
        total_api_fills += stats["api_fills"]
        total_twap_fills += stats["twap_fills"]
        if (
            stats["api_cap_reached"]
            or stats["twap_cap_reached"]
            or stats["page_boundary_collision"]
        ):
            # A skipped same-timestamp fill may belong to any market for this
            # address; invalidate all of its market series conservatively.
            incomplete_addresses.add(wallet["address"])
        wallet_stats.append({
            "manifest_fills": wallet["manifest_fills"],
            "boundary_collision_markets": stats["boundary_collision_markets"],
            "returned_api_fills": stats["api_fills"],
            "returned_twap_fills": stats["twap_fills"],
            **{key: value for key, value in stats.items()
               if key not in {"api_fills", "twap_fills"}},
        })
        for fill in rows:
            coin = str(fill.get("coin") or "")
            if coin not in CORE_COINS:
                continue
            wallet_market_fills[(wallet["address"], coin)].append(fill)

    incomplete_wallet_markets = {
        (wallet["address"], market)
        for wallet, stats in zip(selected, wallet_stats, strict=True)
        for market in stats["boundary_collision_markets"]
    }
    continuity_failures = 0
    ambiguous_timestamp_groups = 0
    unknown_fills = 0
    direction_label_mismatches = 0
    unknown_direction_labels = 0
    eligible_wallet_markets: set[tuple[str, str]] = set()
    calibration_exposure: dict[tuple[str, str], list[float]] = defaultdict(list)
    parsed: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for key, fills in wallet_market_fills.items():
        fills.sort(key=lambda fill: int(fill.get("time") or 0))
        previous_after: float | None = None
        market_continuous = (
            key[0] not in incomplete_addresses and key not in incomplete_wallet_markets
        )
        ordered_fills = []
        for _, group_iter in groupby(fills, key=lambda fill: int(fill.get("time") or 0)):
            group = list(group_iter)
            ordered_group = order_timestamp_group(group)
            if ordered_group is None:
                ambiguous_timestamp_groups += 1
                market_continuous = False
                ordered_group = group
            ordered_fills.extend(ordered_group)
        for fill in ordered_fills:
            try:
                timestamp = int(fill["time"])
                start_position = float(fill["startPosition"])
                size = float(fill["sz"])
                price = float(fill["px"])
                side = str(fill["side"])
                split = split_fill_inventory(start_position, side, size, price)
            except (KeyError, TypeError, ValueError):
                unknown_fills += 1
                market_continuous = False
                continue
            if previous_after is not None and abs(start_position - previous_after) > _position_tolerance(
                start_position, previous_after
            ):
                continuity_failures += 1
                market_continuous = False
            previous_after = split["position_after"]

            direction = str(fill.get("dir") or "")
            known_directions = {
                "Open Long", "Add Long", "Close Long",
                "Open Short", "Add Short", "Close Short",
                "Long > Short", "Short > Long",
            }
            if direction not in known_directions:
                unknown_direction_labels += 1
                market_continuous = False
            elif direction not in possible_direction_labels(start_position, side, size):
                direction_label_mismatches += 1
                market_continuous = False

            record = {
                "time": timestamp,
                "bucket": timestamp // BAR_MS * BAR_MS,
                "price": price,
                "start_position": start_position,
                **split,
                "liquidation": bool(fill.get("liquidation")),
                "direction": direction,
            }
            parsed[key].append(record)
            if timestamp < calibration_end_ms:
                exposure = max(abs(start_position), abs(split["position_after"])) * price
                if exposure > 0:
                    calibration_exposure[key].append(exposure)

        if market_continuous and parsed[key]:
            eligible_wallet_markets.add(key)

    cohort_by_market: dict[str, dict[str, set[str]]] = {}
    for coin in sorted(CORE_COINS):
        candidates = []
        for (address, market), values in calibration_exposure.items():
            if market == coin and len(values) >= 3 and (address, market) in eligible_wallet_markets:
                candidates.append((statistics.median(values), address))
        cohort_by_market[coin] = split_market_cohorts(candidates)

    calibration_candidates_by_market = {}
    for coin in sorted(CORE_COINS):
        calibration_candidates_by_market[coin] = sum(
            1 for (address, market), values in calibration_exposure.items()
            if market == coin and len(values) >= 3 and (address, market) in eligible_wallet_markets
        )

    buckets: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    market_counts: dict[str, dict[str, Any]] = {}
    for coin in CORE_COINS:
        cohorts = cohort_by_market[coin]
        market_counts[coin] = {
            "continuous_wallet_market_pairs": sum(1 for address, market in eligible_wallet_markets if market == coin),
            "calibrated_wallets": len(cohorts["small"]) + len(cohorts["large"]),
            "small_wallets": len(cohorts["small"]),
            "large_wallets": len(cohorts["large"]),
            "test_fills": 0,
            "test_buckets": 0,
        }
    for (address, coin), fills in parsed.items():
        cohorts = cohort_by_market[coin]
        cohort = "small" if address in cohorts["small"] else "large" if address in cohorts["large"] else None
        if cohort is None:
            continue
        for fill in fills:
            if fill["time"] < calibration_end_ms:
                continue
            bucket = buckets[(coin, fill["bucket"])]
            market_counts[coin]["test_fills"] += 1
            market_counts[coin]["test_buckets"] += 0
            bucket[f"{cohort}_wallet_{address}"] = 1.0
            bucket[f"{cohort}_fill_count"] += 1
            bucket[f"{cohort}_open_long_usd"] += fill["open_long_usd"]
            bucket[f"{cohort}_close_long_usd"] += fill["close_long_usd"]
            bucket[f"{cohort}_open_short_usd"] += fill["open_short_usd"]
            bucket[f"{cohort}_close_short_usd"] += fill["close_short_usd"]
            bucket[f"{cohort}_long_net_wallet_{address}"] += (
                fill["open_long_usd"] - fill["close_long_usd"]
            )
            bucket[f"{cohort}_short_net_wallet_{address}"] += (
                fill["open_short_usd"] - fill["close_short_usd"]
            )
            if fill["liquidation"]:
                bucket[f"{cohort}_liquidation_notional_usd"] += (
                    fill["open_long_usd"] + fill["close_long_usd"]
                    + fill["open_short_usd"] + fill["close_short_usd"]
                )

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "clt_inventory_15m.csv"
    fields = [
        "market", "bucket_ts_ms",
        "small_active_wallets", "large_active_wallets",
        "small_long_add_wallets", "small_short_add_wallets",
        "large_long_add_wallets", "large_short_add_wallets",
        "small_fill_count", "large_fill_count",
        "small_long_delta_usd", "small_short_delta_usd",
        "large_long_delta_usd", "large_short_delta_usd",
        "small_open_long_usd", "small_close_long_usd",
        "small_open_short_usd", "small_close_short_usd",
        "large_open_long_usd", "large_close_long_usd",
        "large_open_short_usd", "large_close_short_usd",
        "small_liquidation_notional_usd", "large_liquidation_notional_usd",
    ]
    output_rows = []
    for (coin, bucket_ms), values in sorted(buckets.items()):
        small_active = sum(1 for key in values if key.startswith("small_wallet_"))
        large_active = sum(1 for key in values if key.startswith("large_wallet_"))
        row = {
            "market": f"{coin}-PERP",
            "bucket_ts_ms": bucket_ms,
            "small_active_wallets": small_active,
            "large_active_wallets": large_active,
            "small_long_add_wallets": count_net_add_wallets(values, cohort="small", direction="long"),
            "small_short_add_wallets": count_net_add_wallets(values, cohort="small", direction="short"),
            "large_long_add_wallets": count_net_add_wallets(values, cohort="large", direction="long"),
            "large_short_add_wallets": count_net_add_wallets(values, cohort="large", direction="short"),
            "small_fill_count": int(values["small_fill_count"]),
            "large_fill_count": int(values["large_fill_count"]),
            "small_long_delta_usd": values["small_open_long_usd"] - values["small_close_long_usd"],
            "small_short_delta_usd": values["small_open_short_usd"] - values["small_close_short_usd"],
            "large_long_delta_usd": values["large_open_long_usd"] - values["large_close_long_usd"],
            "large_short_delta_usd": values["large_open_short_usd"] - values["large_close_short_usd"],
        }
        for field in fields[10:]:
            row[field] = values[field]
        output_rows.append(row)
        market_counts[coin]["test_buckets"] += 1

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    report = {
        "status": "DIRECTION_AWARE_INVENTORY_FEASIBILITY_PILOT",
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "calibration_days": CALIBRATION_DAYS,
        "cohort_definition": (
            "Within the selected leaderboard sample, each market's "
            "small cohort is the lower half and large cohort the upper quartile "
            "of median position notional measured only during the first 7 days. "
            "This is relative sizing, not a representative retail/whale census."
        ),
        "counts": {
            "wallets_selected": len(selected),
        "wallet_source": "hyperliquid_public_leaderboard" if selected and selected[0]["manifest_fills"] is None else "wallet_archive_manifest",
        "wallet_sampling": "deterministic monthly-volume deciles" if selected and selected[0]["manifest_fills"] is None else "lowest manifest fill counts",
            "wallets_with_core_market_fills": len({address for address, _ in wallet_market_fills}),
            "api_pages": total_pages,
            "api_fill_rows": total_api_fills,
            "twap_fill_rows": total_twap_fills,
            "continuous_wallet_market_pairs": len(eligible_wallet_markets),
            "position_continuity_failures": continuity_failures,
            "fills_with_missing_or_invalid_position_fields": unknown_fills,
            "direction_label_mismatches_vs_position_math": direction_label_mismatches,
            "unrecognized_direction_labels": unknown_direction_labels,
            "wallets_at_api_10000_fill_cap": sum(bool(row["api_cap_reached"]) for row in wallet_stats),
            "wallets_at_twap_2000_fill_cap": sum(bool(row["twap_cap_reached"]) for row in wallet_stats),
            "page_boundary_timestamp_collisions": sum(bool(row["page_boundary_collision"]) for row in wallet_stats),
            "ambiguous_same_timestamp_groups": ambiguous_timestamp_groups,
            "wallet_market_pairs_excluded_for_boundary_collision": len(incomplete_wallet_markets),
        },
        "by_market": {f"{coin}-PERP": values for coin, values in market_counts.items()},
        "cohort_calibration_candidates_by_market": {
            f"{coin}-PERP": count for coin, count in calibration_candidates_by_market.items()
        },
        "wallet_coverage": {
            "manifest_fill_count_min": min((row["manifest_fills"] for row in selected if row["manifest_fills"] is not None), default=None),
            "manifest_fill_count_max": max((row["manifest_fills"] for row in selected if row["manifest_fills"] is not None), default=None),
            "api_fill_count_min": min((row["returned_api_fills"] for row in wallet_stats), default=0),
            "api_fill_count_max": max((row["returned_api_fills"] for row in wallet_stats), default=0),
            "per_wallet_counts": wallet_stats,
        },
        "limitations": [
            "Only the selected 120-address public leaderboard sample is represented; counterparties outside it are not sampled.",
            "Leaderboard sampling is stratified by reported monthly volume and is not a random sample of all Hyperliquid traders.",
            "Addresses whose requested interval reaches the public API 10,000-fill cap are excluded from all markets.",
            "userFillsByTime exposes only the latest 10,000 fills per address; TWAP slice history is limited to 2,000 recent rows.",
            "A position continuity failure invalidates that wallet-market series; no missing inventory is imputed.",
            "Small/large labels are relative to this selected sample and are not proof of retail/whale identity.",
            "This is a data-feasibility pilot, not a CLT event study or strategy backtest.",
        ],
        "output_csv": str(csv_path),
    }
    (out_dir / "clt_inventory_feasibility.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/clt_inventory_feasibility"))
    parser.add_argument("--start", default="2026-10-03T00:00:00+00:00")
    parser.add_argument("--end", default="2026-11-09T00:00:00+00:00")
    parser.add_argument("--max-wallets", type=int, default=40)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start).astimezone(UTC)
    end = datetime.fromisoformat(args.end).astimezone(UTC)
    report = audit(
        args.manifest,
        args.out_dir,
        start=start,
        end=end,
        max_wallets=args.max_wallets,
    )
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
