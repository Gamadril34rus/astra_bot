from __future__ import annotations

import asyncio
from pathlib import Path

from scripts import archive_bingx_funding_history as archive


DAY_MS = 24 * 60 * 60 * 1000
EIGHT_HOURS_MS = 8 * 60 * 60 * 1000


class _EmptyWideWindowClient:
    async def _request(self, method: str, endpoint: str, *, params: dict):
        start = int(params["startTime"])
        end = int(params["endTime"])
        if end - start + 1 > 8 * 60 * 60 * 1000:
            return {"code": 0, "data": []}
        rows = []
        timestamp = ((start + EIGHT_HOURS_MS - 1) // EIGHT_HOURS_MS) * EIGHT_HOURS_MS
        while timestamp <= end:
            rows.append({
                "fundingTime": timestamp,
                "fundingRate": "0.0001",
            })
            timestamp += EIGHT_HOURS_MS
        return {"code": 0, "data": rows}


def test_empty_seven_day_window_is_retried_as_daily_windows(
    tmp_path: Path, monkeypatch
) -> None:
    async def passthrough(operation, *, label: str):
        return await operation()

    monkeypatch.setattr(archive, "OUT_DIR", tmp_path)
    monkeypatch.setattr(archive, "_call_with_cooldown", passthrough)

    result = asyncio.run(archive.download_symbol(
        _EmptyWideWindowClient(),
        "BTC-USDT",
        0,
        7 * DAY_MS - 1,
        chunk_days=7,
    ))

    assert result["events"] == 21
    assert result["strict_coverage_ok"] is True
    assert result["empty_chunk_fallback_count"] == 1
    assert result["chunks"][0]["fallback_used"] is True
    assert len(result["chunks"][0]["fallback_subchunks"]) == 7
    assert all(len(day["fallback_8h"]) == 3 for day in result["chunks"][0]["fallback_subchunks"])
    assert result["empty_day_fallback_count"] == 7
    assert result["chunks"][0]["accepted_rows"] == 21
    assert (tmp_path / "funding_BTCUSDT.csv").is_file()
