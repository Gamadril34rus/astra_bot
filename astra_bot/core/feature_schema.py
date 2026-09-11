"""Canonical ML feature schema and version (TZ P1.6).

Paper DecisionPipeline uses ``astra_bot.decision.feature_engine.Features``.
Any trainer that consumes those vectors must pin ``FEATURE_SCHEMA_VERSION``.
A model trained on another version is rejected at load (fail-closed).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

FEATURE_SCHEMA_VERSION = "1.0.0"

# Stable order of numeric/categorical fields from Features (excluding ``raw``).
CANONICAL_FEATURE_NAMES: tuple[str, ...] = (
    "ema20",
    "ema50",
    "ema200",
    "adx",
    "trend_alignment",
    "rsi",
    "macd_line",
    "roc",
    "atr_pct",
    "bb_width",
    "realized_vol",
    "volume_ratio",
    "obv_slope",
    "vwap",
    "above_vwap",
    "htf_trend",
    "mtf_regime",
    "ltf_structure",
    "news_score",
    "onchain_score",
    "funding",
    "open_interest_change",
    "btc_regime",
    "price",
)


class FeatureSchemaError(ValueError):
    """Train/serve feature schema mismatch."""


def check_feature_schema(
    names: Sequence[str] | None,
    version: str | None,
    *,
    expected_version: str = FEATURE_SCHEMA_VERSION,
) -> None:
    """Raise FeatureSchemaError if version or name-set diverges."""
    if version and version != expected_version:
        raise FeatureSchemaError(
            f"feature schema version mismatch: model={version!r} "
            f"expected={expected_version!r}"
        )
    if names is None:
        return
    missing = [n for n in CANONICAL_FEATURE_NAMES if n not in names]
    extra = [n for n in names if n not in CANONICAL_FEATURE_NAMES and n != "raw"]
    if missing or extra:
        raise FeatureSchemaError(
            f"feature schema names mismatch: missing={missing} extra={extra}"
        )


def feature_vector(features: dict[str, object], order: Iterable[str] | None = None) -> list[float]:
    """Flatten a feature dict into a stable numeric vector."""
    names = tuple(order) if order is not None else CANONICAL_FEATURE_NAMES
    out: list[float] = []
    for name in names:
        value = features.get(name, 0.0)
        if isinstance(value, bool):
            out.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            out.append(float(value))
        elif value is None:
            out.append(0.0)
        else:
            out.append(0.0)
    return out
