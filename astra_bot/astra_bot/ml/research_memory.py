"""Research Memory: типизированные хранилища (TZ §14).

Память разделена по типам (не один JSON):

    OBSERVATIONS  models/research/observations.jsonl   (append-only)
    HYPOTHESES    models/research/hypotheses.json      (HypothesisStore)
    STRATEGIES    models/strategy_stats.json           (StrategyStatsStore)
    LESSONS       models/lessons*.jsonl, live_lessons.jsonl
    MODELS        models/registry.json + vNNN/         (Model Registry, Phase 4)

Каждая запись наблюдений несёт: id, timestamp, type, source, version,
confidence, sample_size (TZ §14). id стабилен (content-hash) — повторная
обработка не создаёт дублей (TZ §30).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RESEARCH_DIR = Path("models/research")
OBSERVATIONS_PATH = RESEARCH_DIR / "observations.jsonl"


def observation_id(
    source: str, symbol: str, bar_time: int, kind: str, features_digest: str
) -> str:
    raw = f"{source}|{symbol}|{bar_time}|{kind}|{features_digest}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


class ResearchMemory:
    """Фасад: запись и чтение наблюдений + ссылки на остальные хранилища."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        observations_path: Path = OBSERVATIONS_PATH,
        hypotheses_path: Path = RESEARCH_DIR / "hypotheses.json",
    ) -> None:
        self.observations_path = observations_path
        from .hypothesis_engine import HypothesisStore

        self.hypotheses = HypothesisStore(hypotheses_path)
        self._known_ids: set[str] = self._load_known_ids()

    # ------------------------------------------------------------ ids
    def _load_known_ids(self) -> set[str]:
        if not self.observations_path.exists():
            return set()
        ids: set[str] = set()
        try:
            with self.observations_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            row = json.loads(line)
                            if row.get("id"):
                                ids.add(str(row["id"]))
                        except Exception:
                            continue
        except OSError:
            return set()
        return ids

    # ------------------------------------------------------------ record
    def record_observation(
        self,
        *,
        source: str,
        symbol: str,
        bar_time: int,
        kind: str,  # research_event / live_no_trade / live_trade / experiment
        features: dict[str, Any],
        forward: dict[str, Any] | None = None,
        confidence: float | None = None,
        sample_size: int = 1,
    ) -> str | None:
        """Append наблюдения; повтор с тем же id → None (нет дубля)."""
        feat_digest = hashlib.sha1(
            json.dumps(features, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        oid = observation_id(source, symbol, bar_time, kind, feat_digest)
        if oid in self._known_ids:
            return None
        row = {
            "id": oid,
            "timestamp": datetime.now(UTC).isoformat(),
            "bar_time": bar_time,
            "type": "market_research_observation",
            "kind": kind,
            "source": source,
            "version": self.SCHEMA_VERSION,
            "symbol": symbol,
            "features": features,
            "forward": forward,
            "confidence": confidence,
            "sample_size": sample_size,
        }
        self.observations_path.parent.mkdir(parents=True, exist_ok=True)
        with self.observations_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._known_ids.add(oid)
        return oid

    def count(self) -> int:
        if not self.observations_path.exists():
            return 0
        with self.observations_path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
