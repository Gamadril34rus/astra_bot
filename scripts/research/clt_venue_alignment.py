#!/usr/bin/env python3
"""Compare BingX execution candles with Hyperliquid source-venue candles."""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any

TIMEFRAMES = ("15m", "1h")
SYMBOLS = ("BTC", "ETH", "SOL", "HYPE", "INJ")


def load_candles(path: Path) -> list[dict[str, float | int]]:
    rows = []
    previous_ts = None
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            ts = int(raw["open_time"])
            values = {
                key: float(raw[key]) for key in ("open", "high", "low", "close")
            }
            if any(not math.isfinite(value) or value <= 0 for value in values.values()):
                raise ValueError(f"invalid OHLC value in {path.name} at {ts}")
            if (
                values["high"] < values["low"]
                or not values["low"] <= values["open"] <= values["high"]
                or not values["low"] <= values["close"] <= values["high"]
            ):
                raise ValueError(f"invalid OHLC range in {path.name} at {ts}")
            if previous_ts is not None and ts <= previous_ts:
                raise ValueError(f"duplicate or unordered timestamp in {path.name}")
            previous_ts = ts
            rows.append({"open_time": ts, **values})
    if not rows:
        raise ValueError(f"empty candle file: {path}")
    return rows


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if not 0 <= q <= 1:
        raise ValueError("quantile must be within [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _direction(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _swing_breaks(
    rows: list[dict[str, float | int]], *, lookback_bars: int
) -> list[int]:
    if lookback_bars < 2:
        raise ValueError("lookback_bars must be at least 2")
    result = [0] * len(rows)
    for index in range(lookback_bars, len(rows)):
        prior = rows[index - lookback_bars:index]
        prior_low = min(float(row["low"]) for row in prior)
        prior_high = max(float(row["high"]) for row in prior)
        close = float(rows[index]["close"])
        if close < prior_low:
            result[index] = -1
        elif close > prior_high:
            result[index] = 1
    return result


def compare_candles(
    bingx: list[dict[str, float | int]],
    hyperliquid: list[dict[str, float | int]],
    *,
    lookback_bars: int = 8,
) -> dict[str, Any]:
    bingx_times = [int(row["open_time"]) for row in bingx]
    hyperliquid_times = [int(row["open_time"]) for row in hyperliquid]
    if bingx_times != hyperliquid_times:
        raise ValueError("BingX and Hyperliquid candle timestamps do not align")
    if not bingx:
        raise ValueError("no aligned candles to compare")

    close_basis = []
    open_basis = []
    range_width_diff = []
    candle_direction_matches = 0
    for left, right in zip(bingx, hyperliquid, strict=True):
        left_open = float(left["open"])
        right_open = float(right["open"])
        left_close = float(left["close"])
        right_close = float(right["close"])
        close_basis.append((left_close / right_close - 1) * 10_000)
        open_basis.append((left_open / right_open - 1) * 10_000)
        left_direction = _direction(left_close / left_open - 1)
        right_direction = _direction(right_close / right_open - 1)
        candle_direction_matches += left_direction == right_direction
        left_range = (float(left["high"]) / float(left["low"]) - 1) * 10_000
        right_range = (float(right["high"]) / float(right["low"]) - 1) * 10_000
        range_width_diff.append(abs(left_range - right_range))

    bingx_breaks = _swing_breaks(bingx, lookback_bars=lookback_bars)
    hyperliquid_breaks = _swing_breaks(hyperliquid, lookback_bars=lookback_bars)
    same = opposite = only_bingx = only_hyperliquid = 0
    for left, right in zip(bingx_breaks, hyperliquid_breaks, strict=True):
        if left and right:
            if left == right:
                same += 1
            else:
                opposite += 1
        elif left:
            only_bingx += 1
        elif right:
            only_hyperliquid += 1
    union = same + opposite + only_bingx + only_hyperliquid

    def basis_summary(values: list[float]) -> dict[str, float]:
        return {
            "median_signed_bps": statistics.median(values),
            "p95_absolute_bps": _quantile([abs(value) for value in values], 0.95),
            "max_absolute_bps": max(abs(value) for value in values),
        }

    return {
        "matched_bars": len(bingx),
        "close_basis": basis_summary(close_basis),
        "open_basis": basis_summary(open_basis),
        "candle_direction_agreement_pct": candle_direction_matches / len(bingx) * 100,
        "median_absolute_range_width_difference_bps": statistics.median(range_width_diff),
        "swing_lookback_bars": lookback_bars,
        "bingx_swing_breaks": sum(value != 0 for value in bingx_breaks),
        "hyperliquid_swing_breaks": sum(value != 0 for value in hyperliquid_breaks),
        "same_direction_swing_breaks": same,
        "opposite_direction_swing_breaks": opposite,
        "bingx_only_swing_breaks": only_bingx,
        "hyperliquid_only_swing_breaks": only_hyperliquid,
        "swing_break_union": union,
        "same_direction_overlap_pct": same / union * 100 if union else None,
    }


def build_report(
    data_dir: Path,
    *,
    symbols: tuple[str, ...] = SYMBOLS,
    lookback_bars: int = 8,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "signal_source": "Hyperliquid perpetual candles and reconstructed wallet inventory",
        "execution_source": "BingX USDT-M perpetual candles and funding",
        "lookback_bars": lookback_bars,
        "markets": {},
    }
    for symbol in symbols:
        market_report = {}
        for timeframe in TIMEFRAMES:
            bingx = load_candles(data_dir / f"{symbol}_USDT_{timeframe}.csv")
            hyperliquid = load_candles(data_dir / f"{symbol}_HL_{timeframe}.csv")
            market_report[timeframe] = compare_candles(
                bingx, hyperliquid, lookback_bars=lookback_bars
            )
        report["markets"][symbol] = market_report
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("reports/clt_bingx_market_data"))
    parser.add_argument("--output", type=Path, default=Path("reports/clt_bingx_market_data/venue_alignment.json"))
    parser.add_argument("--lookback-bars", type=int, default=8)
    args = parser.parse_args()
    report = build_report(
        args.data_dir,
        lookback_bars=args.lookback_bars,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
