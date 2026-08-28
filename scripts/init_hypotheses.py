#!/usr/bin/env python3
"""Миграция legacy research-гипотез в Hypothesis Engine (TZ §9).

Легаси-формат ``models/research_hypotheses*.json`` (market_research.py)
хранит аггрегаты с единственным статусом ``candidate``. Скрипт
переносит их как DISCOVERED-гипотезы с метриками и метаданными —
дальше lifecycle ведёт Hypothesis Engine.

Идемпотентен: стабильный id от содержимого ключа, существующие записи
не пересоздаются.

Запуск:
    python scripts/init_hypotheses.py [--root models] [--min-samples 20]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.ml.hypothesis_engine import HypothesisStore, new_hypothesis

# Короткие горизонты первыми: ближайший эффект — самый прямой.
HORIZONS_PRIORITY = ("1h", "4h", "1d", "3d", "7d", "30d", "90d")


def _primary_horizon(horizons: dict[str, dict]) -> tuple[str, dict]:
    for label in HORIZONS_PRIORITY:
        if label in horizons:
            return label, horizons[label]
    if horizons:
        label = next(iter(horizons))
        return label, horizons[label]
    return "1h", {}


def _extract_period_metrics(node: dict) -> tuple[dict[str, float], dict[str, float]]:
    """Вернуть (train_metrics, validation_metrics) для узла горизонта.

    Две легаси-форматы:
    - вложенный: {"discovery": {mean, positive_rate, ...}, "validation": {...}}
      (validation может быть {"samples": 0} — пустой период);
    - плоский: {"mean_return": x, "positive_rate": y, ...} (только train).
    """
    if "discovery" in node or "validation" in node:
        disc = node.get("discovery") or {}
        val = node.get("validation") or {}

        def _pack(block: dict) -> dict[str, float]:
            if not int(block.get("samples", 0) or 0):
                return {}
            return {
                "expectancy": float(block.get("mean", 0.0)),
                "positive_rate": float(block.get("positive_rate", 0.0)),
            }

        return _pack(disc), _pack(val)
    if not node:
        return {}, {}
    return {
        "expectancy": float(node.get("mean_return", 0.0)),
        "positive_rate": float(node.get("positive_rate", 0.0)),
    }, {}


def migrate(root: Path, min_samples: int) -> dict[str, int]:
    store = HypothesisStore(root / "research" / "hypotheses.json")
    stats = {"scanned": 0, "migrated": 0, "skipped": 0}
    for path in sorted(root.glob("research_hypotheses*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key, row in (data.get("hypotheses") or {}).items():
            stats["scanned"] += 1
            if int(row.get("observations", 0)) < min_samples:
                stats["skipped"] += 1
                continue
            label, tf, regime = [*key.split("|"), "", ""][:3]
            hid = "hyp-" + hashlib.sha1(key.encode()).hexdigest()[:10]
            if store.get(hid):
                stats["skipped"] += 1
                continue
            horizon, node = _primary_horizon(row.get("horizons") or {})
            train_metrics, validation_metrics = _extract_period_metrics(node)
            hyp = new_hypothesis(
                id=hid,
                description=(
                    f"Legacy research aggregate: {label} | {horizon} | "
                    f"regime={regime or 'ANY'} (source: {path.name})"
                ),
                strategy_id="",
                features={"legacy_key": key, "source_file": path.name},
                conditions={"event": label, "timeframe": tf, "regime": regime},
                timeframes=[tf] if tf else [],
                market_regimes=[regime] if regime else [],
                sample_size=int(row.get("observations", 0)),
                train_metrics=train_metrics,
                validation_metrics=validation_metrics,
                oos_metrics={},
                expectancy=float(train_metrics.get("expectancy", 0.0)),
                win_rate=float(train_metrics.get("positive_rate", 0.0)),
                confidence=float(row.get("confidence", 0.0)),
            )
            store.add(hyp)
            stats["migrated"] += 1
    store.save()
    return stats


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="models")
    p.add_argument("--min-samples", type=int, default=20)
    args = p.parse_args()
    stats = migrate(Path(args.root), args.min_samples)
    print(f"[{datetime.now(UTC).isoformat()}] migration: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
