#!/usr/bin/env python3
"""Read-only probe of free BingX USDT-M historical trade and kline depth."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://open-api.bingx.com"
SYMBOL = "BTC-USDT"
PROBE_START = datetime(2025, 12, 26, 0, 0, tzinfo=UTC)
PROBE_END = PROBE_START + timedelta(hours=3) - timedelta(milliseconds=1)
PROBE_1M_DATES = (
    "2025-08-08",
    "2025-12-26",
    "2026-03-01",
    "2026-06-01",
    "2026-08-03",
    "2026-10-01",
    "2026-11-10",
)


def _iso_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def _request_json(path: str, params: dict[str, int | str]) -> dict:
    """Request public data and honour BingX's HTTP-200 code 100410 cooldown."""
    delays = (5.0, 30.0, 60.0)
    for attempt in range(len(delays) + 1):
        query = dict(params)
        if path.endswith("/historicalTrades"):
            query["timestamp"] = int(time.time() * 1000)
        url = f"{BASE_URL}{path}?{urlencode(query)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "ASTRA-BOT-research-history-probe/1.0",
                "X-SOURCE-KEY": "BX-AI-SKILL",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            if exc.code != 429 and payload.get("code") != 100410:
                raise
            if attempt == len(delays):
                raise RuntimeError(
                    f"BingX {path} rate limit exhausted: HTTP {exc.code} "
                    f"code={payload.get('code')} msg={payload.get('msg')}"
                ) from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                wait = max(delays[attempt], float(retry_after or 0))
            except ValueError:
                wait = delays[attempt]
            time.sleep(wait)
            continue

        if not isinstance(payload, dict):
            raise RuntimeError(f"BingX {path} returned a non-object response")
        if payload.get("code") == 100410:
            if attempt == len(delays):
                raise RuntimeError(
                    f"BingX {path} rate limit exhausted: "
                    f"code=100410 msg={payload.get('msg')}"
                )
            time.sleep(delays[attempt])
            continue
        if payload.get("code") != 0:
            raise RuntimeError(
                f"BingX {path} failed: code={payload.get('code')} msg={payload.get('msg')}"
            )
        return payload
    raise RuntimeError(f"BingX {path}: retry loop exhausted")


def _trade_summary(rows: list[dict], requested_from_id: int | None) -> dict:
    ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    times = [int(row["time"]) for row in rows if row.get("time") is not None]
    return {
        "requested_from_id": requested_from_id,
        "rows": len(rows),
        "min_id": min(ids) if ids else None,
        "max_id": max(ids) if ids else None,
        "min_time_utc": _iso_ms(min(times)) if times else None,
        "max_time_utc": _iso_ms(max(times)) if times else None,
        "estimated_100_row_pages_from_id_1": (
            math.ceil(max(ids) / 100) if ids else None
        ),
    }


def _kline_summary(rows: list, *, object_rows: bool = False) -> dict:
    if rows and isinstance(rows[0], dict):
        time_key = "openTime" if "openTime" in rows[0] else "time"
        times = [
            int(row[time_key]) for row in rows if row.get(time_key) is not None
        ]
    elif object_rows:
        times = [int(row["openTime"]) for row in rows if row.get("openTime") is not None]
    else:
        times = [int(row[0]) for row in rows if row]
    return {
        "rows": len(rows),
        "first_open_utc": _iso_ms(min(times)) if times else None,
        "last_open_utc": _iso_ms(max(times)) if times else None,
    }




def _minute_open_time(row: Any) -> int:
    """Read BingX swap kline timestamps from array, time or openTime rows."""
    if isinstance(row, dict):
        raw_time = row.get("time", row.get("openTime"))
    elif isinstance(row, (list, tuple)) and row:
        raw_time = row[0]
    else:
        raw_time = None
    if raw_time is None:
        raise ValueError(f"BingX 1m candle row has no supported timestamp: {row!r}")
    return int(raw_time)


def _minute_window_summary(rows: list, start_ms: int, end_ms: int) -> dict:
    observed_times = [_minute_open_time(row) for row in rows]
    times = sorted(set(observed_times))
    diffs = [right - left for left, right in itertools.pairwise(times)]
    missing_minutes = sum(max(0, delta // 60_000 - 1) for delta in diffs)
    non_minute_intervals = sum(delta % 60_000 != 0 for delta in diffs)
    expected = (end_ms - start_ms) // 60_000 + 1
    return {
        "requested_start_utc": _iso_ms(start_ms),
        "requested_end_utc": _iso_ms(end_ms),
        "expected_rows": expected,
        "rows": len(rows),
        "unique_timestamps": len(times),
        "first_open_utc": _iso_ms(times[0]) if times else None,
        "last_open_utc": _iso_ms(times[-1]) if times else None,
        "missing_minutes_between_returned_rows": missing_minutes,
        "non_minute_intervals": non_minute_intervals,
        "duplicate_timestamps": len(observed_times) - len(times),
        "full_contiguous_window": (
            len(times) == expected
            and times[0] == start_ms
            and times[-1] == end_ms - 59_999
            and all(delta == 60_000 for delta in diffs)
            and len(observed_times) == len(times)
        ),
    }


def _probe_historical_1m_windows(symbol: str) -> dict:
    """Sample actual 1m swap candles across dates before attempting bulk recovery."""
    result = {}
    for day in PROBE_1M_DATES:
        start = datetime.fromisoformat(f"{day}T00:00:00+00:00")
        start_ms = int(start.timestamp() * 1000)
        end_ms = start_ms + 499 * 60_000 + 59_999
        try:
            time.sleep(1.1)
            payload = _request_json(
                "/openApi/swap/v3/quote/klines",
                {
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 500,
                },
            )
        except RuntimeError as exc:
            result[day] = {
                "requested_start_utc": _iso_ms(start_ms),
                "requested_end_utc": _iso_ms(end_ms),
                "error": str(exc),
                "rows": 0,
                "full_contiguous_window": False,
            }
            continue
        result[day] = _minute_window_summary(payload.get("data") or [], start_ms, end_ms)
    return {
        "source": "BingX USDT-M perpetual swap /openApi/swap/v3/quote/klines",
        "interval": "1m; raw observations sampled only for possible exact 15m aggregation",
        "samples": result,
        "full_sample_windows": sum(
            bool(item["full_contiguous_window"]) for item in result.values()
        ),
        "sample_window_count": len(result),
    }

def run_probe(symbol: str = SYMBOL) -> dict:
    start_ms = int(PROBE_START.timestamp() * 1000)
    end_ms = int(PROBE_END.timestamp() * 1000)
    result: dict = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "symbol": symbol,
        "historical_window_tested": {
            "start_utc": PROBE_START.isoformat(),
            "end_utc": PROBE_END.isoformat(),
        },
        "historical_trades": {},
        "klines": {},
        "mark_price_klines": {},
        "limitations": [
            "This is a read-only depth probe, not a complete historical archive.",
            "A successful fromId response does not prove every intermediate trade ID is retrievable.",
            "Mark-price candles are not last-traded-price OHLCV and cannot replace swap klines.",
        ],
    }

    path = "/openApi/swap/v1/market/historicalTrades"
    latest_payload = _request_json(path, {"symbol": symbol, "limit": 100})
    latest_rows = latest_payload.get("data") or []
    result["historical_trades"]["latest"] = _trade_summary(latest_rows, None)
    if latest_rows:
        latest_min_id = min(int(row["id"]) for row in latest_rows)
        probes = {
            "from_id_1": 1,
            "from_id_latest_minus_1000": max(1, latest_min_id - 1000),
            "from_id_latest_minus_1000000": max(1, latest_min_id - 1_000_000),
        }
        for name, from_id in probes.items():
            time.sleep(1.1)
            payload = _request_json(
                path, {"symbol": symbol, "limit": 100, "fromId": from_id}
            )
            rows = payload.get("data") or []
            result["historical_trades"][name] = _trade_summary(rows, from_id)

    for interval in ("1m", "5m", "15m", "30m", "1h"):
        time.sleep(1.1)
        payload = _request_json(
            "/openApi/swap/v3/quote/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 500,
            },
        )
        result["klines"][interval] = _kline_summary(payload.get("data") or [])

    time.sleep(1.1)
    mark_payload = _request_json(
        "/openApi/swap/v1/market/markPriceKlines",
        {
            "symbol": symbol,
            "interval": "15m",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 500,
        },
    )
    result["mark_price_klines"]["15m"] = _kline_summary(
        mark_payload.get("data") or [], object_rows=True
    )
    result["historical_1m_depth_samples"] = _probe_historical_1m_windows(symbol)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument(
        "--out", type=Path, default=Path("reports/bingx_public_history_probe.json")
    )
    args = parser.parse_args()
    report = run_probe(args.symbol.strip().upper())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
