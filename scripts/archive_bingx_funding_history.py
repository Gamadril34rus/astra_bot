#!/usr/bin/env python3
"""Download timestamped public BingX USDT-M perpetual funding rates for research."""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import statistics
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from astra_bot.adapters.bingx.client import (
    BINGX_API_BASE,
    BINGX_ENDPOINTS,
    BingXClient,
)
from astra_bot.utils.retry import is_exchange_endpoint_cooldown_error

LOGGER = logging.getLogger("bingx_funding_archive")
OUT_DIR = Path("data/bingx_usdm")
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
CHUNK_DAYS = 30
FIELDS = ("funding_time", "rate")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows_from_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or []
    if isinstance(data, dict):
        data = data.get("fundingRates", data.get("data", [])) or []
    if not isinstance(data, list):
        raise ValueError("BingX funding endpoint returned an unsupported data shape")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ts = item.get("fundingTime", item.get("time"))
        rate = item.get("fundingRate")
        if ts is None or rate is None:
            raise ValueError(f"funding record missing time/rate: {item!r}")
        rows.append({"funding_time": int(ts), "rate": str(rate)})
    return rows


async def _call_with_cooldown(
    operation: Callable[[], Awaitable[Any]], *, label: str
) -> Any:
    """Retry only BingX's HTTP-200 100410 cooldown response after its wait."""
    delays = (5.0, 30.0, 60.0)
    for attempt in range(4):
        try:
            return await operation()
        except Exception as exc:
            if not is_exchange_endpoint_cooldown_error(exc) or attempt == 3:
                raise
            delay = delays[attempt]
            LOGGER.warning(
                "%s returned BingX 100410; retry %d/4 in %.0fs",
                label, attempt + 2, delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError(f"{label}: BingX cooldown retries exhausted")

async def download_symbol(
    client: BingXClient,
    symbol: str,
    start_ms: int,
    end_ms: int,
    chunk_days: int = CHUNK_DAYS,
) -> dict[str, Any]:
    """Download funding in bounded windows, retrying empty windows by UTC day.

    Some successful BingX responses have returned an empty data array for a
    seven-day range while narrower requests for dates inside that same range
    contain the expected settlements. Empty parent windows are therefore
    re-queried one UTC day at a time; unresolved gaps remain visible and are
    never filled or interpolated.
    """
    rows: dict[int, str] = {}
    chunk_reports: list[dict[str, Any]] = []
    cursor = start_ms

    async def fetch_window(window_start: int, window_end: int) -> list[dict[str, Any]]:
        response = await _call_with_cooldown(
            lambda: client._request(
                "GET",
                BINGX_ENDPOINTS["swap"]["funding_rate"],
                params={
                    "symbol": symbol,
                    "startTime": window_start,
                    "endTime": window_end,
                    "limit": 1000,
                },
            ),
            label=f"BingX funding {symbol} {window_start}",
        )
        return _rows_from_response(response)

    def accept_window(
        window_rows: list[dict[str, Any]], window_start: int, window_end: int
    ) -> int:
        accepted = 0
        for row in window_rows:
            timestamp = row["funding_time"]
            if window_start <= timestamp <= window_end:
                previous = rows.get(timestamp)
                if previous is not None and previous != row["rate"]:
                    raise ValueError(
                        f"conflicting funding rate for {symbol} at {timestamp}: "
                        f"{previous} != {row['rate']}"
                    )
                rows[timestamp] = row["rate"]
                accepted += 1
        return accepted

    while cursor <= end_ms:
        chunk_end = min(
            end_ms,
            cursor + int(timedelta(days=chunk_days).total_seconds() * 1000) - 1,
        )
        chunk_rows = await fetch_window(cursor, chunk_end)
        accepted_rows = accept_window(chunk_rows, cursor, chunk_end)
        fallback_subchunks: list[dict[str, Any]] = []

        # A genuinely empty 7-day response is not assumed to prove that the
        # whole week had no settlements. Probe each day independently once.
        if not chunk_rows and chunk_end - cursor + 1 > 86_400_000:
            day_cursor = cursor
            while day_cursor <= chunk_end:
                day_end = min(chunk_end, day_cursor + 86_400_000 - 1)
                day_rows = await fetch_window(day_cursor, day_end)
                day_accepted = accept_window(day_rows, day_cursor, day_end)
                accepted_rows += day_accepted
                fallback_subchunks.append({
                    "start_time": day_cursor,
                    "end_time": day_end,
                    "response_rows": len(day_rows),
                    "accepted_rows": day_accepted,
                })
                day_cursor = day_end + 1

        chunk_reports.append({
            "start_time": cursor,
            "end_time": chunk_end,
            "response_rows": len(chunk_rows),
            "accepted_rows": accepted_rows,
            "fallback_used": bool(fallback_subchunks),
            "fallback_subchunks": fallback_subchunks,
        })
        cursor = chunk_end + 1

    ordered = sorted(rows.items())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"funding_{symbol.replace('-', '')}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(
            {"funding_time": ts, "rate": rate} for ts, rate in ordered
        )

    gaps = [
        (right_ts - left_ts) / 3_600_000
        for (left_ts, _), (right_ts, _) in pairwise(ordered)
    ]
    median_gap_hours = statistics.median(gaps) if gaps else None
    max_gap_hours = max(gaps) if gaps else None
    first_edge_hours = (ordered[0][0] - start_ms) / 3_600_000 if ordered else None
    last_edge_hours = (end_ms - ordered[-1][0]) / 3_600_000 if ordered else None
    gap_limit_hours = max(1.5 * median_gap_hours, median_gap_hours + 1.0) if median_gap_hours else None
    coverage_ok = bool(ordered) and bool(gap_limit_hours) and (
        max_gap_hours <= gap_limit_hours
        and first_edge_hours <= gap_limit_hours
        and last_edge_hours <= gap_limit_hours
    )
    return {
        "symbol": symbol,
        "file": str(path),
        "events": len(ordered),
        "first_funding_time": ordered[0][0] if ordered else None,
        "last_funding_time": ordered[-1][0] if ordered else None,
        "max_gap_hours": max_gap_hours,
        "median_gap_hours": median_gap_hours,
        "first_edge_hours": first_edge_hours,
        "last_edge_hours": last_edge_hours,
        "strict_coverage_ok": coverage_ok,
        "chunk_days": chunk_days,
        "chunks": chunk_reports,
        "empty_chunk_fallback_count": sum(
            1 for report in chunk_reports if report["fallback_used"]
        ),
        "sha256": _sha256_file(path),
        "source": "BingX /openApi/swap/v2/quote/fundingRate",
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    start = datetime.combine(date.fromisoformat(args.start_date), time.min, tzinfo=UTC)
    start_ms = int(start.timestamp() * 1000)
    if args.end_date:
        end = datetime.combine(date.fromisoformat(args.end_date), time.min, tzinfo=UTC)
        end_ms = int(end.timestamp() * 1000) - 1
    else:
        end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    client = BingXClient(
        {
            "base_url": BINGX_API_BASE,
            "rate_limit_qps": 0.8,
            "contract_type": "linear",
        }
    )
    await client.initialize()
    try:
        reports = []
        for symbol in args.symbols:
            LOGGER.info("downloading funding history for %s", symbol)
            reports.append(await download_symbol(client, symbol, start_ms, end_ms, args.chunk_days))
    finally:
        await client.close()
    manifest = {
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "requested_start_utc": start.isoformat(),
        "downloaded_through_utc": datetime.fromtimestamp(
            end_ms / 1000, tz=UTC
        ).isoformat(),
        "chunk_days": args.chunk_days,
        "strict_contiguous_requested": args.strict_contiguous,
        "reports": reports,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "funding_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.strict_contiguous:
        failed = [row["symbol"] for row in reports if not row["strict_coverage_ok"]]
        if failed:
            raise RuntimeError(f"funding history has uncovered gaps for: {failed}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2021-01-01")
    parser.add_argument("--end-date", default=None, help="exclusive UTC date boundary")
    parser.add_argument("--chunk-days", type=int, default=CHUNK_DAYS)
    parser.add_argument("--strict-contiguous", action="store_true")
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    args = parser.parse_args()
    args.symbols = tuple(
        item.strip().upper() for item in args.symbols.split(",") if item.strip()
    )
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
