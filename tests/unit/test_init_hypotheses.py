"""Миграция legacy research-гипотез (scripts/init_hypotheses.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from init_hypotheses import migrate


def _legacy_file(root: Path, name: str = "research_hypotheses.json") -> None:
    data = {
        "stats": {},
        "hypotheses": {
            "breakout_up|1h|trend": {
                "observations": 120,
                "confidence": 0.5,
                "status": "candidate",
                "horizons": {
                    "1h": {
                        "samples": 118,
                        "mean_return": 0.0021,
                        "positive_rate": 0.57,
                    },
                    "7d": {
                        "samples": 90,
                        "mean_return": 0.01,
                        "positive_rate": 0.55,
                    },
                },
            },
            "doji|4h|range": {
                "observations": 5,
                "confidence": 0.01,
                "status": "candidate",
                "horizons": {
                    "1h": {"mean_return": 0.01, "positive_rate": 0.9},
                },
            },
        },
    }
    (root / name).write_text(json.dumps(data), encoding="utf-8")


def test_migration_creates_discovered_hypotheses(tmp_path):
    _legacy_file(tmp_path)
    stats = migrate(tmp_path, min_samples=20)
    assert stats["scanned"] == 2
    assert stats["migrated"] == 1  # выборка 5 < 20 — пропущена
    assert stats["skipped"] == 1

    from astra_bot.ml.hypothesis_engine import (
        HypothesisStatus,
        HypothesisStore,
    )

    store = HypothesisStore(tmp_path / "research" / "hypotheses.json")
    hyps = list(store.hypotheses.values())
    assert len(hyps) == 1
    hyp = hyps[0]
    assert hyp.status is HypothesisStatus.DISCOVERED
    assert hyp.sample_size == 120
    assert hyp.timeframes == ["1h"]
    assert hyp.market_regimes == ["trend"]
    assert hyp.train_metrics["expectancy"] == pytest.approx(0.0021)


def test_migration_idempotent(tmp_path):
    _legacy_file(tmp_path)
    first = migrate(tmp_path, min_samples=20)
    second = migrate(tmp_path, min_samples=20)
    assert first["migrated"] == 1
    assert second["migrated"] == 0
    assert second["skipped"] == 2


def test_migration_nested_legacy_format(tmp_path):
    """Старый вложенный формат: discovery/validation (2021-08 файлы)."""
    data = {
        "hypotheses": {
            "breakout_up|1d|range": {
                "observations": 44,
                "confidence": 0.082,
                "status": "candidate",
                "horizons": {
                    "1d": {
                        "discovery": {
                            "samples": 44, "mean": 0.0141,
                            "positive_rate": 0.5909,
                        },
                        "validation": {"samples": 0},
                        "discovery_p": 0.082,
                    },
                    "7d": {
                        "discovery": {
                            "samples": 40, "mean": 0.069,
                            "positive_rate": 0.795,
                        },
                        "validation": {
                            "samples": 10, "mean": 0.05,
                            "positive_rate": 0.7,
                        },
                    },
                },
            },
        }
    }
    (tmp_path / "research_hypotheses_2021-08_1d.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    stats = migrate(tmp_path, min_samples=20)
    assert stats["migrated"] == 1
    from astra_bot.ml.hypothesis_engine import HypothesisStore

    store = HypothesisStore(tmp_path / "research" / "hypotheses.json")
    hyp = next(iter(store.hypotheses.values()))
    # Короткий горизонт (1d) выбран как primary.
    assert hyp.train_metrics["expectancy"] == 0.0141
    assert hyp.validation_metrics == {}  # validation samples=0
    assert hyp.expectancy == 0.0141
