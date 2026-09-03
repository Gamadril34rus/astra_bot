"""Research-first market study engine.

The engine studies observable market states and forward consequences. It does
not require a trade and never uses future candles when constructing the state
at time t. Candidate findings are associations until independently validated.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .market_understanding import compute_market_features

HORIZONS = {
    "1h": {"1h": 1, "4h": 4, "1d": 24, "3d": 72, "7d": 168},
    "4h": {"4h": 1, "1d": 6, "3d": 18, "7d": 42, "30d": 180},
    "1d": {"1d": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90},
}
EVENT_KEYS = (
    "breakout_up", "breakout_down", "retest_support", "retest_resistance",
    "structure_hh", "structure_hl", "structure_lh", "structure_ll",
    "bullish_engulfing", "bearish_engulfing", "candle_hammer",
    "candle_shooting_star", "candle_doji", "morning_star", "evening_star",
    "channel_breakout_up_50", "channel_breakout_down_50", "volume_spike",
    "rsi_overbought", "rsi_oversold", "bollinger_squeeze", "atr_expansion",
    "trend_acceleration", "trend_deceleration",
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


def _sample_step(sample_every: int | dict[str, int], timeframe: str) -> int:
    if isinstance(sample_every, dict):
        return max(1, int(sample_every.get(timeframe, 12)))
    return max(1, int(sample_every))


def _forward_stats(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, i: int, bars: int) -> dict[str, float]:
    end = min(len(closes), i + bars + 1)
    if end <= i:
        return {}
    entry = float(closes[i])
    future_c = closes[i + 1:end]
    future_h = highs[i + 1:end]
    future_l = lows[i + 1:end]
    if not len(future_c) or entry == 0:
        return {}
    returns = future_c / entry - 1.0
    return {
        "return": float(returns[-1]),
        "max_up": float(np.max(future_h) / entry - 1.0),
        "max_down": float(np.min(future_l) / entry - 1.0),
        "volatility": float(np.std(np.diff(future_c) / np.maximum(future_c[:-1], 1e-12))) if len(future_c) > 1 else 0.0,
    }


def research_history(
    history: dict[str, list[Any]],
    output: Path = Path("models/research_observations.jsonl"),
    hypotheses_output: Path = Path("models/research_hypotheses.json"),
    sample_every: int | dict[str, int] = 12,
    append: bool = False,
) -> dict[str, int]:
    """Study event, baseline and regime-conditioned outcomes across horizons.

    TZ P0-2: baseline-наблюдения сэмплируются НЕЗАВИСИМО от наличия событий.
    Каждый baseline_step-й сэмпл (по умолчанию каждый 4-й) помечается как
    baseline-наблюдение, даже если есть события. Это даёт контрольную группу
    для расчёта lift_vs_baseline в гипотезах.
    """
    aggregates: dict[str, dict[str, Any]] = defaultdict(lambda: {"observations": 0, "returns": defaultdict(list), "max_up": defaultdict(list), "max_down": defaultdict(list)})
    stats = {"observations": 0, "events": 0, "symbols": 0, "baseline_observations": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    # Базовый шаг для baseline-квоты: каждый 4-й сэмпл = baseline.
    baseline_step = 4
    baseline_counter = 0

    with output.open(mode, encoding="utf-8") as out:
        for symbol, candles in history.items():
            if len(candles) < 220:
                continue
            stats["symbols"] += 1
            timeframe = getattr(candles[-1], "timeframe", "1h") or "1h"
            horizons = HORIZONS.get(timeframe, HORIZONS["1h"])
            step = _sample_step(sample_every, timeframe)
            closes = np.asarray([float(c.close) for c in candles], dtype=float)
            highs = np.asarray([float(c.high) for c in candles], dtype=float)
            lows = np.asarray([float(c.low) for c in candles], dtype=float)
            max_forward = max(horizons.values())
            for i in range(200, len(candles) - max_forward, step):
                features = compute_market_features(candles[: i + 1], timeframe=timeframe)
                events = _events(features)
                regime = _regime(features)

                # TZ P0-2: baseline сэмплируется независимо от событий.
                baseline_counter += 1
                is_baseline = (baseline_counter % baseline_step == 0)

                if is_baseline:
                    labels = ["baseline"]
                    stats["baseline_observations"] += 1
                elif events:
                    labels = events
                    stats["events"] += len(events)
                else:
                    labels = ["baseline"]
                    stats["baseline_observations"] += 1

                stats["observations"] += 1
                forward: dict[str, dict[str, float]] = {}
                for label, bars in horizons.items():
                    result = _forward_stats(closes, highs, lows, i, bars)
                    if result:
                        forward[label] = result
                row = {
                    "record_type": "market_research",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": int(getattr(candles[i], "open_time", 0)),
                    "market_regime": regime,
                    "events": labels,
                    "features_before": {k: float(v) for k, v in features.items() if isinstance(v, (int, float))},
                    "forward": forward,
                    "future_leakage": False,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                for event in labels:
                    key = f"{event}|{timeframe}|{regime}"
                    agg = aggregates[key]
                    agg["observations"] += 1
                    for horizon, result in forward.items():
                        agg["returns"][horizon].append(result["return"])
                        agg["max_up"][horizon].append(result["max_up"])
                        agg["max_down"][horizon].append(result["max_down"])

    # TZ P0-2: собрать baseline-статистику для расчёта lift_vs_baseline.
    # Ключ baseline-агрегатов: "baseline|{timeframe}|{regime}"
    baseline_agg: dict[str, dict[str, list]] = {}
    for key, agg in aggregates.items():
        if key.startswith("baseline|"):
            baseline_agg[key] = agg

    hypotheses: dict[str, Any] = {}
    for key, agg in aggregates.items():
        if agg["observations"] < 20:
            continue
        horizon_stats: dict[str, Any] = {}
        for label, values in agg["returns"].items():
            arr = np.asarray(values, dtype=float)
            if not len(arr):
                continue
            horizon_stats[label] = {
                "samples": len(arr),
                "mean_return": float(np.mean(arr)),
                "median_return": float(np.median(arr)),
                "positive_rate": float(np.mean(arr > 0)),
                "negative_rate": float(np.mean(arr < 0)),
                "std": float(np.std(arr)),
                "mean_max_up": float(np.mean(agg["max_up"][label])),
                "mean_max_down": float(np.mean(agg["max_down"][label])),
            }

        # TZ P0-2: baseline_expectancy и lift_vs_baseline.
        # Найти baseline для того же timeframe+regime.
        parts = key.split("|")
        if len(parts) == 3:
            _, tf, regime = parts
            bl_key = f"baseline|{tf}|{regime}"
        else:
            bl_key = None
        baseline_expectancy: dict[str, float] = {}
        lift_vs_baseline: dict[str, float] = {}
        if bl_key and bl_key in baseline_agg:
            bl_agg = baseline_agg[bl_key]
            for horizon, values in bl_agg["returns"].items():
                arr = np.asarray(values, dtype=float)
                if len(arr) >= 5:
                    bl_mean = float(np.mean(arr))
                    baseline_expectancy[horizon] = bl_mean
                    # Lift = event_mean - baseline_mean
                    if horizon in horizon_stats:
                        event_mean = horizon_stats[horizon]["mean_return"]
                        lift_vs_baseline[horizon] = event_mean - bl_mean

        hyp_entry: dict[str, Any] = {
            "observations": agg["observations"],
            "horizons": horizon_stats,
            "status": "candidate",
            "confidence": min(1.0, agg["observations"] / 500.0),
            "status_reason": "Candidate only; require walk-forward and out-of-sample confirmation before use.",
        }
        if baseline_expectancy:
            hyp_entry["baseline_expectancy"] = baseline_expectancy
        if lift_vs_baseline:
            hyp_entry["lift_vs_baseline"] = lift_vs_baseline
        hypotheses[key] = hyp_entry

    hypotheses_output.parent.mkdir(parents=True, exist_ok=True)
    hypotheses_output.write_text(
        json.dumps({"updated": datetime.now(tz=UTC).isoformat(), "stats": stats, "hypotheses": hypotheses}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return stats
