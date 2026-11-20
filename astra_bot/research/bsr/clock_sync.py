"""Public BingX/Binance server-clock calibration for BSR research only."""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request


ENDPOINTS = {
    "bingx_swap": ("https://open-api.bingx.com/openApi/swap/v2/server/time", True),
    "binance_spot": ("https://data-api.binance.vision/api/v3/time", False),
}


def parse_server_time(payload: dict[str, Any], *, bingx: bool) -> int:
    """Extract a positive server epoch in milliseconds from a public time response."""
    if bingx:
        if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise ValueError(f"invalid BingX server-time response: {payload!r}")
        value = payload["data"].get("serverTime")
    else:
        value = payload.get("serverTime")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid serverTime: {value!r}")
    return value


def build_clock_sample(
    venue: str,
    server_time_ms: int,
    local_send_ms: int,
    local_receive_ms: int,
    rtt_ms: float,
) -> dict[str, Any]:
    """Estimate server-minus-local offset using the request wall-clock midpoint."""
    if venue not in ENDPOINTS:
        raise ValueError(f"unsupported venue: {venue}")
    if server_time_ms <= 0 or local_send_ms <= 0 or local_receive_ms < local_send_ms:
        raise ValueError("invalid clock sample timestamps")
    if rtt_ms < 0:
        raise ValueError("rtt_ms cannot be negative")
    midpoint_ms = (local_send_ms + local_receive_ms) / 2
    return {
        "kind": "clock_sync",
        "venue": venue,
        "server_time_ms": server_time_ms,
        "local_send_ms": local_send_ms,
        "local_receive_ms": local_receive_ms,
        "rtt_ms": round(rtt_ms, 3),
        "offset_ms": round(server_time_ms - midpoint_ms, 3),
    }


class PublicClockCalibrator:
    """Periodically record public server-time samples; no credentials or private APIs."""

    def __init__(
        self,
        output: Path,
        *,
        samples_per_cycle: int = 5,
        interval_seconds: float = 60.0,
        timeout_seconds: float = 5.0,
    ) -> None:
        if samples_per_cycle < 1 or interval_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("sample count, interval and timeout must be positive")
        self.output = output
        self.samples_per_cycle = samples_per_cycle
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds

    def _write(self, row: dict[str, Any]) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")

    def _request_sample(self, venue: str) -> dict[str, Any]:
        url, bingx = ENDPOINTS[venue]
        send_mono = time.monotonic_ns()
        local_send_ms = int(time.time() * 1000)
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "ASTRA-BSR-research/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{venue} server-time request failed: {exc}") from exc
        local_receive_ms = int(time.time() * 1000)
        rtt_ms = (time.monotonic_ns() - send_mono) / 1_000_000
        if not isinstance(payload, dict):
            raise ValueError(f"{venue} server-time response must be an object")
        server_time_ms = parse_server_time(payload, bingx=bingx)
        return build_clock_sample(
            venue, server_time_ms, local_send_ms, local_receive_ms, rtt_ms
        )

    async def run_forever(self) -> None:
        while True:
            for venue in ENDPOINTS:
                for _ in range(self.samples_per_cycle):
                    try:
                        row = await asyncio.to_thread(self._request_sample, venue)
                    except (RuntimeError, TypeError, ValueError) as exc:
                        row = {
                            "kind": "clock_sync_error",
                            "venue": venue,
                            "received_at_ms": int(time.time() * 1000),
                            "error": str(exc),
                        }
                    self._write(row)
            await asyncio.sleep(self.interval_seconds)


def summarize_clock_sync(path: str | Path) -> dict[str, Any]:
    """Summarize offsets and RTTs without modifying the raw calibration file."""
    source = Path(path)
    rows: dict[str, list[dict[str, Any]]] = {venue: [] for venue in ENDPOINTS}
    errors: dict[str, int] = {venue: 0 for venue in ENDPOINTS}
    malformed = 0
    if source.is_file():
        with source.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    malformed += 1
                    continue
                venue = row.get("venue") if isinstance(row, dict) else None
                if venue not in rows:
                    continue
                if row.get("kind") == "clock_sync_error":
                    errors[venue] += 1
                elif row.get("kind") == "clock_sync":
                    if all(isinstance(row.get(key), (int, float)) for key in
                           ("offset_ms", "rtt_ms", "local_receive_ms")):
                        rows[venue].append(row)
                    else:
                        malformed += 1

    result: dict[str, Any] = {
        "path": str(source),
        "exists": source.is_file(),
        "malformed_records": malformed,
        "by_venue": {},
    }
    for venue, samples in rows.items():
        offsets = [float(row["offset_ms"]) for row in samples]
        rtts = [float(row["rtt_ms"]) for row in samples]
        result["by_venue"][venue] = {
            "samples": len(samples),
            "errors": errors[venue],
            "rtt_ms": {
                "p50": round(statistics.median(rtts), 3) if rtts else None,
                "p95": round(sorted(rtts)[max(0, int(0.95 * len(rtts) + 0.999) - 1)], 3)
                if rtts else None,
            },
            "offset_ms": {
                "median": round(statistics.median(offsets), 3) if offsets else None,
                "min": round(min(offsets), 3) if offsets else None,
                "max": round(max(offsets), 3) if offsets else None,
                "spread": round(max(offsets) - min(offsets), 3) if offsets else None,
                "first_to_last_drift": round(offsets[-1] - offsets[0], 3)
                if len(offsets) > 1 else None,
            },
        }
    return result
