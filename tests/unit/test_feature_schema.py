"""Canonical feature schema version (TZ P1.6)."""

from __future__ import annotations

import pytest
from astra_bot.core.feature_schema import (
    CANONICAL_FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    FeatureSchemaError,
    check_feature_schema,
    feature_vector,
)
from astra_bot.decision.feature_engine import Features


def test_version_pinned():
    assert FEATURE_SCHEMA_VERSION == "1.0.0"
    assert len(CANONICAL_FEATURE_NAMES) >= 20


def test_features_carry_schema_version():
    feats = Features()
    assert feats.schema_version == FEATURE_SCHEMA_VERSION
    ml = feats.as_ml_dict()
    assert "schema_version" not in ml  # numeric vector stays stable


def test_version_mismatch_fail_closed():
    with pytest.raises(FeatureSchemaError, match="version mismatch"):
        check_feature_schema(None, "0.9.0")


def test_feature_vector_stable_order():
    vec = feature_vector({"rsi": 50.0, "price": 100.0})
    assert len(vec) == len(CANONICAL_FEATURE_NAMES)
    assert vec[CANONICAL_FEATURE_NAMES.index("rsi")] == 50.0
    assert vec[CANONICAL_FEATURE_NAMES.index("price")] == 100.0
