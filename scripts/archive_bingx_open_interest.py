#!/usr/bin/env python3
"""Append public BingX USDT-M open-interest snapshots for positioning research."""
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
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from astra_bot.adapters.bingx.client import BINGX_API_BASE, BINGX_ENDPOINTS, BingXClient
from astra_bot.utils.retry import is_exchange_endpoint_cooldown_error

LOGGER = logging.getLogger("bingx_open_interest_archive")
DEFAULT_DATA_DIR = Path("data/bingx_usdm/open_interest")
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
FIELDS = (
    "symbol",
    "source_time",
    "source_time_utc",
    "observed_at_ms",
    "observed_at_utc",
    "open_interest_base",
)
COOLDOWN_DELAYS = (300.0, 300.0, 300.0)


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid open-interest decimal: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"open interest must be finite and positive, got {value!r}")
    return result


def parse_open_interest(
    symbol: str,
    payload: dict[str, Any],
    observed_at_ms: int,
) -> dict[str, str]:
    """Validate one BingX open-interest snapshot; missing values are never zero-filled."""
    data = payload.get("data")
    if isinstance(data, list):
        if len(data) != 1:
            raise ValueError(f"{symbol}: expected one open-interest object, got {len(data)}")
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError(f"{symbol}: BingX open-interest response has invalid data shape")

    response_symbol = str(data.get("symbol") or symbol).upper()
    if response_symbol != symbol.upper():
        raise ValueError(
            f"{symbol}: BingX returned another symbol in open-interest response: "
            f"{response_symbol}"
        )

    source_time = data.get("time") or data.get("timestamp")
    if source_time is None:
        raise ValueError(f"{symbol}: open-interest response has no source timestamp")
    source_time_ms = int(source_time)
    if source_time_ms <= 0 or source_time_ms > observed_at_ms + 60_000:
        raise ValueError(
            f"{symbol}: implausible open-interest timestamp {source_time_ms} "
            f"for observation {observed_at_ms}"
        )

    open_interest = _decimal(data.get("openInterest"))
    return {
        "symbol": symbol,
        "source_time": str(source_time_ms),
        "source_time_utc": _iso_ms(source_time_ms),
        "observed_at_ms": str(observed_at_ms),
        "observed_at_utc": _iso_ms(observed_at_ms),
        "open_interest_base": str(open_interest),
    }


def _archive_path(data_dir: Path, symbol: str) -> Path:
    return data_dir / f"{symbol.replace('-', '')}_open_interest.csv"


def _read_rows(path: Path) -> dict[int, dict[str, str]]:
    if not path.exists():
        return {}
    rows: dict[int, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            observed_at_ms = int(row["observed_at_ms"])
            if observed_at_ms in rows:
                raise ValueError(
                    f"duplicate open-interest observation time in {path}: {observed_at_ms}"
                )
            rows[observed_at_ms] = {field: str(row.get(field, "")) for field in FIELDS}
    return rows


def _merge_rows(
    existing: dict[int, dict[str, str]],
    incoming: dict[int, dict[str, str]],
) -> dict[int, dict[str, str]]:
    """Merge immutable observations; duplicate local observation times must agree."""
    merged = dict(existing)
    for source_time, row in incoming.items():
        previous = merged.get(source_time)
        if previous is None:
            merged[source_time] = row
            continue
        if (
            previous["symbol"] != row["symbol"]
            or previous["source_time"] != row["source_time"]
            or Decimal(previous["open_interest_base"])
            != Decimal(row["open_interest_base"])
        ):
            raise ValueError(
                f"immutable open-interest conflict at {source_time}: "
                f"existing={previous!r} incoming={row!r}"
            )
    return dict(sorted(merged.items()))


def _write_rows(path: Path, rows: dict[int, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
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


async def _call_with_cooldown(
    operation: Callable[[], Awaitable[Any]], *, label: str
) -> Any:
    """Retry BingX HTTP-200 code 100410 only after a full endpoint cooldown."""
    for attempt in range(4):
        try:
            return await operation()
        except Exception as exc:
            if not is_exchange_endpoint_cooldown_error(exc) or attempt == 3:
                raise
            delay = COOLDOWN_DELAYS[attempt]
            LOGGER.warning(
                "%s returned BingX 100410; retry %d/4 in %.0fs",
                label,
                attempt + 2,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError(f"{label}: BingX cooldown retries exhausted")


async def capture_symbol(
    client: BingXClient,
    symbol: str,
    data_dir: Path,
) -> dict[str, Any]:
    payload = await _call_with_cooldown(
        lambda: client._request(
            "GET",
            BINGX_ENDPOINTS["swap"]["open_interest"],
            params={"symbol": symbol},
        ),
        label=f"BingX open interest {symbol}",
    )
    observed_at_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    row = parse_open_interest(symbol, payload, observed_at_ms)
    source_time = int(row["source_time"])
    path = _archive_path(data_dir, symbol)
    rows = _merge_rows(_read_rows(path), {observed_at_ms: row})
    _write_rows(path, rows)
    return {
        "symbol": symbol,
        "path": str(path),
        "source_time": source_time,
        "source_time_utc": row["source_time_utc"],
        "observed_at_ms": observed_at_ms,
        "open_interest_base": row["open_interest_base"],
        "snapshot_count": len(rows),
        "sha256": _sha256_file(path),
    }


async def run(symbols: tuple[str, ...], data_dir: Path) -> dict[str, Any]:
    client = BingXClient(
        {
            "base_url": BINGX_API_BASE,
            "rate_limit_qps": float(os.environ.get("BINGX_OPEN_INTEREST_QPS", "0.2")),
            "contract_type": "linear",
        }
    )
    await client.initialize()
    snapshots: dict[str, Any] = {}
    try:
        for index, symbol in enumerate(symbols):
            if index:
                await asyncio.sleep(5.0)
            LOGGER.info("capture public open interest: %s", symbol)
            snapshots[symbol] = await capture_symbol(client, symbol, data_dir)
    finally:
        await client.close()

    manifest = {
        "schema_version": 1,
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "source_endpoint": BINGX_ENDPOINTS["swap"]["open_interest"],
        "unit": "base asset quantity as returned by BingX",
        "generated_at_utc": datetime.now(tz=UTC).isoformat(),
        "symbols": list(symbols),
        "snapshots": snapshots,
        "warning": (
            "BingX endpoint is a current snapshot, not historical OHLC. "
            "This archive is prospective from the first collection time."
        ),
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    unknown = sorted(set(symbols) - set(SYMBOLS))
    if unknown:
        raise SystemExit(f"unsupported positioning symbols: {', '.join(unknown)}")
    if not symbols:
        raise SystemExit("at least one symbol is required")
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = asyncio.run(run(symbols, args.data_dir))
    LOGGER.info("open-interest snapshot complete: symbols=%s", len(result["snapshots"]))


if __name__ == "__main__":
    main()
