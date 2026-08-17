"""Research-first market discovery engine.

This module deliberately separates market research from trading. It records what
was observable at time t and evaluates what happened afterwards without leaking
future values into the observation. It is designed for historical OHLCV data
and can be extended with derivatives, cross-asset and event features.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import mean, pstdev
from typing import Any, Iterable


HORIZONS = (5, 15, 60, 240, 1440)


@dataclass(slots=True)
class ResearchObservation:
    symbol: str
    timeframe: str
    timestamp: Any
    market_regime: str
    events: list[str]
    features_before: dict[str, float]
    forward_returns: dict[str, float]
    forward_max_up: dict[str, float]
    forward_max_down: dict[str, float]
    volatility_response: dict[str, float]
    hypothesis: str
    sample_count: int = 1
    confidence: float = 0.0
    conclusion: str = ""
    applicability: str = ""
    out_of_sample_status: str = "pending"
    record_type: str = "market_research"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ret(a: float, b: float) -> float:
    return (b / a - 1.0) if a else 0.0


def _regime(closes: list[float], volumes: list[float]) -> str:
    if len(closes) < 50:
        return "unknown"
    base = closes[-50]
    change = _ret(base, closes[-1])
    hi = max(closes[-50:])
    lo = min(closes[-50:])
    rng = _ret(lo, hi)
    if rng and abs(change) > rng * 0.55:
        return "trend_up" if change > 0 else "trend_down"
    if rng > 0.12:
        return "high_volatility_range"
    return "range"


def detect_events(closes: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> list[str]:
    """Detect only events knowable at the current bar."""
    if len(closes) < 30:
        return []
    events: list[str] = []
    c = closes[-1]
    prev_high = max(highs[-21:-1])
    prev_low = min(lows[-21:-1])
    if c > prev_high:
        events.append("breakout_up")
    if c < prev_low:
        events.append("breakout_down")
    body = abs(closes[-1] - closes[-2])
    candle_range = max(highs[-1] - lows[-1], 1e-12)
    if body / candle_range < 0.15:
        events.append("indecision_candle")
    if closes[-1] > closes[-2] and closes[-2] < closes[-3]:
        events.append("short_term_reversal_up")
    if closes[-1] < closes[-2] and closes[-2] > closes[-3]:
        events.append("short_term_reversal_down")
    if len(volumes) >= 21:
        avg = mean(volumes[-21:-1])
        if avg and volumes[-1] / avg >= 2.0:
            events.append("volume_spike")
    return events


def _hypothesis(events: list[str], regime: str) -> str:
    if not events:
        return "No discrete event: measure baseline conditional returns for the current market regime."
    return f"After {', '.join(events)} in {regime}, estimate the distribution of forward price and volatility responses."


def _confidence(samples: int, dispersion: float) -> float:
    # Conservative confidence proxy, not a p-value. Statistical significance must
    # be evaluated by the downstream validator on independent data.
    coverage = min(1.0, samples / 100.0)
    stability = 1.0 / (1.0 + max(0.0, dispersion) * 10.0)
    return round(coverage * stability, 4)


def observe(
    symbol: str,
    timeframe: str,
    candles: list[Any],
    index: int,
    horizons: Iterable[int] = HORIZONS,
    features_before: dict[str, float] | None = None,
) -> ResearchObservation | None:
    """Create one causal observation at ``index``.

    ``candles[:index+1]`` is the complete information set available at t.
    Future candles are used only to score the consequences and never enter
    ``features_before`` or event detection.
    """
    if index < 30 or index >= len(candles) - 1:
        return None
    before = candles[: index + 1]
    closes = [float(x.close) for x in before]
    highs = [float(x.high) for x in before]
    lows = [float(x.low) for x in before]
    volumes = [float(getattr(x, "volume", 0.0) or 0.0) for x in before]
    events = detect_events(closes, highs, lows, volumes)
    regime = _regime(closes, volumes)
    fwd: dict[str, float] = {}
    max_up: dict[str, float] = {}
    max_down: dict[str, float] = {}
    vol_response: dict[str, float] = {}
    entry = closes[-1]
    for h in horizons:
        future = candles[index + 1 : index + 1 + h]
        if not future:
            continue
        future_closes = [float(x.close) for x in future]
        future_highs = [float(x.high) for x in future]
        future_lows = [float(x.low) for x in future]
        fwd[str(h)] = _ret(entry, future_closes[-1])
        max_up[str(h)] = _ret(entry, max(future_highs))
        max_down[str(h)] = _ret(entry, min(future_lows))
        if len(future_closes) > 1:
            returns = [_ret(future_closes[i - 1], future_closes[i]) for i in range(1, len(future_closes))]
            vol_response[str(h)] = pstdev(returns) if len(returns) > 1 else 0.0
    if not fwd:
        return None
    return ResearchObservation(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=before[-1].open_time,
        market_regime=regime,
        events=events,
        features_before=dict(features_before or {}),
        forward_returns=fwd,
        forward_max_up=max_up,
        forward_max_down=max_down,
        volatility_response=vol_response,
        hypothesis=_hypothesis(events, regime),
        confidence=_confidence(1, vol_response.get("60", 0.0)),
        conclusion="pending aggregation",
        applicability=f"regime={regime}; events={','.join(events) or 'baseline'}",
    )


def aggregate(observations: Iterable[ResearchObservation]) -> list[dict[str, Any]]:
    """Aggregate observations by event/regime/timeframe without claiming causality."""
    groups: dict[tuple[str, str, str], list[ResearchObservation]] = {}
    for obs in observations:
        key = ("+".join(sorted(obs.events)) or "baseline", obs.market_regime, obs.timeframe)
        groups.setdefault(key, []).append(obs)
    result: list[dict[str, Any]] = []
    for (event, regime, timeframe), rows in groups.items():
        returns = [r.forward_returns["60"] for r in rows if "60" in r.forward_returns]
        if not returns:
            continue
        positive = sum(x > 0 for x in returns) / len(returns)
        avg = mean(returns)
        result.append({
            "record_type": "research_hypothesis",
            "event": event,
            "market_regime": regime,
            "timeframe": timeframe,
            "samples": len(returns),
            "positive_rate_1h": round(positive, 6),
            "mean_return_1h": round(avg, 8),
            "dispersion_1h": round(pstdev(returns), 8) if len(returns) > 1 else 0.0,
            "status": "candidate" if len(returns) >= 30 else "insufficient_sample",
            "warning": "association only; require out-of-sample validation before use",
        })
    return result
