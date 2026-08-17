"""Research-first engine for discovering and validating market relationships.

The engine never creates orders. It studies observable state -> future response,
keeps discovery and validation periods separate, and records negative findings.
"""
from __future__ import annotations

import json
import math
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

EVENT_FEATURES = (
    "breakout_up", "breakout_down", "retest_support", "retest_resistance",
    "structure_hh", "structure_hl", "structure_lh", "structure_ll",
    "bullish_engulfing", "bearish_engulfing", "candle_hammer",
    "candle_shooting_star", "candle_doji", "morning_star", "evening_star",
    "channel_breakout_up_50", "channel_breakout_down_50", "volume_spike",
    "rsi_overbought", "rsi_oversold", "bollinger_squeeze", "atr_expansion",
    "trend_acceleration", "trend_deceleration",
)


def _regime(f: dict[str, float]) -> str:
    atr = float(f.get("atr_pct", 0.0))
    slope = abs(float(f.get("trend_slope_50", 0.0)))
    r2 = float(f.get("trend_r2_50", 0.0))
    if atr >= 7.0:
        return "high_volatility"
    if r2 >= 0.55 and slope >= 0.01:
        return "trend"
    if r2 < 0.25:
        return "range"
    return "transition"


def _events(
    f: dict[str, float],
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    i: int,
) -> list[str]:
    """Combine explicit feature flags with raw OHLCV-derived event flags."""
    labels = [name for name in EVENT_FEATURES if float(f.get(name, 0.0)) > 0.5]
    if i >= 20:
        recent_volume = volumes[max(0, i - 20):i]
        median_volume = float(np.median(recent_volume)) if len(recent_volume) else 0.0
        if median_volume > 0 and volumes[i] >= median_volume * 2.0:
            labels.append("volume_spike")
    if i >= 21:
        recent_atr = []
        for j in range(i - 20, i + 1):
            prev = closes[j - 1]
            tr = max(highs[j] - lows[j], abs(highs[j] - prev), abs(lows[j] - prev))
            recent_atr.append(tr / max(closes[j], 1e-12))
        current_atr = recent_atr[-1]
        base_atr = float(np.median(recent_atr[:-1])) if len(recent_atr) > 1 else 0.0
        if base_atr > 0 and current_atr >= base_atr * 1.5:
            labels.append("atr_expansion")
    rsi = float(f.get("rsi_14", 50.0))
    if rsi >= 70:
        labels.append("rsi_overbought")
    elif rsi <= 30:
        labels.append("rsi_oversold")
    bb_width = f.get("bb_width_pct")
    if bb_width is not None and float(bb_width) <= 2.0:
        labels.append("bollinger_squeeze")
    slope20 = float(f.get("trend_slope_20", 0.0))
    slope50 = float(f.get("trend_slope_50", 0.0))
    if abs(slope20) > abs(slope50) * 1.35 and abs(slope20) > 0.01:
        labels.append("trend_acceleration")
    elif abs(slope20) < abs(slope50) * 0.65 and abs(slope50) > 0.01:
        labels.append("trend_deceleration")
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(labels))


def _forward(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, i: int, bars: int) -> dict[str, float]:
    end = min(len(closes), i + bars + 1)
    if end <= i + 1:
        return {}
    entry = float(closes[i])
    if entry <= 0:
        return {}
    c = closes[i + 1:end]
    h = highs[i + 1:end]
    l = lows[i + 1:end]
    returns = c / entry - 1.0
    step_returns = np.diff(np.r_[entry, c]) / np.maximum(np.r_[entry, c[:-1]], 1e-12)
    return {
        "return": float(returns[-1]),
        "max_up": float(np.max(h) / entry - 1.0),
        "max_down": float(np.min(l) / entry - 1.0),
        "volatility": float(np.std(step_returns)) if len(step_returns) else 0.0,
    }


def _summary(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=float)
    if a.size == 0:
        return {"samples": 0}
    mean = float(np.mean(a))
    std = float(np.std(a, ddof=1)) if a.size > 1 else 0.0
    se = std / math.sqrt(a.size) if a.size > 1 else 0.0
    return {
        "samples": int(a.size),
        "mean": mean,
        "median": float(np.median(a)),
        "positive_rate": float(np.mean(a > 0)),
        "negative_rate": float(np.mean(a < 0)),
        "std": std,
        "t_stat": mean / se if se > 0 else 0.0,
    }


def _fdr_qvalues(pairs: list[tuple[str, float]]) -> dict[str, float]:
    """Benjamini-Hochberg FDR correction for candidate discovery."""
    if not pairs:
        return {}
    ordered = sorted(pairs, key=lambda x: x[1])
    m = len(ordered)
    out: dict[str, float] = {}
    running = 1.0
    for rank in range(m, 0, -1):
        key, p = ordered[rank - 1]
        q = min(running, p * m / rank)
        running = q
        out[key] = float(q)
    return out


def research_history_v2(
    history: dict[str, list[Any]],
    output: Path = Path("models/research_observations.jsonl"),
    hypotheses_output: Path = Path("models/research_hypotheses.json"),
    sample_every: int | dict[str, int] = 12,
    validation_fraction: float = 0.30,
    min_samples: int = 30,
    news_service: Any | None = None,
) -> dict[str, int]:
    """Run discovery/OOS research over OHLCV without placing trades."""
    validation_fraction = min(0.45, max(0.15, validation_fraction))
    output.parent.mkdir(parents=True, exist_ok=True)
    aggregates: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "all": defaultdict(list), "discovery": defaultdict(list), "validation": defaultdict(list),
        "count": 0, "validation_count": 0,
    })
    stats = {"symbols": 0, "observations": 0, "events": 0, "baseline_observations": 0, "validation_observations": 0}

    with output.open("w", encoding="utf-8") as out:
        for symbol, candles in history.items():
            if len(candles) < 240:
                continue
            stats["symbols"] += 1
            timeframe = getattr(candles[-1], "timeframe", "1h") or "1h"
            horizons = HORIZONS.get(timeframe, HORIZONS["1h"])
            step = max(1, int(sample_every.get(timeframe, 12) if isinstance(sample_every, dict) else sample_every))
            closes = np.asarray([float(c.close) for c in candles], dtype=float)
            highs = np.asarray([float(c.high) for c in candles], dtype=float)
            lows = np.asarray([float(c.low) for c in candles], dtype=float)
            volumes = np.asarray([float(c.volume) for c in candles], dtype=float)
            split = int(len(candles) * (1.0 - validation_fraction))
            max_forward = max(horizons.values())
            for i in range(200, len(candles) - max_forward, step):
                f = compute_market_features(candles[: i + 1], timeframe=timeframe)
                if news_service is not None:
                    try:
                        snap = news_service.cached_historical(symbol, int(getattr(candles[i], "open_time", 0)))
                        f.update(snap.to_features())
                    except Exception:
                        pass
                regime = _regime(f)
                events = _events(f, closes, highs, lows, volumes, i)
                labels = events or ["baseline"]
                if events:
                    stats["events"] += len(events)
                else:
                    stats["baseline_observations"] += 1
                forward = {h: _forward(closes, highs, lows, i, bars) for h, bars in horizons.items()}
                forward = {h: v for h, v in forward.items() if v}
                stats["observations"] += 1
                in_validation = i >= split
                if in_validation:
                    stats["validation_observations"] += 1
                row = {
                    "record_type": "market_research",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": int(getattr(candles[i], "open_time", 0)),
                    "market_regime": regime,
                    "events": labels,
                    "features_before": {k: float(v) for k, v in f.items() if isinstance(v, (int, float)) and math.isfinite(float(v))},
                    "forward": forward,
                    "phase": "validation" if in_validation else "discovery",
                    "future_leakage": False,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                for label in labels:
                    key = f"{label}|{timeframe}|{regime}"
                    agg = aggregates[key]
                    agg["count"] += 1
                    if in_validation:
                        agg["validation_count"] += 1
                    for horizon, result in forward.items():
                        bucket = agg["validation"] if in_validation else agg["discovery"]
                        bucket[horizon].append(result["return"])
                        agg["all"][horizon].append(result["return"])

    hypotheses: dict[str, Any] = {}
    p_candidates: list[tuple[str, float]] = []
    for key, agg in aggregates.items():
        if agg["count"] < min_samples:
            continue
        horizons: dict[str, Any] = {}
        for horizon, values in agg["discovery"].items():
            if len(values) < min_samples:
                continue
            s = _summary(values)
            t = abs(float(s.get("t_stat", 0.0)))
            p = math.erfc(t / math.sqrt(2.0)) if t else 1.0
            val = _summary(agg["validation"].get(horizon, []))
            horizons[horizon] = {"discovery": s, "validation": val, "discovery_p": p}
            if horizon == next(iter(horizons)):
                p_candidates.append((key, p))
        if horizons:
            hypotheses[key] = {
                "observations": agg["count"],
                "validation_observations": agg["validation_count"],
                "horizons": horizons,
                "status": "candidate",
                "oos_required": True,
                "research_note": "Association only until walk-forward/OOS stability is demonstrated.",
            }

    qvalues = _fdr_qvalues(p_candidates)
    confirmed = 0
    for key, item in hypotheses.items():
        q = qvalues.get(key, 1.0)
        item["fdr_q"] = q
        primary = next(iter(item["horizons"].values()), {})
        val = primary.get("validation", {})
        discovery_mean = float(primary.get("discovery", {}).get("mean", 0.0))
        validation_mean = float(val.get("mean", 0.0))
        stable_sign = discovery_mean == 0 or validation_mean == 0 or (discovery_mean > 0) == (validation_mean > 0)
        enough_oos = int(val.get("samples", 0)) >= min_samples
        if q <= 0.10 and enough_oos and stable_sign:
            item["status"] = "provisional_confirmed"
            confirmed += 1
        elif enough_oos:
            item["status"] = "oos_unconfirmed"

    hypotheses_output.parent.mkdir(parents=True, exist_ok=True)
    hypotheses_output.write_text(
        json.dumps({
            "updated": datetime.now(tz=UTC).isoformat(),
            "method": "walk_forward_discovery_oos_fdr",
            "stats": stats,
            "confirmed_candidates": confirmed,
            "hypotheses": hypotheses,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return stats
