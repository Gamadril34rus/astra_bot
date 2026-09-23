"""Калибровка p_win: confidence стратегии ≠ вероятность выигрыша.

Sprint «Зевс-Активация» 2026-09-23: live WR ≈ 20%, а confidence 0.5–0.95
завышал EV и пропускал сделки через LOW_EV-гейт.
"""

from __future__ import annotations

from typing import Any

# Консервативные priors по семейству (ниже 0.5 — ближе к live).
PRIORS: dict[str, float] = {
    "momentum": 0.35,
    "mean_reversion": 0.32,
    "pattern": 0.38,
    "trend": 0.36,
    "zeus": 0.40,
    "scalp": 0.28,
    "breakout": 0.30,
}


def _strategy_type(strategy: str) -> str:
    s = (strategy or "").lower()
    for key in ("zeus", "scalp", "momentum", "mean_reversion", "breakout", "trend", "pattern"):
        if key in s:
            return key
    for frag in (
        "triangle",
        "wedge",
        "flag",
        "pennant",
        "diamond",
        "cup",
        "head_shoulder",
        "rectangle",
        "double",
        "triple",
        "rounded",
    ):
        if frag in s:
            return "pattern"
    return "pattern"


def calibrate_p_win(
    strategy: str,
    confidence: float,
    stats_store: Any = None,
    regime: str = "",
    min_samples: int = 30,
) -> float:
    """Реальный WR из stats при n>=min_samples, иначе Bayesian blend prior×confidence."""
    strategy_type = _strategy_type(strategy)
    prior = PRIORS.get(strategy_type, 0.35)

    if stats_store is not None:
        try:
            bucket = None
            if hasattr(stats_store, "get_bucket"):
                bucket = stats_store.get_bucket(strategy, regime or "UNKNOWN")
            if bucket is not None:
                n = int(getattr(bucket, "sample_size", 0) or getattr(bucket, "n", 0) or 0)
                wins = int(getattr(bucket, "wins", 0) or 0)
                if n >= min_samples and n > 0:
                    return max(0.05, min(0.95, wins / n))
        except Exception:
            pass

    conf = max(0.25, min(0.55, float(confidence or 0.4)))
    blended = 0.7 * prior + 0.3 * conf
    return max(0.05, min(0.55, blended))
