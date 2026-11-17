#!/usr/bin/env python3
"""Research-only snapshot of public Hyperliquid wallet behaviour.

This collector is deliberately separate from the ASTRA execution path. It captures
a point-in-time cohort (including weak/losing accounts), raw fills, and transparent
coverage flags. It does not label wallets as institutions or generate trade signals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"
USER_AGENT = "ASTRA-BOT-public-wallet-research/1.0"
MAX_WALLETS = 100
MAX_FILLS_PER_WALLET = 10_000
PAGE_SIZE = 2_000
DEFAULT_PAUSE_SECONDS = 6.2


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("leaderboard response is not a JSON object")
    return payload


def _post_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    delays = (10.0, 30.0, 60.0)
    for attempt in range(len(delays) + 1):
        request = Request(
            INFO_URL,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not isinstance(result, list):
                raise ValueError(
                    f"Hyperliquid info response is not a list: {type(result).__name__}"
                )
            return result
        except HTTPError as exc:
            if exc.code != 429 or attempt == len(delays):
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                wait = max(delays[attempt], float(retry_after or 0))
            except ValueError:
                wait = delays[attempt]
            time.sleep(wait)
    raise RuntimeError("Hyperliquid request retry loop exhausted")


def _metric(row: dict[str, Any], window: str, key: str) -> float:
    for item in row.get("windowPerformances", []):
        if isinstance(item, list) and len(item) == 2 and item[0] == window:
            try:
                return float(item[1].get(key, 0))
            except (TypeError, ValueError, AttributeError):
                return 0.0
    return 0.0


def select_cohort(rows: list[dict[str, Any]], limit: int = MAX_WALLETS) -> list[dict[str, Any]]:
    """Stratify a current leaderboard snapshot; intentionally retain losers."""
    valid = [
        row for row in rows
        if isinstance(row, dict) and isinstance(row.get("ethAddress"), str)
        and row["ethAddress"].startswith("0x") and len(row["ethAddress"]) == 42
    ]
    selectors = (
        ("high_month_pnl", lambda r: _metric(r, "month", "pnl"), True),
        ("high_week_pnl", lambda r: _metric(r, "week", "pnl"), True),
        ("high_month_volume", lambda r: _metric(r, "month", "vlm"), True),
        ("high_account_value", lambda r: float(r.get("accountValue") or 0), True),
        ("low_month_roi", lambda r: _metric(r, "month", "roi"), False),
    )
    picked: dict[str, dict[str, Any]] = {}
    per_stratum = max(1, limit // len(selectors))
    for label, score, reverse in selectors:
        for row in sorted(valid, key=score, reverse=reverse)[:per_stratum]:
            address = row["ethAddress"].lower()
            if address not in picked:
                item = dict(row)
                item["cohort_strata"] = []
                picked[address] = item
            picked[address]["cohort_strata"].append(label)
    # Fill remaining slots by alternating the leaderboard's order, not ROI alone.
    for row in valid:
        if len(picked) >= limit:
            break
        address = row["ethAddress"].lower()
        if address not in picked:
            item = dict(row)
            item["cohort_strata"] = ["leaderboard_residual"]
            picked[address] = item
    return list(picked.values())[:limit]


def fetch_wallet_fills(
    address: str,
    now_ms: int,
    pause_s: float = DEFAULT_PAUSE_SECONDS,
    window_hours: float = 96,
) -> dict[str, Any]:
    """Fetch a common recent window, including normal and TWAP slice fills."""
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")
    start_ms = max(0, now_ms - int(window_hours * 60 * 60 * 1000))
    errors: list[str] = []
    rate_limit_weight = 0

    def collect(endpoint_type: str, source: str, is_twap: bool = False) -> dict[str, Any]:
        nonlocal rate_limit_weight
        cursor = start_ms
        collected: dict[tuple[Any, ...], dict[str, Any]] = {}
        pages = 0
        while cursor <= now_ms and len(collected) < MAX_FILLS_PER_WALLET:
            try:
                batch = _post_json({
                    "type": endpoint_type,
                    "user": address,
                    "startTime": cursor,
                    "endTime": now_ms,
                    "aggregateByTime": False,
                })
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                errors.append(f"{endpoint_type}: {type(exc).__name__}: {exc}")
                break
            pages += 1
            rate_limit_weight += 20 + math.ceil(len(batch) / 20)
            normalized_batch = []
            for row in batch:
                fill = row.get("fill") if is_twap and isinstance(row.get("fill"), dict) else row
                if not isinstance(fill, dict):
                    continue
                normalized = dict(fill)
                if is_twap:
                    normalized["twapId"] = row.get("twapId") or fill.get("twapId")
                normalized["fill_source"] = source
                normalized_batch.append(normalized)
                key = (
                    normalized.get("coin"), normalized.get("time"), normalized.get("tid"),
                    normalized.get("oid"), normalized.get("side"), normalized.get("px"),
                    normalized.get("sz"), normalized.get("twapId"),
                )
                collected[key] = normalized
            times = [
                int(item["time"])
                for item in normalized_batch
                if item.get("time") is not None
            ]
            if batch and not times:
                errors.append(f"{endpoint_type}: response page had no fill timestamps")
                time.sleep(pause_s)
                break
            if times:
                next_cursor = max(times) + 1
                if next_cursor <= cursor:
                    errors.append(f"{endpoint_type}: pagination cursor did not advance")
                    time.sleep(pause_s)
                    break
                cursor = next_cursor
            time.sleep(pause_s)
            if not batch or len(batch) < PAGE_SIZE:
                break
        ordered = sorted(
            collected.values(),
            key=lambda item: int(item.get("time", 0)),
        )
        return {
            "fills": ordered[:MAX_FILLS_PER_WALLET],
            "fill_count": min(len(ordered), MAX_FILLS_PER_WALLET),
            "pages": pages,
            "at_api_cap": len(ordered) >= MAX_FILLS_PER_WALLET,
        }

    regular = collect("userFillsByTime", "user_fill")
    twap = collect("userTwapSliceFillsByTime", "twap_slice", is_twap=True)
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for fill in regular["fills"] + twap["fills"]:
        key = (
            fill.get("coin"), fill.get("time"), fill.get("tid"), fill.get("oid"),
            fill.get("side"), fill.get("px"), fill.get("sz"), fill.get("twapId"),
            fill.get("fill_source"),
        )
        merged[key] = fill
    ordered = sorted(merged.values(), key=lambda item: int(item.get("time", 0)))
    times = [int(item["time"]) for item in ordered if item.get("time") is not None]
    at_api_cap = regular["at_api_cap"] or twap["at_api_cap"]
    return {
        "address": address,
        "fills": ordered,
        "fill_count": len(ordered),
        "regular_fill_count": regular["fill_count"],
        "twap_slice_fill_count": twap["fill_count"],
        "first_fill_time_ms": min(times) if times else None,
        "last_fill_time_ms": max(times) if times else None,
        "requested_start_time_ms": start_ms,
        "requested_end_time_ms": now_ms,
        "window_hours": window_hours,
        "regular_pages": regular["pages"],
        "twap_pages": twap["pages"],
        "estimated_rate_limit_weight": rate_limit_weight,
        "at_api_cap": at_api_cap,
        "window_complete": not errors and not at_api_cap,
        "errors": errors,
        "coverage_note": (
            "At least one endpoint reached its 10,000-fill cap; this window is left-censored."
            if at_api_cap
            else "Both normal fills and TWAP slices were queried for the requested common time window; "
            "coverage is incomplete if any endpoint error is listed."
        ),
    }


def classify_fill(fill: dict[str, Any]) -> str:
    """Classify position effect; exchange 'Open Long' can also describe an add."""
    side = str(fill.get("side") or "").upper()
    try:
        start = float(fill.get("startPosition") or 0)
        size = float(fill.get("sz") or 0)
    except (TypeError, ValueError):
        start = size = 0.0
    if side in {"A", "B"} and size > 0:
        delta = size if side == "B" else -size
        end = start + delta
        epsilon = 1e-12
        if abs(start) <= epsilon and abs(end) > epsilon:
            return "open"
        if abs(end) <= epsilon:
            return "close"
        if start * end < 0:
            return "reverse"
        if abs(end) > abs(start):
            return "add"
        if abs(end) < abs(start):
            return "close"
    direction = str(fill.get("dir") or "").lower()
    if "open" in direction:
        return "open"
    if "close" in direction:
        return "close"
    return "unknown"


def market_class(coin: str) -> str:
    """Separate native crypto perps from builder-deployed/spot-like markets."""
    normalized = str(coin or "").strip()
    if not normalized or normalized.startswith("@") or ":" in normalized or "/" in normalized:
        return "non_native_crypto_perp"
    return "native_crypto_perp"


def summarize_fills(fills: list[dict[str, Any]]) -> dict[str, Any]:
    actions: dict[str, int] = defaultdict(int)
    coins: dict[str, int] = defaultdict(int)
    market_classes: dict[str, int] = defaultdict(int)
    crossed = 0
    fees = 0.0
    closed_pnl = 0.0
    fill_sources: dict[str, int] = defaultdict(int)
    for fill in fills:
        actions[classify_fill(fill)] += 1
        coin = str(fill.get("coin") or "UNKNOWN")
        coins[coin] += 1
        market_classes[market_class(coin)] += 1
        fill_sources[str(fill.get("fill_source") or "unspecified")] += 1
        crossed += int(bool(fill.get("crossed")))
        try:
            fees += float(fill.get("fee") or 0)
            closed_pnl += float(fill.get("closedPnl") or 0)
        except (TypeError, ValueError):
            continue
    return {
        "actions": dict(sorted(actions.items())),
        "fills_by_coin": dict(sorted(coins.items())),
        "fills_by_market_class": dict(sorted(market_classes.items())),
        "fills_by_source": dict(sorted(fill_sources.items())),
        "taker_fill_share": crossed / len(fills) if fills else None,
        "reported_fill_fees": fees,
        "reported_closed_pnl": closed_pnl,
        "pnl_scope_warning": "Funding, transfers, rebates outside fill fee fields, and account-level cash flows are not included.",
    }


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def run(
    out_dir: Path,
    limit: int = MAX_WALLETS,
    pause_s: float = DEFAULT_PAUSE_SECONDS,
    window_hours: float = 96,
) -> dict[str, Any]:
    generated = datetime.now(UTC)
    now_ms = int(generated.timestamp() * 1000)
    leaderboard = _get_json(LEADERBOARD_URL)
    rows = leaderboard.get("leaderboardRows")
    if not isinstance(rows, list):
        raise ValueError("leaderboardRows missing or not a list")
    cohort = select_cohort(rows, limit)
    snapshot = {
        "generated_at_utc": generated.isoformat(),
        "source": LEADERBOARD_URL,
        "leaderboard_row_count": len(rows),
        "cohort_selection": "stratified current snapshot: month/week PnL, month volume, account value, low month ROI",
        "survivorship_warning": "This is a prospective cohort snapshot. It is not a historical point-in-time leaderboard and cannot remove past survivorship bias.",
        "wallets": cohort,
    }
    _write_json(out_dir / "leaderboard_snapshot.json", snapshot)
    results = []
    for index, wallet in enumerate(cohort, start=1):
        address = wallet["ethAddress"].lower()
        result = fetch_wallet_fills(address, now_ms, pause_s, window_hours)
        raw_hash = _write_json(out_dir / "wallet_fills" / f"{address}.json", result)
        result_summary = {
            key: result[key]
            for key in (
                "address", "fill_count", "regular_fill_count", "twap_slice_fill_count",
                "first_fill_time_ms", "last_fill_time_ms", "requested_start_time_ms",
                "requested_end_time_ms", "window_hours", "regular_pages", "twap_pages",
                "estimated_rate_limit_weight", "at_api_cap", "window_complete", "errors",
                "coverage_note",
            )
        }
        result_summary["raw_sha256"] = raw_hash
        result_summary["behaviour"] = summarize_fills(result["fills"])
        result_summary["cohort_strata"] = wallet.get("cohort_strata", [])
        results.append(result_summary)
        print(f"[{index}/{len(cohort)}] {address}: fills={result['fill_count']} cap={result['at_api_cap']}")
    manifest = {
        "schema_version": 1,
        "generated_at_utc": generated.isoformat(),
        "exchange": "Hyperliquid",
        "market": "public perpetual wallet fills",
        "leaderboard_url": LEADERBOARD_URL,
        "info_api_url": INFO_URL,
        "wallet_count": len(cohort),
        "fill_window_hours": window_hours,
        "wallets_with_fills": sum(item["fill_count"] > 0 for item in results),
        "wallets_at_10k_fill_cap": sum(item["at_api_cap"] for item in results),
        "wallets_with_incomplete_window": sum(not item["window_complete"] for item in results),
        "total_fills": sum(item["fill_count"] for item in results),
        "wallet_results": results,
        "limitations": [
            "Wallet addresses are pseudonymous accounts, not verified funds or legal institutions.",
            "Leaderboard selection is a current snapshot and is not survivorship-free historical sampling.",
            "The Info API is queried over a common recent 96h window; each normal/TWAP endpoint is capped at 10,000 fills per wallet.",
            "The documented REST IP limit is 1,200 weighted units/minute; a 2,000-fill page costs about 120 units, so requests are paced at 6.2 seconds.",
            "A 429 is retried with exponential backoff; any remaining 429 is preserved as an explicit collection error.",
            "Builder-deployed markets and spot-like coin identifiers are separated from native crypto perpetual fills in summaries.",
            "Normal fills and TWAP slice fills are collected separately through userFillsByTime and userTwapSliceFillsByTime.",
            "Funding, deposits, withdrawals and transfers are not included in fill-level PnL summaries.",
            "Hyperliquid behaviour is a hypothesis source only; final validation must use BingX-native data.",
        ],
    }
    manifest["leaderboard_snapshot_sha256"] = hashlib.sha256(
        (out_dir / "leaderboard_snapshot.json").read_bytes()
    ).hexdigest()
    _write_json(out_dir / "wallet_panel_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/public_wallet_panel"))
    parser.add_argument("--limit", type=int, default=MAX_WALLETS)
    parser.add_argument("--pause-seconds", type=float, default=DEFAULT_PAUSE_SECONDS)
    parser.add_argument("--window-hours", type=float, default=96)
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_WALLETS:
        raise SystemExit(f"--limit must be between 1 and {MAX_WALLETS}")
    manifest = run(args.out_dir, args.limit, args.pause_seconds, args.window_hours)
    print(json.dumps({
        "wallet_count": manifest["wallet_count"],
        "wallets_with_fills": manifest["wallets_with_fills"],
        "wallets_at_10k_fill_cap": manifest["wallets_at_10k_fill_cap"],
        "total_fills": manifest["total_fills"],
    }, indent=2))


if __name__ == "__main__":
    main()
