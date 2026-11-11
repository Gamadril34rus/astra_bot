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

from astra_bot.adapters.bingx.client import BINGX_API_BASE, BINGX_ENDPOINTS, BingXClient
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
)
PRICE_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")


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
    return all(_decimal(left[key]) == _decimal(right[key]) for key in PRICE_FIELDS)


def _merge_rows(
    existing: dict[int, dict[str, str]],
    incoming: dict[int, dict[str, str]],
) -> dict[int, dict[str, str]]:
    """Merge by candle open time; never silently overwrite a historical bar."""
    merged = dict(existing)
    for open_time, row in incoming.items():
        previous = merged.get(open_time)
        if previous is not None:
            if not _same_bar(previous, row):
                raise ValueError(
                    f"immutable candle conflict at open_time={open_time}: "
                    f"existing={previous!r} incoming={row!r}"
                )
            continue
        merged[open_time] = row
    return dict(sorted(merged.items()))


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


async def _call_with_cooldown(
    operation: Callable[[], Awaitable[Any]], *, label: str
) -> Any:
    """Retry BingX HTTP-200 code 100410 after endpoint cooldown windows."""
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

async def _contract_launch_times(
    client: BingXClient, symbols: tuple[str, ...]
) -> dict[str, int]:
    response = await _call_with_cooldown(
        lambda: client._request(
            "GET", BINGX_ENDPOINTS["swap"]["instruments"], params={}
        ),
        label="BingX contract metadata",
    )
    data = response.get("data") or []
    if isinstance(data, dict):
        for key in ("contracts", "symbols", "list", "data"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
        else:
            data = []
    wanted = set(symbols)
    found: dict[str, int] = {}
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if symbol not in wanted:
            continue
        if str(item.get("currency") or "").upper() != "USDT":
            raise RuntimeError(f"{symbol}: contract is not USDT-settled")
        if str(item.get("status") or "") != "1":
            raise RuntimeError(f"{symbol}: BingX contract is not active")
        if str(item.get("apiStateOpen", "true")).lower() != "true":
            raise RuntimeError(f"{symbol}: BingX contract does not allow opening")
        launch_ms = int(item.get("launchTime") or 0)
        if launch_ms <= 0:
            raise RuntimeError(f"{symbol}: contract metadata has no launchTime")
        found[symbol] = launch_ms
    missing = sorted(wanted - set(found))
    if missing:
        raise RuntimeError(f"missing active BingX USDT-M contracts: {missing}")
    return found


def _latest_contiguous_funding_start_ms(symbol: str, default_start_ms: int) -> int:
    """Return the first event in the latest uninterrupted funding segment.

    Candle history before this timestamp is unusable by the portfolio replay,
    because missing funding is never imputed. Fetch it only when the funding
    archive already exists; the workflow intentionally downloads funding first.
    """
    path = ARCHIVE_DIR / f"funding_{symbol.replace('-', '')}.csv"
    if not path.exists():
        return default_start_ms
    timestamps: list[int] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"funding_time", "rate"}.issubset(reader.fieldnames):
            raise ValueError(f"invalid funding archive schema for {symbol}: {path}")
        for row in reader:
            timestamp = int(row["funding_time"])
            rate = _decimal(row["rate"])
            if not rate.is_finite():
                raise ValueError(f"non-finite funding rate for {symbol} at {timestamp}")
            timestamps.append(timestamp)
    timestamps = sorted(set(timestamps))
    if not timestamps:
        raise RuntimeError(f"empty BingX funding archive for {symbol}: {path}")
    gap_indices = [
        index for index, (left, right) in enumerate(pairwise(timestamps))
        if right - left > 12 * 60 * 60 * 1000
    ]
    latest_start = timestamps[gap_indices[-1] + 1] if gap_indices else timestamps[0]
    return max(default_start_ms, latest_start)

async def _download_pair(
    client: BingXClient,
    *,
    symbol: str,
    timeframe: str,
    mode: str,
    start_ms: int,
    now_ms: int,
    launch_ms: int = 0,
    funding_start_ms: int | None = None,
) -> tuple[dict[str, Any], int]:
    interval_ms = TIMEFRAME_MS[timeframe]
    path = _archive_path(symbol, timeframe)
    existing = _read_rows(path)
    if mode == "daily" and not existing:
        raise RuntimeError(
            f"missing initial archive for {symbol} {timeframe}; "
            "run workflow_dispatch with mode=initial first"
        )
    if mode == "initial":
        # Re-fetch from the required floor even when a prior initial run left
        # a partial file; min(existing) would permanently skip its missing prefix.
        floor_ms = max(start_ms, launch_ms, funding_start_ms or 0)
    else:
        overlap_floor = max(existing) - 3 * interval_ms
        floor_ms = max(min(existing), overlap_floor, launch_ms)

    # BingX's endTime-only pagination repeated the same oldest page in CI.
    # Use bounded startTime/endTime windows so every request has an explicit
    # non-overlapping range and the expected candle count can be verified.
    cursor_start = ((floor_ms + interval_ms - 1) // interval_ms) * interval_ms
    if mode == "initial":
        # Keep only funding-covered rows. If a prior run left a contiguous
        # prefix from the requested floor, resume with a three-bar overlap;
        # if the prefix is missing, restart at the floor to backfill it.
        existing = {ts: row for ts, row in existing.items() if ts >= cursor_start}
        if existing:
            prior_coverage = _coverage(existing, interval_ms)
            if prior_coverage["gap_count"]:
                raise RuntimeError(
                    f"partial BingX archive is internally gapped for {symbol} {timeframe}; "
                    f"sample={prior_coverage['gap_samples'][:3]}"
                )
            if min(existing) == cursor_start:
                cursor_start = max(cursor_start, max(existing) - 3 * interval_ms)
    last_closed_open = (now_ms // interval_ms) * interval_ms - interval_ms
    incoming: dict[int, dict[str, str]] = {}
    pages = 0
    while cursor_start <= last_closed_open:
        page_last_open = min(
            cursor_start + (PAGE_SIZE - 1) * interval_ms, last_closed_open
        )
        page = await _call_with_cooldown(
            lambda cursor_start=cursor_start, page_last_open=page_last_open: client.fetch_kline_page(
                symbol,
                timeframe,
                end_time_ms=page_last_open + interval_ms - 1,
                limit=PAGE_SIZE,
                start_time_ms=cursor_start,
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
        if not actual_times and not incoming and (
            not existing or cursor_start < min(existing)
        ):
            # BingX may retain less history than contract launchTime. Skip only
            # leading empty windows, including windows before a partial file's
            # first observed candle; once data starts, later gaps are fatal.
            cursor_start = page_last_open + interval_ms
            continue
        expected_start = (
            cursor_start
            if incoming or (existing and cursor_start >= min(existing))
            else actual_times[0]
        )
        expected_times = list(range(expected_start, page_last_open + 1, interval_ms))
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
        # Persist each verified page so a later API cooldown does not discard
        # the work completed earlier in this symbol/timeframe archive.
        if incoming:
            _write_rows(path, _merge_rows(existing, incoming))
        cursor_start = page_last_open + interval_ms

    if not existing and not incoming:
        raise RuntimeError(f"BingX returned no closed candles for {symbol} {timeframe}")
    merged = _merge_rows(existing, incoming)
    if merged:
        _write_rows(path, merged)
    coverage = _coverage(merged, interval_ms)
    if coverage["gap_count"]:
        raise RuntimeError(
            f"BingX archive contains {coverage['gap_count']} candle gaps for "
            f"{symbol} {timeframe}; sample={coverage['gap_samples'][:3]}"
        )
    coverage.update(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "BingX USDT-M perpetual swap public klines",
            "mode": mode,
            "contract_launch_time": launch_ms,
            "funding_segment_start_time": funding_start_ms,
            "effective_start_time": min(merged) if merged else None,
            "new_bars": len(set(merged) - set(existing)),
            "verified_overlap_bars": len(set(incoming) & set(existing)),
            "pages_requested": pages,
            "sha256": _sha256_file(path),
        }
    )
    return coverage, len(incoming)

async def run(args: argparse.Namespace) -> dict[str, Any]:
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
        launch_times = await _contract_launch_times(client, args.symbols)
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
                    launch_ms=launch_times[symbol],
                    funding_start_ms=_latest_contiguous_funding_start_ms(symbol, start_ms),
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
