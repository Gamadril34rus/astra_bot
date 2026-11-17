#!/usr/bin/env python3
"""Append-only historical OHLCV archive for BingX USDT-M perpetual contracts."""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any

from astra_bot.adapters.bingx.client import BINGX_API_BASE, BingXClient
from astra_bot.utils.retry import is_exchange_endpoint_cooldown_error

LOGGER = logging.getLogger("bingx_candle_archive")
ARCHIVE_DIR = Path("data/bingx_usdm")
MANIFEST_PATH = ARCHIVE_DIR / "manifest.json"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
DEFAULT_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")
TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}
PAGE_SIZE = 500
COOLDOWN_DELAYS = (300.0, 300.0, 300.0)
CSV_FIELDS = (
    "open_time",
    "open_time_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
)
PRICE_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")
OPTIONAL_VOLUME_FIELDS = ("taker_buy_volume", "taker_buy_quote_volume")


async def _call_with_cooldown(
    operation: Callable[[], Awaitable[Any]], *, label: str
) -> Any:
    """Retry BingX HTTP-200 code 100410 only after the endpoint cooldown."""
    for attempt, delay in enumerate((*COOLDOWN_DELAYS, None), start=1):
        try:
            return await operation()
        except Exception as exc:
            if (
                delay is None
                or not is_exchange_endpoint_cooldown_error(exc)
            ):
                raise
            LOGGER.warning(
                "%s returned BingX 100410; retry %d/4 in %.0fs",
                label,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError(f"{label}: BingX cooldown retries exhausted")


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def _row_from_candle(candle: Any) -> dict[str, str]:
    return {
        "open_time": str(int(candle.open_time)),
        "open_time_utc": _iso_ms(int(candle.open_time)),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
        "quote_volume": str(candle.quote_volume),
        "trades_count": str(int(candle.trades_count or 0)),
        "taker_buy_volume": (
            str(candle.taker_buy_volume)
            if getattr(candle, "taker_buy_volume", None) is not None else ""
        ),
        "taker_buy_quote_volume": (
            str(candle.taker_buy_quote_volume)
            if getattr(candle, "taker_buy_quote_volume", None) is not None else ""
        ),
    }


def _read_rows(path: Path) -> dict[int, dict[str, str]]:
    if not path.exists():
        return {}
    rows: dict[int, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            open_time = int(row["open_time"])
            if open_time in rows:
                raise ValueError(f"duplicate open_time in archive {path}: {open_time}")
            rows[open_time] = {key: str(row.get(key, "")) for key in CSV_FIELDS}
    return rows


def _same_bar(left: dict[str, str], right: dict[str, str]) -> bool:
    if int(left["open_time"]) != int(right["open_time"]):
        return False
    if not all(_decimal(left[key]) == _decimal(right[key]) for key in PRICE_FIELDS):
        return False
    # Older archives predate taker-buy fields. Blank optional values mean
    # unknown, so a verified re-fetch may enrich them without changing OHLCV.
    return all(
        not left.get(key) or not right.get(key) or _decimal(left[key]) == _decimal(right[key])
        for key in OPTIONAL_VOLUME_FIELDS
    )


def _merge_rows(
    existing: dict[int, dict[str, str]],
    incoming: dict[int, dict[str, str]],
    *,
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[int, dict[str, str]]:
    """Merge by candle open time; conflicting history is preserved and surfaced.

    Without a conflict sink, retain the strict legacy behavior (raise). The
    archive runner supplies a sink so one revised historical bar cannot abort
    collection for every remaining symbol/timeframe.
    """
    merged = dict(existing)
    for open_time, row in incoming.items():
        previous = merged.get(open_time)
        if previous is not None:
            if not _same_bar(previous, row):
                conflict = {
                    "open_time": open_time,
                    "existing": dict(previous),
                    "incoming": dict(row),
                }
                if conflicts is None:
                    raise ValueError(
                        f"immutable candle conflict at open_time={open_time}: "
                        f"existing={previous!r} incoming={row!r}"
                    )
                conflicts.append(conflict)
                continue
            enriched = dict(previous)
            for field in OPTIONAL_VOLUME_FIELDS:
                if not enriched.get(field) and row.get(field):
                    enriched[field] = row[field]
            merged[open_time] = enriched
            continue
        merged[open_time] = row
    return dict(sorted(merged.items()))


def _append_conflicts(
    path: Path, conflicts: list[dict[str, Any]]
) -> int:
    """Append unique immutable-bar discrepancies to an audit JSONL sidecar."""
    if not conflicts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    known: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                known.add(str(item.get("conflict_id") or ""))
            except (json.JSONDecodeError, AttributeError):
                continue
    added = 0
    with path.open("a", encoding="utf-8") as handle:
        for conflict in conflicts:
            payload = {
                **conflict,
                "detected_at_utc": datetime.now(tz=UTC).isoformat(),
            }
            identity = json.dumps(
                [conflict["open_time"], conflict["existing"], conflict["incoming"]],
                sort_keys=True,
                separators=(",", ":"),
            )
            conflict_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if conflict_id in known:
                continue
            payload["conflict_id"] = conflict_id
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            known.add(conflict_id)
            added += 1
    return added


def _write_rows(path: Path, rows: dict[int, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows[key] for key in sorted(rows))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _coverage(rows: dict[int, dict[str, str]], interval_ms: int) -> dict[str, Any]:
    if not rows:
        return {
            "bar_count": 0,
            "first_open_time": None,
            "last_open_time": None,
            "gap_count": 0,
            "gap_samples": [],
        }
    times = sorted(rows)
    gaps = [
        {"after": left, "before": right, "missing_bars": (right - left) // interval_ms - 1}
        for left, right in pairwise(times)
        if right - left > interval_ms
    ]
    return {
        "bar_count": len(times),
        "first_open_time": times[0],
        "first_open_time_utc": _iso_ms(times[0]),
        "last_open_time": times[-1],
        "last_open_time_utc": _iso_ms(times[-1]),
        "gap_count": len(gaps),
        "gap_samples": gaps[:20],
    }


def _archive_path(symbol: str, timeframe: str) -> Path:
    return ARCHIVE_DIR / f"{symbol.replace('-', '')}_{timeframe}.csv"


async def _download_pair(
    client: BingXClient,
    *,
    symbol: str,
    timeframe: str,
    mode: str,
    start_ms: int,
    now_ms: int,
    refresh_optional_fields: bool = False,
) -> tuple[dict[str, Any], int]:
    """Fetch deterministic forward pages and preserve/enrich immutable candle rows."""
    interval_ms = TIMEFRAME_MS[timeframe]
    path = _archive_path(symbol, timeframe)
    existing = _read_rows(path)
    if mode == "daily" and not existing:
        raise RuntimeError(
            f"missing initial archive for {symbol} {timeframe}; "
            "run workflow_dispatch with mode=initial first"
        )

    if mode == "initial":
        floor_ms = start_ms
    elif refresh_optional_fields and existing:
        # Re-fetch the full archived window to backfill newly supported fields.
        floor_ms = min(existing)
    elif existing:
        overlap_floor = max(existing) - 3 * interval_ms
        floor_ms = max(min(existing), overlap_floor)
    else:
        floor_ms = start_ms

    cursor_start = ((floor_ms + interval_ms - 1) // interval_ms) * interval_ms
    last_closed_open = (now_ms // interval_ms) * interval_ms - interval_ms
    incoming: dict[int, dict[str, str]] = {}
    pages = 0

    while cursor_start <= last_closed_open:
        page_last_open = min(
            cursor_start + (PAGE_SIZE - 1) * interval_ms,
            last_closed_open,
        )
        page = await _call_with_cooldown(
            lambda cursor_start=cursor_start, page_last_open=page_last_open: (
                client.fetch_kline_page(
                    symbol,
                    timeframe,
                    start_time_ms=cursor_start,
                    end_time_ms=page_last_open + interval_ms - 1,
                    limit=PAGE_SIZE,
                )
            ),
            label=f"BingX {symbol} {timeframe} {cursor_start}",
        )
        pages += 1
        expected_times = list(range(cursor_start, page_last_open + 1, interval_ms))
        actual_times = sorted(
            int(candle.open_time)
            for candle in page
            if cursor_start <= int(candle.open_time) <= page_last_open
            and int(candle.open_time) + interval_ms <= now_ms
        )
        if actual_times != expected_times:
            missing = sorted(set(expected_times) - set(actual_times))
            duplicates = len(actual_times) != len(set(actual_times))
            raise RuntimeError(
                f"incomplete BingX candle range for {symbol} {timeframe}: "
                f"start={cursor_start} end={page_last_open} "
                f"expected={len(expected_times)} actual={len(actual_times)} "
                f"missing_sample={missing[:5]} duplicates={duplicates}"
            )

        for candle in page:
            open_time = int(candle.open_time)
            if open_time not in expected_times:
                continue
            row = _row_from_candle(candle)
            previous = incoming.get(open_time)
            if previous is not None and not _same_bar(previous, row):
                raise ValueError(
                    f"conflicting duplicate within BingX page at {symbol} "
                    f"{timeframe} open_time={open_time}"
                )
            incoming[open_time] = row

        cursor_start = page_last_open + interval_ms

    if not existing and not incoming:
        raise RuntimeError(f"BingX returned no closed candles for {symbol} {timeframe}")

    conflicts: list[dict[str, Any]] = []
    merged = _merge_rows(existing, incoming, conflicts=conflicts)
    if merged:
        _write_rows(path, merged)
    conflict_path = path.parent / "conflicts" / f"{symbol.replace('-', '')}_{timeframe}.jsonl"
    conflicts_recorded = _append_conflicts(conflict_path, conflicts)
    coverage = _coverage(merged, interval_ms)
    coverage.update(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "BingX USDT-M perpetual swap public klines",
            "mode": mode,
            "new_bars": len(set(merged) - set(existing)),
            "verified_overlap_bars": len(set(incoming) & set(existing)),
            "immutable_conflicts_detected": len(conflicts),
            "immutable_conflicts_newly_recorded": conflicts_recorded,
            "pages_requested": pages,
            "optional_fields_refresh": refresh_optional_fields,
        }
    )
    return coverage, len(incoming)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.mode == "daily" and not MANIFEST_PATH.is_file():
        raise RuntimeError(
            "missing initial archive manifest; "
            "run workflow_dispatch with mode=initial first"
        )

    start_date = date.fromisoformat(args.start_date)
    start_ms = int(datetime.combine(start_date, time.min, tzinfo=UTC).timestamp() * 1000)
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    client = BingXClient(
        {
            "base_url": BINGX_API_BASE,
            "rate_limit_qps": float(os.environ.get("BINGX_ARCHIVE_QPS", "0.2")),
            "contract_type": "linear",
        }
    )
    await client.initialize()
    results: dict[str, Any] = {}
    try:
        for symbol in args.symbols:
            for timeframe in args.timeframes:
                LOGGER.info("archive %s %s mode=%s", symbol, timeframe, args.mode)
                report, _ = await _download_pair(
                    client,
                    symbol=symbol,
                    timeframe=timeframe,
                    mode=args.mode,
                    start_ms=start_ms,
                    now_ms=now_ms,
                    refresh_optional_fields=args.refresh_optional_fields,
                )
                results[f"{symbol}:{timeframe}"] = report
    finally:
        await client.close()

    manifest = {
        "schema_version": 1,
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "source_endpoint": "/openApi/swap/v3/quote/klines",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "requested_history_start_utc": _iso_ms(start_ms),
        "downloaded_through_utc": _iso_ms(now_ms),
        "mode": args.mode,
        "symbols": list(args.symbols),
        "timeframes": list(args.timeframes),
        "interval_ms": TIMEFRAME_MS,
        "archives": results,
    }
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest




def _resolve_timeframes(
    requested: str | None,
    mode: str,
    manifest_path: Path = MANIFEST_PATH,
) -> tuple[str, ...]:
    if requested:
        return tuple(item.strip() for item in requested.split(",") if item.strip())
    if mode == "daily" and manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        return tuple(previous.get("timeframes") or DEFAULT_TIMEFRAMES)
    return DEFAULT_TIMEFRAMES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("initial", "daily"), default="daily")
    parser.add_argument("--start-date", default="2021-01-01")
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--timeframes")
    parser.add_argument(
        "--refresh-optional-fields",
        action="store_true",
        help="re-fetch every covered candle to enrich new optional fields such as taker-buy volume",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    args.timeframes = _resolve_timeframes(
        args.timeframes, args.mode, MANIFEST_PATH
    )
    unknown = sorted(set(args.timeframes) - set(TIMEFRAME_MS))
    if unknown:
        raise SystemExit(f"unsupported archive timeframes: {', '.join(unknown)}")
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    manifest = asyncio.run(run(args))
    LOGGER.info(
        "archive complete: pairs=%s bars=%s",
        len(manifest["archives"]),
        sum(item["bar_count"] for item in manifest["archives"].values()),
    )


if __name__ == "__main__":
    main()
