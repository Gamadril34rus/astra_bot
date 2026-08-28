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
- ``models/no_trade_outcomes.json`` — мутабельный индекс исходов
  (id -> horizons), режется до 30 дней.

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

logger = logging.getLogger(__name__)

DEFAULT_OBSERVATIONS = Path("models/no_trade_observations.jsonl")
DEFAULT_OUTCOMES = Path("models/no_trade_outcomes.json")
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
        self.outcomes_path = outcomes_path
        self.horizons = horizons
        self._known_ids: set[str] = set()
        self._load_known_ids()
        self._outcomes: dict[str, dict[str, Any]] = self._load_outcomes()

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
        if not self.outcomes_path.exists():
            return {}
        try:
            data = json.loads(self.outcomes_path.read_text(encoding="utf-8"))
            return data.get("outcomes", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_outcomes(self) -> None:
        self.outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.outcomes_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"updated": datetime.now(UTC).isoformat(), "outcomes": self._outcomes},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.outcomes_path)

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
                    if obs.id in self._outcomes:
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
        """
        enriched: list[NoTradeObservation] = []
        changed = False
        for obs in self.pending():
            candles = candles_by_symbol.get(obs.symbol) or []
            result = _forward_outcome(candles, obs.bar_time, self.horizons)
            if result is None:
                continue
            self._outcomes[obs.id] = {
                "bar_time": obs.bar_time,
                "symbol": obs.symbol,
                "reason_code": obs.reason_code,
                "market_regime": obs.market_regime,
                "horizons": result,
                "computed_at": datetime.now(UTC).isoformat(),
            }
            enriched.append(obs)
            changed = True
        if changed:
            self._prune_outcomes()
            self._save_outcomes()
        return enriched

    def _prune_outcomes(self) -> None:
        # bar_time — open_time свечи (секунды), сравниваем в секундах.
        cutoff = datetime.now(UTC).timestamp() - OUTCOMES_KEEP_DAYS * 86_400
        keep = {
            obs_id: row
            for obs_id, row in self._outcomes.items()
            if row.get("bar_time", 0) >= cutoff
        }
        if len(keep) != len(self._outcomes):
            self._outcomes = keep


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
