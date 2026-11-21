#!/usr/bin/env python3
"""Reproducible price-only swing-break control for the CLT research archive.

This is a benchmark, not the CLT strategy. Signals use completed Hyperliquid
15-minute candles; entries and exits are priced on timestamp-aligned BingX
candles. Results are gross and fee-adjusted illustrations, before spread,
slippage and funding.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

SYMBOLS = ("BTC", "ETH", "SOL", "HYPE", "INJ")
BAR_MS = 15 * 60 * 1_000
DEFAULT_HORIZONS = (2, 4, 8, 16, 36)
DEFAULT_LOOKBACK = 8
DEFAULT_COOLDOWN = 36
DEFAULT_BLOCK_DAYS = 3
DEFAULT_RESAMPLES = 20_000
DEFAULT_SEED = 20_261_120
MAKER_FEE_BPS_PER_SIDE = 2.0
TAKER_FEE_BPS_PER_SIDE = 5.0


def load_candles(path: Path) -> list[dict[str, float | int]]:
    """Load and strictly validate a gap-free 15m OHLC archive."""
    rows: list[dict[str, float | int]] = []
    previous_ts: int | None = None
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            try:
                ts = int(raw["open_time"])
                values = {
                    key: float(raw[key])
                    for key in ("open", "high", "low", "close")
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"malformed OHLC row in {path.name}") from exc
            if any(not math.isfinite(value) or value <= 0 for value in values.values()):
                raise ValueError(f"invalid OHLC value in {path.name} at {ts}")
            if (
                values["high"] < values["low"]
                or not values["low"] <= values["open"] <= values["high"]
                or not values["low"] <= values["close"] <= values["high"]
            ):
                raise ValueError(f"invalid OHLC range in {path.name} at {ts}")
            if previous_ts is not None:
                if ts <= previous_ts:
                    raise ValueError(f"duplicate or unordered timestamp in {path.name}")
                if ts - previous_ts != BAR_MS:
                    raise ValueError(f"15m candle gap in {path.name} after {previous_ts}")
            previous_ts = ts
            rows.append({"open_time": ts, **values})
    if not rows:
        raise ValueError(f"empty candle file: {path}")
    return rows


def detect_price_only_events(
    signal_rows: list[dict[str, float | int]],
    execution_rows: list[dict[str, float | int]],
    *,
    symbol: str,
    lookback_bars: int = DEFAULT_LOOKBACK,
    cooldown_bars: int = DEFAULT_COOLDOWN,
    horizons_bars: tuple[int, ...] = DEFAULT_HORIZONS,
) -> list[dict[str, Any]]:
    """Signal on source-venue swings; execute and mark returns on target-venue candles."""
    if lookback_bars < 2 or cooldown_bars < 0:
        raise ValueError("lookback must be >= 2 and cooldown must be >= 0")
    if not horizons_bars or any(value < 1 for value in horizons_bars):
        raise ValueError("horizons must contain positive bar counts")
    if tuple(sorted(set(horizons_bars))) != horizons_bars:
        raise ValueError("horizons must be strictly increasing and unique")
    if len(signal_rows) != len(execution_rows):
        raise ValueError("signal and execution candle counts do not match")
    signal_times = [int(row["open_time"]) for row in signal_rows]
    execution_times = [int(row["open_time"]) for row in execution_rows]
    if signal_times != execution_times:
        raise ValueError("signal and execution candle timestamps do not align")

    max_horizon = max(horizons_bars)
    events: list[dict[str, Any]] = []
    last_signal_index = -cooldown_bars - 1

    for index in range(lookback_bars, len(signal_rows) - max_horizon):
        if index - last_signal_index <= cooldown_bars:
            continue
        prior = signal_rows[index - lookback_bars:index]
        prior_low = min(float(row["low"]) for row in prior)
        prior_high = max(float(row["high"]) for row in prior)
        close = float(signal_rows[index]["close"])
        direction = -1 if close < prior_low else 1 if close > prior_high else 0
        if direction == 0:
            continue

        entry_price = float(execution_rows[index + 1]["open"])
        if not math.isfinite(entry_price) or entry_price <= 0:
            raise ValueError("entry price must be finite and positive")
        # The signal becomes observable at candle close, which is the next bar open.
        signal_ts = int(signal_rows[index]["open_time"]) + BAR_MS
        if int(execution_rows[index + 1]["open_time"]) != signal_ts:
            raise ValueError("next execution candle does not start at signal close")
        returns = {
            f"gross_bps_{horizon}": direction
            * (float(execution_rows[index + horizon]["close"]) / entry_price - 1.0)
            * 10_000
            for horizon in horizons_bars
        }
        if any(not math.isfinite(value) for value in returns.values()):
            raise ValueError("non-finite event return")
        signal_date = datetime.fromtimestamp(signal_ts / 1000, UTC).date().isoformat()
        events.append({
            "symbol": symbol,
            "signal_ts_ms": signal_ts,
            "signal_date_utc": signal_date,
            "direction": direction,
            "entry_ts_ms": int(execution_rows[index + 1]["open_time"]),
            "entry_price": entry_price,
            **returns,
        })
        last_signal_index = index

    return events


def _percentile(values: list[float], q: float) -> float:
    if not values or not 0 <= q <= 1:
        raise ValueError("percentile requires values and q within [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def moving_block_confidence_intervals(
    events: list[dict[str, Any]],
    *,
    horizons_bars: tuple[int, ...] = DEFAULT_HORIZONS,
    start_date: date,
    end_date: date,
    block_days: int = DEFAULT_BLOCK_DAYS,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, dict[str, float]]:
    """Event-weighted percentile CI from date blocks shared across markets."""
    if block_days < 1 or end_date < start_date:
        raise ValueError("invalid bootstrap date range or block size")
    if resamples < 100:
        raise ValueError("at least 100 bootstrap resamples are required")
    days = [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range((end_date - start_date).days + 1)
    ]
    if len(days) < block_days:
        raise ValueError("bootstrap range is shorter than one block")

    daily: dict[str, dict[int, list[float]]] = {
        day: {horizon: [] for horizon in horizons_bars}
        for day in days
    }
    for event in events:
        day = str(event["signal_date_utc"])
        if day not in daily:
            raise ValueError(f"event date {day} is outside bootstrap range")
        for horizon in horizons_bars:
            daily[day][horizon].append(float(event[f"gross_bps_{horizon}"]))

    rng = random.Random(seed)
    starts = len(days) - block_days + 1
    distributions: dict[int, list[float]] = {
        horizon: [] for horizon in horizons_bars
    }
    for _ in range(resamples):
        selected_days: list[str] = []
        while len(selected_days) < len(days):
            block_start = rng.randrange(starts)
            selected_days.extend(days[block_start:block_start + block_days])
        selected_days = selected_days[:len(days)]

        for horizon in horizons_bars:
            total = 0.0
            count = 0
            for day in selected_days:
                values = daily[day][horizon]
                total += sum(values)
                count += len(values)
            if count:
                distributions[horizon].append(total / count)

    result = {}
    for horizon, values in distributions.items():
        if len(values) < resamples * 0.95:
            raise RuntimeError("too many bootstrap samples contained no events")
        result[str(horizon)] = {
            "lower_95_bps": _percentile(values, 0.025),
            "upper_95_bps": _percentile(values, 0.975),
            "resamples": len(values),
        }
    return result


def _summarize_events(
    events: list[dict[str, Any]],
    *,
    horizons_bars: tuple[int, ...],
    start_date: date,
    end_date: date,
    block_days: int,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    if not events:
        return {"events": 0, "gross_mean_bps": {}}

    gross = {
        str(horizon): statistics.fmean(
            float(event[f"gross_bps_{horizon}"]) for event in events
        )
        for horizon in horizons_bars
    }
    maker_fee = 2 * MAKER_FEE_BPS_PER_SIDE
    taker_fee = 2 * TAKER_FEE_BPS_PER_SIDE
    net = {
        "maker_maker": {
            str(horizon): gross[str(horizon)] - maker_fee
            for horizon in horizons_bars
        },
        "taker_taker": {
            str(horizon): gross[str(horizon)] - taker_fee
            for horizon in horizons_bars
        },
    }
    gross_intervals = moving_block_confidence_intervals(
        events,
        horizons_bars=horizons_bars,
        start_date=start_date,
        end_date=end_date,
        block_days=block_days,
        resamples=resamples,
        seed=seed,
    )
    net_intervals = {
        fee_model: {
            str(horizon): {
                "lower_95_bps": gross_intervals[str(horizon)]["lower_95_bps"] - fee,
                "upper_95_bps": gross_intervals[str(horizon)]["upper_95_bps"] - fee,
                "resamples": gross_intervals[str(horizon)]["resamples"],
            }
            for horizon in horizons_bars
        }
        for fee_model, fee in (
            ("maker_maker", maker_fee),
            ("taker_taker", taker_fee),
        )
    }
    return {
        "events": len(events),
        "gross_mean_bps": gross,
        "net_mean_bps": net,
        "gross_moving_block_bootstrap_95pct": gross_intervals,
        "net_moving_block_bootstrap_95pct": net_intervals,
    }


def build_report(
    data_dir: Path,
    *,
    symbols: tuple[str, ...] = SYMBOLS,
    lookback_bars: int = DEFAULT_LOOKBACK,
    cooldown_bars: int = DEFAULT_COOLDOWN,
    horizons_bars: tuple[int, ...] = DEFAULT_HORIZONS,
    block_days: int = DEFAULT_BLOCK_DAYS,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("at least one symbol is required")
    all_events: list[dict[str, Any]] = []
    events_by_market: dict[str, list[dict[str, Any]]] = {}
    first_date: date | None = None
    last_date: date | None = None

    for symbol in symbols:
        signal_rows = load_candles(data_dir / f"{symbol}_HL_15m.csv")
        execution_rows = load_candles(data_dir / f"{symbol}_USDT_15m.csv")
        first = datetime.fromtimestamp(
            (int(signal_rows[0]["open_time"]) + BAR_MS) / 1000, UTC
        ).date()
        last = datetime.fromtimestamp(
            (int(signal_rows[-1]["open_time"]) + BAR_MS) / 1000, UTC
        ).date()
        first_date = first if first_date is None else min(first_date, first)
        last_date = last if last_date is None else max(last_date, last)
        events = detect_price_only_events(
            signal_rows,
            execution_rows,
            symbol=symbol,
            lookback_bars=lookback_bars,
            cooldown_bars=cooldown_bars,
            horizons_bars=horizons_bars,
        )
        events_by_market[symbol] = events
        all_events.extend(events)

    if not all_events or first_date is None or last_date is None:
        raise RuntimeError("no complete price-only events in the requested archive")

    market_stats = {
        symbol: _summarize_events(
            events,
            horizons_bars=horizons_bars,
            start_date=first_date,
            end_date=last_date,
            block_days=block_days,
            resamples=resamples,
            seed=seed,
        )
        for symbol, events in events_by_market.items()
    }
    pooled = _summarize_events(
        all_events,
        horizons_bars=horizons_bars,
        start_date=first_date,
        end_date=last_date,
        block_days=block_days,
        resamples=resamples,
        seed=seed,
    )
    excluding_hype_events = [
        event for event in all_events if event["symbol"] != "HYPE"
    ]
    excluding_hype = _summarize_events(
        excluding_hype_events,
        horizons_bars=horizons_bars,
        start_date=first_date,
        end_date=last_date,
        block_days=block_days,
        resamples=resamples,
        seed=seed,
    )

    return {
        "status": "PRICE_ONLY_SWING_BREAK_CONTROL",
        "window_start_utc": first_date.isoformat(),
        "window_end_utc_inclusive": last_date.isoformat(),
        "signal_source": "Hyperliquid perpetual 15m OHLC",
        "execution_source": "BingX USDT-M perpetual 15m OHLC",
        "lookback_bars": lookback_bars,
        "cooldown_bars": cooldown_bars,
        "entry": "next 15m candle open",
        "exit": "close after the specified number of 15m bars from entry",
        "horizons_bars": list(horizons_bars),
        "events_total": len(all_events),
        "markets": market_stats,
        "pooled_gross_mean_bps": pooled["gross_mean_bps"],
        "pooled_net_mean_bps": pooled["net_mean_bps"],
        "pooled_gross_moving_block_bootstrap_95pct": (
            pooled["gross_moving_block_bootstrap_95pct"]
        ),
        "pooled_net_moving_block_bootstrap_95pct": (
            pooled["net_moving_block_bootstrap_95pct"]
        ),
        "excluding_hype": excluding_hype,
        "round_trip_fee_assumptions_bps": {
            "maker_maker": 2 * MAKER_FEE_BPS_PER_SIDE,
            "taker_taker": 2 * TAKER_FEE_BPS_PER_SIDE,
        },
        "bootstrap": {
            "method": "non-circular moving blocks of UTC calendar dates; identical sampled calendar blocks across pooled, per-market and ex-HYPE views; event-weighted means",
            "block_days": block_days,
            "resamples": resamples,
            "seed": seed,
        },
        "limitations": [
            "Price-only control, not a CLT event study or a strategy promotion result.",
            "Fee scenarios use published BingX base rates, not the owner's confirmed account rates.",
            "Spread, slippage, funding and market impact are excluded.",
            "All horizons use the same events with complete forward data through the maximum horizon.",
            "Per-market and ex-HYPE intervals are descriptive multiple views of one sample, not independent confirmatory tests.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("reports/clt_bingx_market_data"))
    parser.add_argument("--output", type=Path, default=Path("reports/clt_bingx_market_data/price_control.json"))
    parser.add_argument("--lookback-bars", type=int, default=DEFAULT_LOOKBACK)
    parser.add_argument("--cooldown-bars", type=int, default=DEFAULT_COOLDOWN)
    parser.add_argument("--block-days", type=int, default=DEFAULT_BLOCK_DAYS)
    parser.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    report = build_report(
        args.data_dir,
        lookback_bars=args.lookback_bars,
        cooldown_bars=args.cooldown_bars,
        block_days=args.block_days,
        resamples=args.resamples,
        seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "events_total": report["events_total"],
        "pooled_gross_mean_bps": report["pooled_gross_mean_bps"],
        "pooled_net_mean_bps": report["pooled_net_mean_bps"],
        "pooled_gross_moving_block_bootstrap_95pct": report["pooled_gross_moving_block_bootstrap_95pct"],
        "excluding_hype": report["excluding_hype"],
        "markets": report["markets"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
