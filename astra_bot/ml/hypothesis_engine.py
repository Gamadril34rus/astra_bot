"""Hypothesis Engine: полный lifecycle торговых гипотез (TZ §9–§11).

Статусы и допустимые переходы:

    DISCOVERED  → TESTING            (есть данные, начинаем проверку)
    TESTING     → VALIDATED          (есть train + validation + OOS +
                                      walk-forward + stress, sample size
                                      достаточный — TZ §11)
    TESTING     → INVALIDATED        (проверка не подтвердила)
    TESTING     → DISCOVERED         (недостаточно данных, откат)
    VALIDATED   → ACTIVE             (допущена к live-учту)
    ACTIVE      → WEAKENING          (live-статистика ухудшилась)
    WEAKENING   → ACTIVE             (восстановление на live-данных)
    WEAKENING   → INVALIDATED        (исчезновение преимущества доказано)
    INVALIDATED → RETIRED            (окончательный вывод; история не
                                      удаляется — TZ §10)

Правила:
- VALIDATED не выдаётся «на нескольких прибыльных сделках»: требуются
  метрики по всем периодам и sample size (TZ §11);
- каждый переход пишется в ``status_log`` (история сохраняется);
- ``invalidation_reason`` обязателен для INVALIDATED;
- гипотеза ссылается на стратегию (strategy_id) — live-мониторинг
  деградации использует стратегию как ключ.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ruff: noqa: UP042

class HypothesisStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    TESTING = "TESTING"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    WEAKENING = "WEAKENING"
    INVALIDATED = "INVALIDATED"
    RETIRED = "RETIRED"


# Допустимые переходы: статус -> множество допустимых следующих.
ALLOWED_TRANSITIONS: dict[HypothesisStatus, set[HypothesisStatus]] = {
    HypothesisStatus.DISCOVERED: {HypothesisStatus.TESTING},
    HypothesisStatus.TESTING: {
        HypothesisStatus.VALIDATED,
        HypothesisStatus.INVALIDATED,
        HypothesisStatus.DISCOVERED,
    },
    HypothesisStatus.VALIDATED: {HypothesisStatus.ACTIVE, HypothesisStatus.INVALIDATED},
    HypothesisStatus.ACTIVE: {
        HypothesisStatus.WEAKENING,
        HypothesisStatus.INVALIDATED,
        HypothesisStatus.RETIRED,
    },
    HypothesisStatus.WEAKENING: {
        HypothesisStatus.ACTIVE,
        HypothesisStatus.INVALIDATED,
        HypothesisStatus.RETIRED,
    },
    HypothesisStatus.INVALIDATED: {HypothesisStatus.RETIRED},
    HypothesisStatus.RETIRED: set(),
}

# Периоды, обязательные для VALIDATED (TZ §11).
REQUIRED_PERIODS = ("train", "validation", "oos", "walk_forward")


@dataclass
class Hypothesis:
    id: str
    created_at: str
    updated_at: str
    description: str
    strategy_id: str = ""
    features: dict[str, Any] = field(default_factory=dict)
    conditions: dict[str, Any] = field(default_factory=dict)
    symbols: list[str] = field(default_factory=list)
    timeframes: list[str] = field(default_factory=list)
    market_regimes: list[str] = field(default_factory=list)
    sample_size: int = 0
    train_metrics: dict[str, float] = field(default_factory=dict)
    validation_metrics: dict[str, float] = field(default_factory=dict)
    oos_metrics: dict[str, float] = field(default_factory=dict)
    walk_forward_metrics: dict[str, float] = field(default_factory=dict)
    stress_metrics: dict[str, Any] = field(default_factory=dict)
    expectancy: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    confidence: float = 0.0
    status: HypothesisStatus = HypothesisStatus.DISCOVERED
    parent_hypothesis: str | None = None
    version: int = 1
    invalidation_reason: str | None = None
    status_log: list[dict[str, str]] = field(default_factory=list)

    def _log(self, new_status: HypothesisStatus, reason: str = "") -> None:
        self.status = new_status
        self.updated_at = datetime.now(UTC).isoformat()
        self.status_log.append(
            {"at": self.updated_at, "status": new_status.value, "reason": reason}
        )

    def transition(
        self, new_status: HypothesisStatus, reason: str = "", min_samples: int = 20
    ) -> tuple[bool, str]:
        """Попробовать переход. (ok, причина отказа)."""
        if new_status not in ALLOWED_TRANSITIONS[self.status]:
            return False, f"переход {self.status.value} -> {new_status.value} запрещён"
        if new_status is HypothesisStatus.VALIDATED:
            ok, why = self._validate_requirements(min_samples)
            if not ok:
                return False, why
        if new_status is HypothesisStatus.INVALIDATED and not reason:
            return False, "INVALIDATED требует invalidation_reason"
        self._log(new_status, reason)
        if new_status is HypothesisStatus.INVALIDATED:
            self.invalidation_reason = reason
        return True, ""

    def _validate_requirements(self, min_samples: int) -> tuple[bool, str]:
        """TZ §11: VALIDATED только при полном наборе доказательств."""
        if self.sample_size < min_samples:
            return False, (
                f"sample_size {self.sample_size} < min_samples {min_samples}"
            )
        for period in REQUIRED_PERIODS:
            metrics = getattr(self, f"{period}_metrics")
            if not metrics:
                return False, f"нет метрик периода {period}"
        if not self.stress_metrics:
            return False, "нет stress test метрик (fees/slippage/perturbation)"
        for period in REQUIRED_PERIODS:
            metrics = getattr(self, f"{period}_metrics")
            if float(metrics.get("expectancy", 0.0)) <= 0:
                return False, f"expectancy <= 0 в периоде {period}"
        return True, ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hypothesis:
        data = dict(data)
        data["status"] = HypothesisStatus(data.get("status", "DISCOVERED"))
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class HypothesisStore:
    """Персистентное хранилище гипотез: ``models/research/hypotheses.json``.

    Атомарная запись (tmp+rename). История статусов — внутри записи;
    записи никогда не удаляются (TZ §10): RETIRED остаётся в файле.
    """

    def __init__(self, path: Path = Path("models/research/hypotheses.json")) -> None:
        self.path = path
        self.hypotheses: dict[str, Hypothesis] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for hid, row in (data.get("hypotheses") or {}).items():
                self.hypotheses[hid] = Hypothesis.from_dict(row)
        except Exception as exc:
            logger.warning("Не загрузил hypotheses: %s", exc)
            self.hypotheses = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "updated": datetime.now(UTC).isoformat(),
                    "hypotheses": {
                        hid: h.to_dict() for hid, h in self.hypotheses.items()
                    },
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ------------------------------------------------------------ access
    def get(self, hid: str) -> Hypothesis | None:
        return self.hypotheses.get(hid)

    def add(self, hyp: Hypothesis) -> None:
        if hyp.id in self.hypotheses:
            raise ValueError(f"гипотеза {hyp.id} уже существует")
        self.hypotheses[hyp.id] = hyp
        self.save()

    def for_strategy(self, strategy_id: str) -> list[Hypothesis]:
        return [h for h in self.hypotheses.values() if h.strategy_id == strategy_id]

    def active_for(self, strategy_id: str) -> Hypothesis | None:
        """Лучшая (по confidence) ACTIVE-гипотеза стратегии."""
        actives = [
            h for h in self.for_strategy(strategy_id)
            if h.status is HypothesisStatus.ACTIVE
        ]
        if not actives:
            return None
        return max(actives, key=lambda h: h.confidence)

    # ------------------------------------------------------------ mutations
    def transition(
        self, hid: str, new_status: HypothesisStatus, reason: str = "",
        min_samples: int = 20,
    ) -> tuple[bool, str]:
        hyp = self.hypotheses.get(hid)
        if hyp is None:
            return False, f"гипотеза {hid} не найдена"
        ok, why = hyp.transition(new_status, reason, min_samples=min_samples)
        if ok:
            self.save()
        return ok, why

    # ------------------------------------------------------------ live monitor
    def check_live_degradation(
        self,
        strategy_id: str,
        live_expectancy: float,
        live_samples: int,
        weakening_ratio: float = 0.5,
        min_live_samples: int = 20,
    ) -> list[str]:
        """Live-мониторинг (TZ §31): ACTIVE → WEAKENING при деградации.

        Критерий: live-выборки достаточно (>= min_live_samples) и live
        expectancy < weakening_ratio × валидированного expectancy
        (или live expectancy < 0 при валидированном > 0).
        Возвращает id гипотез, переведённых в WEAKENING.
        """
        demoted: list[str] = []
        for hyp in self.for_strategy(strategy_id):
            if hyp.status is not HypothesisStatus.ACTIVE:
                continue
            if live_samples < min_live_samples:
                continue
            validated = hyp.expectancy
            degraded = (
                (validated > 0 and live_expectancy < weakening_ratio * validated)
                or (validated > 0 and live_expectancy < 0)
            )
            if degraded:
                ok, _ = hyp.transition(
                    HypothesisStatus.WEAKENING,
                    reason=(
                        f"live expectancy {live_expectancy:.3f}R при "
                        f"validated {validated:.3f}R (n={live_samples})"
                    ),
                )
                if ok:
                    demoted.append(hyp.id)
        if demoted:
            self.save()
        return demoted


def make_hypothesis_id(strategy_id: str, seed: str = "") -> str:
    import hashlib

    raw = f"{strategy_id}|{seed or 'v1'}|{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    return "hyp-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def new_hypothesis(
    *,
    id: str,
    description: str,
    strategy_id: str = "",
    **kwargs: Any,
) -> Hypothesis:
    now = datetime.now(UTC).isoformat()
    hyp = Hypothesis(
        id=id,
        created_at=now,
        updated_at=now,
        description=description,
        strategy_id=strategy_id,
        **kwargs,
    )
    hyp._log(HypothesisStatus.DISCOVERED, "создана")
    return hyp
