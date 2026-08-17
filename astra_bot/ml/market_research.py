"""Research-first market study engine.

The engine studies what happens after market conditions/events instead of
counting profitable trades. It records observations, forward returns and
aggregated hypotheses for multiple horizons.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .market_understanding import compute_market_features

HORIZONS = (5, 15, 60, 240, 1440)
EVENT_KEYS = (
    "breakout_up", "breakout_down", "retest_support", "retest_resistance",
    "structure_hh", "structure_hl", "structure_lh", "structure_ll",
    "bullish_engulfing", "bearish_engulfing", "candle_hammer",
    "candle_shooting_star", "candle_doji", "morning_star", "evening_star",
    "channel_breakout_up_50", "channel_breakout_down_50",
)


def _regime(features: dict[str, float]) -> str:
    trend = abs(float(features.get("trend_slope_50", 0.0)))
    r2 = float(features.get("trend_r2_50", 0.0))
    atr = float(features.get("atr_pct", 0.0))
    if atr >= 7:
        return "high_volatility"
    if r2 >= 0.55 and trend >= 0.01:
        return "trend"
    if r2 < 0.25:
        return "range"
    return "transition"


def _events(features: dict[str, float]) -> list[str]:
    return [name for name in EVENT_KEYS if float(features.get(name, 0.0)) > 0.5]


def research_history(
    history: dict[str, list[Any]],
    output: Path = Path("models/research_observations.jsonl"),
    hypotheses_output: Path = Path("models/research_hypotheses.json"),
    sample_every: dict[str, int] | None = None,
) -> dict[str, int]:
    """Study historical market events and their forward consequences."""
    sample_every = sample_every or {"1h": 12, "4h": 3, "1d": 1}
    aggregates: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "observations": 0, "positive": 0, "negative": 0,
        "returns": {str(h): [] for h in HORIZONS},
    })
    stats = {"observations": 0, "events": 0, "symbols": 0}
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as out:
        for symbol, candles in history.items():
            if len(candles) < 220:
                continue
            stats["symbols"] += 1
            timeframe = getattr(candles[-1], "timeframe", "1h") or "1h"
            step = max(1, int(sample_every.get(timeframe, 12)))
            closes = np.asarray([float(c.close) for c in candles], dtype=float)
            for i in range(200, len(candles) - 5, step):
                # Feature engine is explicitly causal: only candles through i.
                features = compute_market_features(candles[: i + 1], timeframe=timeframe)
                events = _events(features)
                if not events:
                    continue
                stats["observations"] += 1
                stats["events"] += len(events)
                regime = _regime(features)
                returns: dict[str, float | None] = {}
                for horizon in HORIZONS:
                    j = i + horizon if i + horizon < len(closes) else None
                    returns[str(horizon)] = None if j is None else float(closes[j] / closes[i] - 1.0)
                for event in events:
                    key = f"{event}|{timeframe}|{regime}"
                    agg = aggregates[key]
                    agg["observations"] += 1
                    for h, value in returns.items():
                        if value is not None:
                            agg["returns"][h].append(value)
                            if value > 0:
                                agg["positive"] += 1 if h == "60" else 0
                            elif value < 0:
                                agg["negative"] += 1 if h == "60" else 0
                row = {
                    "record_type": "market_research",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": int(getattr(candles[i], "open_time", 0)),
                    "market_regime": regime,
                    "events": events,
                    "features": {k: float(v) for k, v in features.items() if isinstance(v, (int, float))},
                    "forward_returns": returns,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")

    hypotheses: dict[str, Any] = {}
    for key, agg in aggregates.items():
        if agg["observations"] < 20:
            continue
        horizon_stats = {}
        for h, values in agg["returns"].items():
            if not values:
                continue
            arr = np.asarray(values, dtype=float)
            mean = float(np.mean(arr))
            median = float(np.median(arr))
            positive_rate = float(np.mean(arr > 0))
            horizon_stats[h] = {
                "samples": int(len(arr)),
                "mean_return": mean,
                "median_return": median,
                "positive_rate": positive_rate,
                "std": float(np.std(arr)),
            }
        hypotheses[key] = {
            "observations": agg["observations"],
            "horizons": horizon_stats,
            "status": "candidate",
            "note": "Requires out-of-sample confirmation before being treated as a rule.",
        }

    hypotheses_output.parent.mkdir(parents=True, exist_ok=True)
    hypotheses_output.write_text(
        json.dumps({
            "updated": datetime.now(tz=UTC).isoformat(),
            "stats": stats,
            "hypotheses": hypotheses,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return stats
