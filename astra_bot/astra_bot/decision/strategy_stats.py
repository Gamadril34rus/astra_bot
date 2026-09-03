"""Статистика стратегий по (strategy, regime, timeframe) + EV со сжатием.

Master prompt §3.1 / TZ §3: каждая стратегия должна иметь собственную
статистику в каждом рыночном режиме: sample size, win rate, average
win/loss (в R), expectancy, profit factor, MFE/MAE, издержки.

R = первоначальное расстояние входа-стоп (net-измерения: pnl включает
fees/slippage, т.к. broker считает их).

EV с учётом sample size (TZ §6): используется аддитивное bayesian
shrinkage к prior-оценке (EV кандидата, посчитанному на уровне сделки):

    ev_shrunk = w * sample_expectancy + (1 - w) * prior_ev,
    w = n / (n + k),   k = shrinkage_k (по умолчанию 30).

Последствия (консервативно, без «знаний из трёх удачных сделок»):
- n = 0      -> ev = prior, confidence = 0 (данных нет);
- n = 3      -> w = 0.09: три выигрыша почти не двигают оценку;
- n = 30     -> w = 0.5:  оценка наполовину своя;
- n = 300    -> w = 0.91: оценка почти полностью эмпирическая.
confidence = w — доля доверия, которую Meta-Strategy учитывает при
отсечении (см. ``meta_strategy.MetaStrategy``).

Падение выборки по конкретному режиму — обратно на агрегированный бакет
``ANY`` (все режимы, та же стратегия и таймфрейм), затем на prior.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ANY_REGIME = "ANY"


@dataclass
class StrategyRegimeStats:
    """Накопленная статистика одной стратегии в одном режиме."""

    sample_size: int = 0
    wins: int = 0
    losses: int = 0
    sum_r: float = 0.0  # суммарный net-R всех закрытых сделок
    wins_sum_r: float = 0.0
    losses_sum_r: float = 0.0  # отрицательная величина
    sum_mfe_r: float = 0.0
    sum_mae_r: float = 0.0
    sum_fees: float = 0.0
    last_updated: str | None = None

    @property
    def win_rate(self) -> float:
        return self.wins / self.sample_size if self.sample_size else 0.0

    @property
    def avg_win_r(self) -> float:
        return self.wins_sum_r / self.wins if self.wins else 0.0

    @property
    def avg_loss_r(self) -> float:
        return self.losses_sum_r / self.losses if self.losses else 0.0

    @property
    def expectancy_r(self) -> float:
        return self.sum_r / self.sample_size if self.sample_size else 0.0

    @property
    def profit_factor(self) -> float:
        if self.losses_sum_r == 0:
            return float("inf") if self.wins_sum_r > 0 else 0.0
        return self.wins_sum_r / abs(self.losses_sum_r)

    def record(self, r_multiple: float, mfe_r: float = 0.0, mae_r: float = 0.0, fees: float = 0.0) -> None:
        self.sample_size += 1
        self.sum_r += r_multiple
        self.sum_mfe_r += mfe_r
        self.sum_mae_r += mae_r
        self.sum_fees += fees
        if r_multiple > 0:
            self.wins += 1
            self.wins_sum_r += r_multiple
        else:
            self.losses += 1
            self.losses_sum_r += r_multiple
        self.last_updated = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyRegimeStats:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def shrunken_expectancy(
    stats: StrategyRegimeStats | None,
    prior_r: float,
    shrinkage_k: float = 30.0,
) -> tuple[float, float]:
    """EV (в R) со сжатием к prior и confidence.

    Возвращает ``(ev_r, confidence)``. ``confidence`` — доля доверия к
    эмпирической оценке (0 при отсутствии данных, ~1 при большой выборке).
    """
    if stats is None or stats.sample_size == 0:
        return float(prior_r), 0.0
    n = float(stats.sample_size)
    w = n / (n + shrinkage_k)
    ev = w * stats.expectancy_r + (1.0 - w) * float(prior_r)
    return ev, w


class StrategyStatsStore:
    """Персистентное хранилище статистики по бакетам strategy|regime|timeframe.

    Файл JSON (атомарная запись tmp+rename). SQLite-миграция — отдельная
    фаза (runtime persistence); для текущего объёма (единицы бакетов)
    JSON достаточен и совместим с существующим стилём ``models/*.json``.
    """

    def __init__(
        self,
        path: Path = Path("models/strategy_stats.json"),
        shrinkage_k: float = 30.0,
        min_samples: int = 30,
    ) -> None:
        self.path = path
        self.shrinkage_k = shrinkage_k
        self.min_samples = min_samples
        self.buckets: dict[str, StrategyRegimeStats] = {}
        self.load()

    @staticmethod
    def bucket_key(strategy: str, regime: str, timeframe: str) -> str:
        return f"{strategy}|{regime}|{timeframe}"

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key, row in (data.get("buckets") or {}).items():
                self.buckets[key] = StrategyRegimeStats.from_dict(row)
        except Exception as exc:
            logger.warning("Не загрузил strategy_stats: %s", exc)
            self.buckets = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "updated": datetime.now(UTC).isoformat(),
                    "buckets": {k: v.to_dict() for k, v in self.buckets.items()},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ------------------------------------------------------------ access
    def get(
        self,
        strategy: str,
        regime: str,
        timeframe: str,
        regime_axes: str | None = None,
    ) -> StrategyRegimeStats | None:
        """Бакет режима с миграцией A2.

        Порядок: композитный ключ осей (``regime_axes``, Regime 2.0) →
        legacy-ключ режима (старые файлы читаются как раньше) →
        агрегированный ANY → None.
        """
        if regime_axes:
            axes_bucket = self.buckets.get(
                self.bucket_key(strategy, regime_axes, timeframe)
            )
            if axes_bucket and axes_bucket.sample_size > 0:
                return axes_bucket
        bucket = self.buckets.get(self.bucket_key(strategy, regime, timeframe))
        if bucket and bucket.sample_size > 0:
            return bucket
        any_bucket = self.buckets.get(self.bucket_key(strategy, ANY_REGIME, timeframe))
        if any_bucket and any_bucket.sample_size > 0:
            return any_bucket
        return None

    def get_any(self, strategy: str, timeframe: str) -> StrategyRegimeStats | None:
        return self.buckets.get(self.bucket_key(strategy, ANY_REGIME, timeframe))

    def expectancy(
        self,
        strategy: str,
        regime: str,
        timeframe: str,
        prior_r: float,
        regime_axes: str | None = None,
    ) -> tuple[float, float, StrategyRegimeStats | None]:
        """(ev_r, confidence, stats) — с fallback на legacy-ключ и ANY-режим."""
        stats = self.get(strategy, regime, timeframe, regime_axes=regime_axes)
        ev, conf = shrunken_expectancy(stats, prior_r, self.shrinkage_k)
        return ev, conf, stats

    # ------------------------------------------------------------ updates
    def record(
        self,
        *,
        strategy: str,
        regime: str,
        timeframe: str,
        r_multiple: float,
        mfe_r: float = 0.0,
        mae_r: float = 0.0,
        fees: float = 0.0,
        regime_axes: str | None = None,
    ) -> None:
        """Записать закрытую сделку в бакеты режимов и в агрегированный ANY.

        Миграция A2: при наличии композитного ключа осей пишем и в него, и
        в legacy-бакет режима — старые читатели (даунгрейд, отчёты) не
        «голодают», а новые выборки накапливаются по Regime 2.0.
        """
        regimes = (regime_axes, regime, ANY_REGIME) if regime_axes else (regime, ANY_REGIME)
        for key_regime in dict.fromkeys(r for r in regimes if r):
            key = self.bucket_key(strategy, key_regime, timeframe)
            bucket = self.buckets.setdefault(key, StrategyRegimeStats())
            bucket.record(r_multiple, mfe_r, mae_r, fees)
        self.save()

    def to_dict(self) -> dict[str, Any]:
        return {
            "buckets": {k: v.to_dict() for k, v in self.buckets.items()},
            "updated": datetime.now(UTC).isoformat(),
        }
