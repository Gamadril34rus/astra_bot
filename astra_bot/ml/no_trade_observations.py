"""NO_TRADE observations: отказ от сделки — тоже результат модели.

Master prompt §3 / TZ §12-13: каждый evaluation cycle фиксируется как
TRADE (сделка в paper_trades.jsonl) или NO_TRADE (наблюдение здесь).
Наблюдение не остаётся «навсегда без результата»: по мере появления
будущих баров к нему дописывается исход на горизонтах
1/3/6/12/24 бара — future return, MFE, MAE. Это позволяет отвечать на
вопрос «правильно ли система отказалась от сделки».

Хранение (append-only, idempotent — TZ §30):
- ``models/no_trade_observations.jsonl`` — архив наблюдений, только
  append; повторная обработка того же бара не создаёт дубль (стабильный
  id = sha1(symbol|bar_time|reason|strategy|direction));
- ``models/no_trade_outcomes.jsonl`` — append-once исходы с id; индекс в
  памяти режется до 30 дней (bar_time в мс), файл ограничивается ротацией;
- при отсутствии JSONL читается legacy ``no_trade_outcomes.json``.

Свечи будущего берутся из рыночных данных текущего цикла (движок уже
тянет 200-300 баров) — никакого look-ahead при формировании самого
наблюдения (только состояние на момент t).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core import models
from .no_trade_outcomes import load_outcomes, outcome_sort_key, outcomes_jsonl_path

logger = logging.getLogger(__name__)

DEFAULT_OBSERVATIONS = Path("models/no_trade_observations.jsonl")
DEFAULT_OUTCOMES = Path("models/no_trade_outcomes.jsonl")
DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 6, 12, 24)
OUTCOMES_KEEP_DAYS = 30


@dataclass
class NoTradeObservation:
    id: str
    symbol: str
    bar_time: int  # open_time основного бара на момент решения
    timestamp: int
    market_regime: str
    regime_confidence: float
    reason_code: str
    reasons: list[str] = field(default_factory=list)
    candidate: dict[str, Any] | None = None
    features: dict[str, float] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    # Additive portfolio-gate attribution; absent on legacy rows.
    rejection_stage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "bar_time": self.bar_time,
            "timestamp": self.timestamp,
            "market_regime": self.market_regime,
            "regime_confidence": self.regime_confidence,
            "reason_code": self.reason_code,
            "reasons": self.reasons,
            "candidate": self.candidate,
            "features": self.features,
            "result": self.result,
            "rejection_stage": self.rejection_stage,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NoTradeObservation:
        return cls(
            id=str(data.get("id", "")),
            symbol=str(data.get("symbol", "")),
            bar_time=int(data.get("bar_time", 0)),
            timestamp=int(data.get("timestamp", 0)),
            market_regime=str(data.get("market_regime", "UNKNOWN")),
            regime_confidence=float(data.get("regime_confidence", 0.0)),
            reason_code=str(data.get("reason_code", "NO_VALID_SETUP")),
            reasons=list(data.get("reasons") or []),
            candidate=data.get("candidate"),
            features=dict(data.get("features") or {}),
            result=data.get("result"),
            rejection_stage=data.get("rejection_stage"),
        )


def make_observation_id(
    symbol: str, bar_time: int, reason_code: str, strategy: str, direction: str
) -> str:
    """Стабильный ID: same input -> same ID -> no duplicate (TZ §30)."""
    raw = f"{symbol}|{bar_time}|{reason_code}|{strategy or '-'}|{direction or '-'}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def quick_features(candles: list[models.Candle]) -> dict[str, float]:
    """Компактный срез состояния рынка на момент t (без будущего)."""
    if len(candles) < 25:
        return {}
    closes = [float(c.close) for c in candles]
    last = closes[-1]
    if last <= 0:
        return {}
    prev = closes[-25]
    ranges = [(float(c.high) - float(c.low)) / last * 100 for c in candles[-25:]]
    vols = [float(c.volume) for c in candles]
    avg_vol = sum(vols[-26:-1]) / 25 if len(vols) >= 26 else 0.0
    return {
        "close": round(last, 6),
        "return_24b_pct": round((last / prev - 1.0) * 100.0, 4) if prev else 0.0,
        "atr25_pct": round(sum(ranges) / len(ranges), 4),
        "volume_ratio": round(vols[-1] / avg_vol, 3) if avg_vol > 0 else 0.0,
    }


class NoTradeObservationLog:
    """Append-only журнал NO_TRADE-наблюдений с dedup по стабильному id."""

    def __init__(
        self,
        observations_path: Path = DEFAULT_OBSERVATIONS,
        outcomes_path: Path = DEFAULT_OUTCOMES,
        horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    ) -> None:
        self.observations_path = observations_path
        self.outcomes_path = outcomes_jsonl_path(outcomes_path)
        self.horizons = horizons
        self._known_ids: set[str] = set()
        self._load_known_ids()
        self._outcomes: dict[str, dict[str, Any]] = self._load_outcomes()
        # A fallback-loaded legacy index must survive the first JSONL append and
        # the next process restart. Bootstrap once; never rewrite legacy JSON.
        self._unsaved_outcomes: dict[str, dict[str, Any]] = (
            {} if self.outcomes_path.exists() else dict(self._outcomes)
        )
        self._prune_outcomes()

    # ------------------------------------------------------------ loading
    def _load_known_ids(self) -> None:
        if not self.observations_path.exists():
            return
        try:
            with self.observations_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if row.get("id"):
                            self._known_ids.add(str(row["id"]))
                    except Exception:
                        continue
        except OSError as exc:
            logger.debug("no_trade: не прочитал журнал: %s", exc)

    def _load_outcomes(self) -> dict[str, dict[str, Any]]:
        return load_outcomes(self.outcomes_path)

    def _save_outcomes(self) -> None:
        """Append only new outcomes (plus a one-time legacy bootstrap)."""
        if not self._unsaved_outcomes:
            return
        self.outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        # An interrupted append must not join the next row to a damaged tail.
        needs_newline = False
        if self.outcomes_path.exists() and self.outcomes_path.stat().st_size:
            with self.outcomes_path.open("rb") as tail:
                tail.seek(-1, 2)
                needs_newline = tail.read(1) != b"\n"
        with self.outcomes_path.open("a", encoding="utf-8") as stream:
            if needs_newline:
                stream.write("\n")
            for oid, row in sorted(self._unsaved_outcomes.items(), key=outcome_sort_key):
                stream.write(json.dumps({"id": oid, **row}, ensure_ascii=False) + "\n")
        self._unsaved_outcomes.clear()

    # ------------------------------------------------------------ record
    def add(self, obs: NoTradeObservation) -> bool:
        """Append наблюдение; повторный вызов с тем же id → False (нет дубля)."""
        if obs.id in self._known_ids:
            return False
        self.observations_path.parent.mkdir(parents=True, exist_ok=True)
        with self.observations_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obs.to_dict(), ensure_ascii=False) + "\n")
        self._known_ids.add(obs.id)
        return True

    def pending(self, limit: int = 200) -> list[NoTradeObservation]:
        """Наблюдения, которые ещё могут получить результат (последние)."""
        if not self.observations_path.exists():
            return []
        rows: list[NoTradeObservation] = []
        cutoff_ms = _outcomes_cutoff_ms()
        try:
            with self.observations_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obs = NoTradeObservation.from_dict(json.loads(line))
                    except Exception:
                        continue
                    # Outcomes evicted by rotation/time-pruning must not be
                    # computed again: observation backfill is append-once too.
                    if obs.id in self._outcomes or obs.result is not None or obs.bar_time < cutoff_ms:
                        continue
                    rows.append(obs)
        except OSError:
            return []
        # Самые свежие — последние строки файла.
        return list(reversed(rows))[:limit]

    # ------------------------------------------------------------ enrich
    def enrich(self, candles_by_symbol: dict[str, list[models.Candle]]) -> list[NoTradeObservation]:
        """Дополнить pending-наблюдения будущим исходом по горизонтам.

        Возвращает список наблюдений, к которым результат (полный или
        частичный) записан в индекс исходов. Вызывается из живого
        цикла: будущие бары уже доступны в рыночных данных.

        TZ P0-3: после обогащения обновляет result в JSONL-файле наблюдений
        (backfill_jsonl), чтобы result != null для ≥90% записей старше 24 баров.
        """
        self._prune_outcomes()
        enriched: list[NoTradeObservation] = []
        for obs in self.pending():
            candles = candles_by_symbol.get(obs.symbol) or []
            result = _forward_outcome(candles, obs.bar_time, self.horizons)
            if result is None:
                continue
            outcome_row = {
                "bar_time": obs.bar_time,
                "symbol": obs.symbol,
                "reason_code": obs.reason_code,
                "market_regime": obs.market_regime,
                "horizons": result,
                "computed_at": datetime.now(UTC).isoformat(),
            }
            # PR #78 (часть 3): гипотетический R от geometрии отклонённого
            # кандидата — чистая телеметрия, нужна чтобы оценить эффект гейта
            # «не торгуй в шуме» ДО его включения. Ошибки глотаем: без него
            # обогащение горизонтов должно работать как раньше.
            try:
                hyp = _hypothetic_r(candles, obs.bar_time, obs.candidate, max(self.horizons))
            except Exception:  # pragma: no cover
                hyp = None
            if hyp is not None:
                outcome_row["hypothetic_r"] = hyp
            self._outcomes[obs.id] = outcome_row
            self._unsaved_outcomes[obs.id] = outcome_row
            enriched.append(obs)
        if self._unsaved_outcomes:
            self._save_outcomes()
            # TZ P0-3: backfill result в JSONL.
            self.backfill_jsonl()
        return enriched

    def backfill_jsonl(self) -> int:
        """Переписать JSONL-файл наблюдений, заполняя result из outcomes.

        Возвращает количество обновлённых записей.
        """
        if not self.observations_path.exists() or not self._outcomes:
            return 0
        updated = 0
        tmp_path = self.observations_path.with_suffix(".jsonl.tmp")
        try:
            with self.observations_path.open("r", encoding="utf-8") as fin, \
                 tmp_path.open("w", encoding="utf-8") as fout:
                for line in fin:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        fout.write(line + "\n")
                        continue
                    obs_id = str(row.get("id", ""))
                    if obs_id in self._outcomes:
                        outcome = self._outcomes[obs_id]
                        dirty = False
                        if row.get("result") is None:
                            row["result"] = outcome.get("horizons")
                            dirty = True
                        # аддитивно: гипотетический R для записей гейтов входа
                        hyp = outcome.get("hypothetic_r")
                        if hyp is not None and row.get("hypothetic_r") is None:
                            row["hypothetic_r"] = hyp
                            dirty = True
                        if dirty:
                            updated += 1
                    fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            tmp_path.replace(self.observations_path)
        except OSError as exc:
            logger.debug("no_trade: backfill error: %s", exc)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return updated

    def _prune_outcomes(self) -> None:
        """Bound the in-memory index in ms; on-disk JSONL is rotated, not rewritten."""
        cutoff_ms = _outcomes_cutoff_ms()
        keep: dict[str, dict[str, Any]] = {}
        for oid, row in self._outcomes.items():
            try:
                if int(row.get("bar_time") or 0) >= cutoff_ms:
                    keep[oid] = row
            except (TypeError, ValueError):
                continue
        self._outcomes = keep
        self._unsaved_outcomes = {
            oid: row for oid, row in self._unsaved_outcomes.items() if oid in keep
        }


def _outcomes_cutoff_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000) - OUTCOMES_KEEP_DAYS * 86_400_000


def _hypothetic_r(
    candles: list[models.Candle],
    bar_time: int,
    candidate: dict[str, Any] | None,
    max_bars: int,
) -> dict[str, Any] | None:
    """Гипотетический R отклонённого кандидата (грязто, без комиссий).

    Правила фиксированы и консервативны:
      * отсчёт от close сигнального бара (тот же репер, что и в ``_forward_outcome``);
      * вход предполагается по цене кандидата, т.е. R = |entry − stop|;
      * если в одном баре касаются и стоп, и тейк — засчитывается СТОП (как в
        ``lever_scan``/``edge_scan``);
      * тейк/стоп исполняются ровно на уровне (без гэпа) — это верхняя оценка
        для стопа и нижняя для тейка;
      * если ни один уровень не тронут за ``max_bars`` — выход по close
        последнего бара окна, ``incomplete=True`` если баров ещё нет.

    Возвращает ``None``, если геометрия непригодна (нет стопа/нулевой риск) —
    молча, чтобы не влиять на основной поток обогащения.
    """
    if not candidate:
        return None
    try:
        entry = float(candidate.get("entry_price") or 0.0)
        stop = float(candidate.get("stop_loss") or 0.0)
    except (TypeError, ValueError):
        return None
    if entry <= 0.0 or stop <= 0.0:
        return None
    risk = abs(entry - stop)
    if risk <= 0.0:
        return None
    dirn = -1.0 if str(candidate.get("direction", "long")).lower() == "short" else 1.0
    take = candidate.get("take_profit")
    try:
        take_dist = abs(float(take) - entry) if take else None
    except (TypeError, ValueError):
        take_dist = None
    index = None
    for i, c in enumerate(candles):
        if int(c.open_time) == int(bar_time):
            index = i
            break
    if index is None:
        return None
    end_limit = min(len(candles) - 1, index + max(1, int(max_bars)))
    for k in range(index + 1, end_limit + 1):
        c = candles[k]
        hi = float(c.high)
        lo = float(c.low)
        if dirn > 0:
            if lo <= entry - risk:
                return {"r": -1.0, "exit_kind": "stop", "bars": k - index}
            if take_dist and hi >= entry + take_dist:
                return {"r": round(take_dist / risk, 4), "exit_kind": "take", "bars": k - index}
        else:
            if hi >= entry + risk:
                return {"r": -1.0, "exit_kind": "stop", "bars": k - index}
            if take_dist and lo <= entry - take_dist:
                return {"r": round(take_dist / risk, 4), "exit_kind": "take", "bars": k - index}
    if end_limit <= index:
        return None  # будущего ещё нет — дождёмся следующего цикла
    cl = float(candles[end_limit].close)
    return {
        "r": round((cl - entry) * dirn / risk, 4),
        "exit_kind": "time",
        "bars": end_limit - index,
        "incomplete": end_limit < index + max(1, int(max_bars)),
    }


def _forward_outcome(
    candles: list[models.Candle],
    bar_time: int,
    horizons: tuple[int, ...],
) -> dict[str, Any] | None:
    """Исход на горизонтах от бара bar_time. None — если бара не нашли."""
    index = None
    for i, c in enumerate(candles):
        if int(c.open_time) == int(bar_time):
            index = i
            break
    if index is None:
        return None
    entry = float(candles[index].close)
    if entry <= 0:
        return None
    horizons_out: dict[str, Any] = {}
    for h in horizons:
        end = index + h
        if end >= len(candles):
            break  # будущего ещё мало — дождёмся следующего цикла
        window = candles[index + 1 : end + 1]
        future_closes = [float(c.close) for c in window]
        future_highs = [float(c.high) for c in window]
        future_lows = [float(c.low) for c in window]
        horizons_out[str(h)] = {
            "future_return": round(future_closes[-1] / entry - 1.0, 6),
            "max_up": round(max(future_highs) / entry - 1.0, 6),
            "max_down": round(min(future_lows) / entry - 1.0, 6),
        }
    if not horizons_out:
        return None
    return horizons_out
