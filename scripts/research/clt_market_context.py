"""Closed-candle higher-timeframe context for CLT research.

The 1h feature row is selected only when its close timestamp is no later
than the 15m signal candle close. Context is descriptive/stratifying; it is
not a hard directional filter and uses no EMA.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

HOUR_MS = 60 * 60 * 1_000
QUARTER_HOUR_MS = 15 * 60 * 1_000


def _positive(row: Mapping[str, Any], key: str) -> float:
    value = float(row[key])
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be finite and positive")
    return value


def attach_closed_1h_context(
    bars_15m: Sequence[Mapping[str, Any]],
    bars_1h: Sequence[Mapping[str, Any]],
    *,
    lookback_hours: int = 4,
) -> list[dict[str, Any]]:
    """Attach the latest fully closed 1h structure to each 15m bar.

    Input ts is candle open time in milliseconds. Optional close_time is the
    exchange candle close timestamp; otherwise the exclusive close boundary
    ts + interval is used. At a 15m candle close, a 1h candle closing at that
    same instant is considered available.
    """
    if lookback_hours < 2:
        raise ValueError("lookback_hours must be at least 2")
    previous_15m = None
    for row in bars_15m:
        ts = int(row["ts"])
        if previous_15m is not None and ts <= previous_15m:
            raise ValueError("15m bars must be strictly chronological")
        previous_15m = ts
        for key in ("open", "high", "low", "close"):
            _positive(row, key)

    hour_rows = []
    previous_1h = None
    for row in bars_1h:
        ts = int(row["ts"])
        if previous_1h is not None and ts <= previous_1h:
            raise ValueError("1h bars must be strictly chronological")
        if previous_1h is not None and ts - previous_1h != HOUR_MS:
            raise ValueError("1h context contains a gap; refusing partial structure")
        previous_1h = ts
        high = _positive(row, "high")
        low = _positive(row, "low")
        close = _positive(row, "close")
        if high < low or not low <= close <= high:
            raise ValueError("invalid 1h OHLC range")
        close_time = int(row.get("close_time", ts + HOUR_MS))
        if close_time < ts or close_time > ts + HOUR_MS:
            raise ValueError("invalid 1h close_time")
        hour_rows.append({**row, "_close_time": close_time})

    result = []
    pointer = -1
    for bar in bars_15m:
        signal_close = int(bar["ts"]) + QUARTER_HOUR_MS
        while (
            pointer + 1 < len(hour_rows)
            and hour_rows[pointer + 1]["_close_time"] <= signal_close
        ):
            pointer += 1

        enriched = dict(bar)
        if pointer < lookback_hours:
            enriched.update({
                "htf_context_ready": False,
                "htf_context_ts": None,
                "htf_context_age_ms": None,
                "htf_return_pct": None,
                "htf_range_pct": None,
                "htf_structure_break": None,
                "htf_close_location": None,
            })
            result.append(enriched)
            continue

        current = hour_rows[pointer]
        prior = hour_rows[pointer - lookback_hours:pointer]
        prior_high = max(_positive(row, "high") for row in prior)
        prior_low = min(_positive(row, "low") for row in prior)
        current_close = _positive(current, "close")
        current_high = _positive(current, "high")
        current_low = _positive(current, "low")
        baseline_close = _positive(
            hour_rows[pointer - lookback_hours], "close"
        )
        structure_break = (
            1 if current_close > prior_high
            else -1 if current_close < prior_low
            else 0
        )
        close_location = (
            (current_close - current_low) / (current_high - current_low)
            if current_high > current_low else 0.5
        )
        enriched.update({
            "htf_context_ready": True,
            "htf_context_ts": int(current["ts"]),
            "htf_context_age_ms": max(
                0, signal_close - int(current["_close_time"])
            ),
            "htf_return_pct": (current_close / baseline_close - 1.0) * 100.0,
            "htf_range_pct": (prior_high - prior_low) / current_close * 100.0,
            "htf_structure_break": structure_break,
            "htf_close_location": close_location,
        })
        result.append(enriched)
    return result
