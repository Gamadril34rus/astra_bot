#!/usr/bin/env python3
"""Read-only BingX USDT-M kline-depth probe; no signals or trades are generated."""
from __future__ import annotations

import argparse
import itertools
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://open-api.bingx.com"
KLINE_PATH = "/openApi/swap/v3/quote/klines"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "INJ-USDT", "HYPE-USDT")
SAMPLE_DATES_1M = (
    "2025-11-01", "2025-11-13", "2025-11-14", "2025-12-01",
    "2025-12-26", "2026-03-01", "2026-06-01", "2026-08-03", "2026-11-10",
)
SAMPLE_DATES_NATIVE = ("2025-11-13", "2025-12-26", "2026-03-01")
INTERVALS = {"1m": 60_000, "5m": 300_000, "15m": 900_000}


def _row_time(row: Any) -> int:
    if isinstance(row, dict):
        value = row.get("time", row.get("openTime", row.get("open_time")))
    elif isinstance(row, (list, tuple)) and row:
        value = row[0]
    else:
        value = None
    if value is None:
        raise ValueError(f"kline row has no timestamp: {row!r}")
    return int(value)


def _field_value(row: Any, names: tuple[str, ...], index: int) -> Any:
    if isinstance(row, dict):
        for name in names:
            if row.get(name) is not None:
                return row[name]
        return None
    if isinstance(row, (list, tuple)) and len(row) > index:
        return row[index]
    return None


def summarize_window(rows: list[Any], start_ms: int, interval_ms: int, expected: int) -> dict[str, Any]:
    observed = [_row_time(row) for row in rows]
    times = sorted(set(observed))
    diffs = [right - left for left, right in itertools.pairwise(times)]
    missing = sum(max(0, delta // interval_ms - 1) for delta in diffs)
    quote = [_field_value(row, ("quoteVolume", "quote_volume"), 7) for row in rows]
    trades = [_field_value(row, ("count", "trades", "tradesCount", "n"), 8) for row in rows]
    taker_base = [_field_value(row, ("takerBuyVol", "takerBuyBaseVolume", "taker_buy_volume"), 9) for row in rows]
    taker_quote = [_field_value(row, ("takerBuyQuoteVol", "takerBuyQuoteVolume", "taker_buy_quote_volume"), 10) for row in rows]

    def nonzero_share(values: list[Any]) -> float | None:
        valid = [float(v) for v in values if v not in (None, "")]
        return round(sum(v != 0 for v in valid) / len(valid), 6) if valid else None

    first = times[0] if times else None
    last = times[-1] if times else None
    return {
        "expected_rows": expected,
        "rows": len(rows),
        "unique_timestamps": len(times),
        "first_open_utc": datetime.fromtimestamp(first / 1000, UTC).isoformat() if first is not None else None,
        "last_open_utc": datetime.fromtimestamp(last / 1000, UTC).isoformat() if last is not None else None,
        "missing_minutes_or_bars_between_rows": missing,
        "duplicate_timestamps": len(observed) - len(times),
        "full_contiguous_window": (
            len(times) == expected
            and first == start_ms
            and last == start_ms + (expected - 1) * interval_ms
            and all(delta == interval_ms for delta in diffs)
            and len(observed) == len(times)
        ),
        "row_shape": (
            {"kind": "object", "keys": sorted(rows[0].keys())}
            if rows and isinstance(rows[0], dict)
            else {"kind": "array", "length": len(rows[0])}
            if rows and isinstance(rows[0], (list, tuple))
            else None
        ),
        "nonzero_share": {
            "quote_volume": nonzero_share(quote),
            "trade_count": nonzero_share(trades),
            "taker_buy_base": nonzero_share(taker_base),
            "taker_buy_quote": nonzero_share(taker_quote),
        },
    }


class BingXProbe:
    def __init__(self, qps: float = 0.5) -> None:
        self.delay = 1.0 / qps
        self.last_request = 0.0

    def get(self, symbol: str, interval: str, day: str, limit: int = 500) -> dict[str, Any]:
        interval_ms = INTERVALS[interval]
        start = datetime.fromisoformat(day).replace(tzinfo=UTC)
        start_ms = int(start.timestamp() * 1000)
        expected = int(limit)
        end_ms = start_ms + expected * interval_ms - 1
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": expected,
        }
        delays = (5, 30, 60)
        for attempt in range(4):
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            query = urlencode(params)
            req = Request(
                f"{BASE_URL}{KLINE_PATH}?{query}",
                headers={"Accept": "application/json", "User-Agent": "ASTRA-BOT-BingX-depth-probe/1.0"},
            )
            self.last_request = time.monotonic()
            try:
                with urlopen(req, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                if exc.code != 429 and "100410" not in raw:
                    raise
                if attempt == 3:
                    raise RuntimeError(f"BingX rate limit exhausted for {symbol} {interval} {day}: {raw[:300]}") from exc
                time.sleep(delays[attempt])
                continue
            if not isinstance(payload, dict):
                raise RuntimeError("BingX returned a non-object response")
            if str(payload.get("code", "0")) == "100410":
                if attempt == 3:
                    raise RuntimeError(f"BingX 100410 retries exhausted: {payload.get('msg')}")
                time.sleep(delays[attempt])
                continue
            if str(payload.get("code", "0")) != "0":
                raise RuntimeError(f"BingX {symbol} {interval} {day}: code={payload.get('code')} msg={payload.get('msg')}")
            data = payload.get("data") or []
            if isinstance(data, dict):
                data = data.get("klines", data.get("data", [])) or []
            return summarize_window(data, start_ms, interval_ms, expected)
        raise RuntimeError("unreachable retry state")


def run_probe() -> dict[str, Any]:
    client = BingXProbe()
    results: dict[str, Any] = {}
    for symbol in SYMBOLS:
        results[symbol] = {}
        for day in SAMPLE_DATES_1M:
            try:
                results[symbol][f"1m:{day}"] = client.get(symbol, "1m", day)
            except Exception as exc:
                results[symbol][f"1m:{day}"] = {"error": str(exc), "full_contiguous_window": False}
        for interval in ("5m", "15m"):
            for day in SAMPLE_DATES_NATIVE:
                try:
                    results[symbol][f"{interval}:{day}"] = client.get(symbol, interval, day)
                except Exception as exc:
                    results[symbol][f"{interval}:{day}"] = {"error": str(exc), "full_contiguous_window": False}
        for day in ("2025-11-13", "2026-11-10"):
            try:
                results[symbol][f"1m1440:{day}"] = client.get(symbol, "1m", day, limit=1440)
            except Exception as exc:
                results[symbol][f"1m1440:{day}"] = {"error": str(exc), "full_contiguous_window": False}
    full_1m = {
        symbol: [day for day in SAMPLE_DATES_1M if results[symbol].get(f"1m:{day}", {}).get("full_contiguous_window")]
        for symbol in SYMBOLS
    }
    limit_probe = {
        symbol: {
            day: results[symbol].get(f"1m1440:{day}", {})
            for day in ("2025-11-13", "2026-11-10")
        }
        for symbol in SYMBOLS
    }
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "source_endpoint": KLINE_PATH,
        "purpose": "Historical data-depth and field-presence probe only; no 1m/5m signals or trades.",
        "aggregation_policy_if_depth_passes": "Use complete 1m bars only to construct 15m decision candles and closed 1h context; never evaluate signals on 1m/5m.",
        "sample_dates_1m": list(SAMPLE_DATES_1M),
        "sample_dates_native": list(SAMPLE_DATES_NATIVE),
        "full_1m_sample_dates_by_symbol": full_1m,
        "limit_1440_probe_by_symbol": limit_probe,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("reports/bingx_1m_depth_probe.json"))
    args = parser.parse_args()
    report = run_probe()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"full_1m_sample_dates_by_symbol": report["full_1m_sample_dates_by_symbol"], "limit_1440_probe_by_symbol": report["limit_1440_probe_by_symbol"]}, indent=2))


if __name__ == "__main__":
    main()
