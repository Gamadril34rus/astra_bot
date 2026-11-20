#!/usr/bin/env python3
"""Causal event labels for Crowd Liquidity Trap research.

Input rows must already contain explicit, direction-aware cohort inventory deltas.
This module does not infer position changes from raw buy/sell prints.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

INVENTORY_FIELDS = (
    "open_long_usd",
    "close_long_usd",
    "open_short_usd",
    "close_short_usd",
)


def inventory_deltas(fill_totals: Mapping[str, float]) -> tuple[float, float]:
    """Return (long_inventory_delta, short_inventory_delta) from explicit fill directions."""
    missing = [key for key in INVENTORY_FIELDS if key not in fill_totals]
    if missing:
        raise ValueError(f"missing explicit direction totals: {', '.join(missing)}")
    values = [float(fill_totals[key]) for key in INVENTORY_FIELDS]
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("fill notionals must be finite and non-negative")
    open_long, close_long, open_short, close_short = values
    return open_long - close_long, open_short - close_short


def _number(row: Mapping[str, Any], key: str) -> float:
    value = float(row[key])
    if not isfinite(value):
        raise ValueError(f"{key} must be finite")
    return value


def _sum(rows: Sequence[Mapping[str, Any]], start: int, end: int, key: str) -> float:
    return sum(_number(row, key) for row in rows[start:end])


def detect_clt_events(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_crowd_add_usd: float,
    min_large_add_usd: float,
    min_continued_crowd_add_usd: float,
    max_crowd_progress_z: float,
    crowd_window_bars: int = 12,
    max_lag_bars: int = 12,
    max_confirmation_bars: int = 12,
    swing_lookback_bars: int = 8,
    cooldown_bars: int = 36,
    min_crowd_active_wallets: int = 3,
    min_large_active_wallets: int = 1,
) -> list[dict[str, Any]]:
    """Detect non-overlapping CLT structure-break events from normalized 15m rows.

    Each row requires OHLC, cohort inventory deltas, rolling unique participant
    counts, a closed 1h context, and prior_volatility_pct computed only from bars
    before that row. Absorption is proxied by pre-break crowd-directional progress
    normalized by prior volatility.

    Signal is known only after the current bar closes; entry is next bar open.
    Direction is +1 LONG and -1 SHORT. No event is emitted without a next bar.
    """
    if min_crowd_add_usd <= 0 or min_large_add_usd <= 0:
        raise ValueError("inventory thresholds must be positive")
    if min_continued_crowd_add_usd < 0 or max_crowd_progress_z < 0:
        raise ValueError("progress and continuation thresholds cannot be negative")
    if min(
        crowd_window_bars, max_lag_bars, max_confirmation_bars,
        swing_lookback_bars, cooldown_bars,
    ) < 1:
        raise ValueError("bar windows must be positive")
    if max(crowd_window_bars, max_lag_bars, max_confirmation_bars) > 12:
        raise ValueError("rolling participant coverage is limited to 12 prior bars")
    if min(min_crowd_active_wallets, min_large_active_wallets) < 1:
        raise ValueError("active wallet thresholds must be positive")

    required = (
        "ts", "open", "high", "low", "close",
        "crowd_long_delta_usd", "crowd_short_delta_usd",
        "large_long_delta_usd", "large_short_delta_usd",
        "small_long_add_wallets", "small_short_add_wallets",
        "large_long_add_wallets", "large_short_add_wallets",
        "prior_volatility_pct",
        "htf_context_ready", "htf_context_ts", "htf_context_age_ms",
        "htf_return_pct", "htf_range_pct", "htf_structure_break",
        "htf_close_location",
    )
    rolling_participant_fields = [
        f"{cohort}_{direction}_add_wallets_prev_{window}"
        for cohort in ("small", "large")
        for direction in ("long", "short")
        for window in range(1, max(crowd_window_bars, max_lag_bars, max_confirmation_bars) + 1)
    ]
    required = required + tuple(rolling_participant_fields)
    previous_ts = None
    for row in rows:
        missing = [key for key in required if key not in row]
        if missing:
            raise ValueError(f"row missing fields: {', '.join(missing)}")
        if previous_ts is not None and row["ts"] <= previous_ts:
            raise ValueError("rows must be strictly chronological")
        previous_ts = row["ts"]
        for key in ("open", "high", "low", "close"):
            if _number(row, key) <= 0:
                raise ValueError(f"{key} must be positive")
        if _number(row, "prior_volatility_pct") < 0:
            raise ValueError("prior_volatility_pct cannot be negative")
        if not isinstance(row["htf_context_ready"], bool):
            raise ValueError("htf_context_ready must be boolean")
        if row["htf_context_ready"]:
            if row["htf_context_ts"] is None:
                raise ValueError("ready 1h context requires htf_context_ts")
            if _number(row, "htf_context_age_ms") < 0:
                raise ValueError("htf_context_age_ms cannot be negative")
            if _number(row, "htf_range_pct") < 0:
                raise ValueError("htf_range_pct cannot be negative")
            if not 0 <= _number(row, "htf_close_location") <= 1:
                raise ValueError("htf_close_location must be within [0, 1]")
            if _number(row, "htf_structure_break") not in (-1, 0, 1):
                raise ValueError("htf_structure_break must be -1, 0 or 1")

    events: list[dict[str, Any]] = []
    last_event_index = -cooldown_bars - 1
    for signal_index in range(len(rows) - 1):
        if signal_index - last_event_index <= cooldown_bars:
            continue
        if not rows[signal_index]["htf_context_ready"]:
            continue
        swing_start = max(0, signal_index - swing_lookback_bars)
        if signal_index - swing_start < swing_lookback_bars:
            continue
        prior_lows = [_number(row, "low") for row in rows[swing_start:signal_index]]
        prior_highs = [_number(row, "high") for row in rows[swing_start:signal_index]]
        close = _number(rows[signal_index], "close")

        found = None
        search_start = max(0, signal_index - max_lag_bars - crowd_window_bars)
        for crowd_start in range(search_start, signal_index):
            crowd_end = min(signal_index, crowd_start + crowd_window_bars)
            for crowd_direction in (1, -1):
                crowd_key = (
                    "crowd_long_delta_usd" if crowd_direction == 1
                    else "crowd_short_delta_usd"
                )
                large_key = (
                    "large_short_delta_usd" if crowd_direction == 1
                    else "large_long_delta_usd"
                )
                crowd_active_key = (
                    "small_long_add_wallets" if crowd_direction == 1
                    else "small_short_add_wallets"
                )
                large_active_key = (
                    "large_short_add_wallets" if crowd_direction == 1
                    else "large_long_add_wallets"
                )
                for large_start in range(crowd_start + 1, min(crowd_end, signal_index)):
                    if large_start - crowd_start > max_lag_bars:
                        continue
                    if signal_index - large_start > max_confirmation_bars:
                        continue
                    if _number(rows[large_start], large_key) <= 0:
                        continue
                    prior_large_add = _sum(
                        rows,
                        max(0, crowd_start - crowd_window_bars),
                        large_start,
                        large_key,
                    )
                    if prior_large_add > 0:
                        continue
                    crowd_build_bars = large_start - crowd_start
                    crowd_active_wallets = _number(
                        rows[large_start],
                        f"{crowd_active_key}_prev_{crowd_build_bars}",
                    )
                    if crowd_active_wallets < min_crowd_active_wallets:
                        continue
                    crowd_build = _sum(rows, crowd_start, large_start, crowd_key)
                    if crowd_build < min_crowd_add_usd:
                        continue
                    large_active_wallets = _number(rows[large_start], large_active_key)
                    if large_active_wallets < min_large_active_wallets:
                        continue
                    large_add = _sum(rows, large_start, signal_index, large_key)
                    if large_add < min_large_add_usd:
                        continue
                    continued_bars = signal_index - large_start - 1
                    continued_active_wallets = (
                        _number(
                            rows[signal_index],
                            f"{crowd_active_key}_prev_{continued_bars}",
                        )
                        if continued_bars > 0 else 0
                    )
                    continued_add = _sum(rows, large_start + 1, signal_index, crowd_key)
                    if (
                        continued_add < min_continued_crowd_add_usd
                        or continued_active_wallets < min_crowd_active_wallets
                    ):
                        continue

                    start_close = _number(rows[crowd_start], "close")
                    pre_break_close = _number(rows[signal_index - 1], "close")
                    progress_pct = (
                        crowd_direction * (pre_break_close / start_close - 1.0) * 100.0
                    )
                    volatility_pct = _number(rows[signal_index], "prior_volatility_pct")
                    if volatility_pct <= 0:
                        continue
                    elapsed_bars = max(1, signal_index - crowd_start)
                    expected_move_pct = volatility_pct * elapsed_bars**0.5
                    progress_z = progress_pct / expected_move_pct
                    if progress_z > max_crowd_progress_z:
                        continue

                    structure_broken = (
                        close < min(prior_lows) if crowd_direction == 1
                        else close > max(prior_highs)
                    )
                    if not structure_broken:
                        continue

                    found = {
                        "crowd_start_ts": rows[crowd_start]["ts"],
                        "large_add_start_ts": rows[large_start]["ts"],
                        "signal_ts": rows[signal_index]["ts"],
                        "entry_ts": rows[signal_index + 1]["ts"],
                        "crowd_to_large_bars": crowd_build_bars,
                        "large_to_break_bars": signal_index - large_start,
                        "continued_bars": continued_bars,
                        "entry_price": _number(rows[signal_index + 1], "open"),
                        "signal_index": signal_index,
                        "entry_index": signal_index + 1,
                        "direction": -crowd_direction,
                        "setup": "crowd_long_trap" if crowd_direction == 1
                            else "crowd_short_trap",
                        "crowd_build_usd": round(crowd_build, 2),
                        "large_opposite_add_usd": round(large_add, 2),
                        "continued_crowd_add_usd": round(continued_add, 2),
                        "crowd_active_wallets": int(crowd_active_wallets),
                        "large_active_wallets": int(large_active_wallets),
                        "continued_active_wallets": int(continued_active_wallets),
                        "crowd_directional_progress_pct": round(progress_pct, 6),
                        "crowd_directional_progress_z": round(progress_z, 6),
                        "prior_swing_low": min(prior_lows),
                        "prior_swing_high": max(prior_highs),
                        "htf_context_ts": rows[signal_index]["htf_context_ts"],
                        "htf_context_age_ms": rows[signal_index]["htf_context_age_ms"],
                        "htf_return_pct": rows[signal_index]["htf_return_pct"],
                        "htf_range_pct": rows[signal_index]["htf_range_pct"],
                        "htf_structure_break": rows[signal_index]["htf_structure_break"],
                        "htf_close_location": rows[signal_index]["htf_close_location"],
                    }
                    break
                if found:
                    break
            if found:
                break

        if found:
            events.append(found)
            last_event_index = signal_index

    return events
