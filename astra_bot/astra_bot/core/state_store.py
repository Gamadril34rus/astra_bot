"""Единый слой персистентности состояния — StateStore (Этап 3).

Один state-бандл ``<state_dir>/state_bundle.json`` собирает снимки
всех значимых состояний:

- paper: broker state (equity/realized_pnl/initial_capital + открытые
  позиции) — тот же формат, что пишет PaperBroker;
- risk: risk_state, trading_enabled, дневной/недельный PnL,
  high water mark, открытые позиции;
- readiness: путь к readiness-файлу + последний score/ready;
- champions: production-модель из Model Registry + ACTIVE-гипотезы;
- memory: пути research/lesson-файлов (pointer + размер, не данные —
  данные живут в своих append-only хранилищах).

Семантика:
- атомарная запись: временный файл + ``os.replace`` (частичного бандла
  на диске не существует);
- версия схемы: ``schema_version``; бандл с НЕИЗВЕСТНОЙ (высшей)
  версией НЕ грузится — fail-closed, компонентные файлы остаются
  источником истины;
- компонентные файлы (paper_positions.json, paper_trades.jsonl, ...)
  остаются source of truth; бандл — checkpoint для crash-восстановления:
  если компонентный файл утерян/повреждён, состояние восстанавливается
  из последнего бандла (``restore_broker``).

GitHub Actions: файл попадает в CI save-state как обычный JSON.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BUNDLE_PATH = Path("models/state_bundle.json")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class StateBundle:
    schema_version: int
    saved_at: str
    paper: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    readiness: dict[str, Any] = field(default_factory=dict)
    champions: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateBundle:
        return cls(
            schema_version=int(data.get("schema_version", 0)),
            saved_at=str(data.get("saved_at", "")),
            paper=dict(data.get("paper") or {}),
            risk=dict(data.get("risk") or {}),
            readiness=dict(data.get("readiness") or {}),
            champions=dict(data.get("champions") or {}),
            memory=dict(data.get("memory") or {}),
        )


class StateStore:
    """Атомарный checkpoint-снимок состояния системы."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path = DEFAULT_BUNDLE_PATH) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------ snapshot
    def snapshot(
        self,
        *,
        broker: Any = None,
        risk: Any = None,
        readiness_info: dict[str, Any] | None = None,
        registry: Any = None,
        hypotheses: Any = None,
    ) -> StateBundle:
        """Собрать бандл из компонентов (то, что передано; остальное — пустые)."""
        paper: dict[str, Any] = {}
        if broker is not None:
            paper = {
                "broker_state": {
                    "positions": [
                        {
                            **broker_state_dict(p),
                        }
                        for p in broker.positions
                    ],
                    "realized_pnl": str(broker.realized_pnl),
                    "initial_capital": str(broker.initial_capital),
                },
                "equity": str(broker.equity),
                "open_positions": len(broker.positions),
            }
        risk_dict: dict[str, Any] = {}
        if risk is not None:
            risk_dict = {
                "risk_state": risk.risk_state.value,
                "trading_enabled": bool(risk.trading_enabled),
                "daily_pnl": str(risk.daily_pnl),
                "weekly_pnl": str(risk.weekly_pnl),
                "high_water_mark": str(getattr(risk, "_high_water_mark", 0)),
                "current_equity": str(getattr(risk, "_current_equity", 0)),
                "open_positions": len(risk.get_open_positions()),
            }
        readiness = dict(readiness_info or {})
        champions: dict[str, Any] = {}
        if registry is not None:
            try:
                prod = registry.get_production_model()
                champions["production_model"] = prod.version if prod else None
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("champions registry: %s", exc)
        if hypotheses is not None:
            try:
                from ..ml.hypothesis_engine import HypothesisStatus

                champions["active_hypotheses"] = sum(
                    1
                    for h in hypotheses.hypotheses.values()
                    if h.status is HypothesisStatus.ACTIVE
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("champions hypotheses: %s", exc)
        return StateBundle(
            schema_version=self.SCHEMA_VERSION,
            saved_at=_now_iso(),
            paper=paper,
            risk=risk_dict,
            readiness=readiness,
            champions=champions,
            memory=self._memory_pointers(),
        )

    def _memory_pointers(self) -> dict[str, Any]:
        """Пути research/lesson-файлов + размер (строки) — только указатели."""
        pointers: dict[str, Any] = {}
        base = self.path.parent
        for rel in (
            "no_trade_observations.jsonl",
            "live_lessons.jsonl",
            "lessons.jsonl",
            "paper_trades.jsonl",
            "strategy_stats.json",
            "research/hypotheses.json",
            "research/observations.jsonl",
        ):
            p = base / rel
            if p.exists():
                try:
                    size = (
                        p.stat().st_size
                        if p.suffix != ".jsonl"
                        else sum(1 for line in p.open("r", encoding="utf-8") if line.strip())
                    )
                    pointers[rel] = size
                except OSError:  # pragma: no cover - defensive
                    pointers[rel] = None
        return pointers

    # ------------------------------------------------------------ save/load
    def save(self, bundle: StateBundle) -> None:
        """Атомарная запись: tmp-файл + os.replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(bundle.to_dict(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def load(self) -> StateBundle | None:
        """Чтение бандла. Повреждённый/неизвестной версии → None (fail-closed)."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("state_bundle повреждён, игнорируем: %s", exc)
            return None
        version = int(data.get("schema_version", 0))
        if version > self.SCHEMA_VERSION:
            logger.warning(
                "state_bundle версии %s новее поддерживаемой %s — "
                "не грузим (fail-closed)",
                version,
                self.SCHEMA_VERSION,
            )
            return None
        return StateBundle.from_dict(data)

    # ------------------------------------------------------------ restore
    def restore_broker(self, broker: Any, bundle: StateBundle) -> bool:
        """Восстановить broker из бандла, ЕСЛИ компонентного файла нет.

        Компонентный файл (paper_positions.json) — source of truth;
        восстанавливаем только при его отсутствии/пустоте (crash recovery
        между CI-сессиями, потеря артефакта).
        """
        if broker.state_path.exists():
            try:
                # Валидный компонентный файл — source of truth (даже
                # пустой: это законченное состояние, а не потеря).
                json.loads(broker.state_path.read_text(encoding="utf-8"))
                return False
            except Exception:
                pass  # повреждённый файл — восстанавливаем из бандла
        state = (bundle.paper or {}).get("broker_state")
        if not state:
            return False
        from ..decision.broker import PaperPosition

        broker.state_path.parent.mkdir(parents=True, exist_ok=True)
        positions: list[PaperPosition] = []
        for p in state.get("positions", []):
            try:
                positions.append(PaperPosition(**p))
            except Exception as exc:
                logger.warning("Позиция из бандла не восстановлена: %s", exc)
        from decimal import Decimal

        if state.get("realized_pnl"):
            broker.realized_pnl = Decimal(str(state["realized_pnl"]))
        if state.get("initial_capital"):
            broker.initial_capital = Decimal(str(state["initial_capital"]))
        broker.positions = positions
        for pos in broker.positions:
            pos.entry_price = Decimal(str(pos.entry_price))
            pos.quantity = Decimal(str(pos.quantity))
            pos.stop_loss = Decimal(str(pos.stop_loss))
            pos.take_profits = [Decimal(str(x)) for x in pos.take_profits]
            if pos.highest_price is not None:
                pos.highest_price = Decimal(str(pos.highest_price))
            if pos.lowest_price is not None:
                pos.lowest_price = Decimal(str(pos.lowest_price))
        broker.save()
        logger.warning(
            "Broker state восстановлен из state_bundle: позиций=%d, "
            "realized_pnl=%s",
            len(positions),
            broker.realized_pnl,
        )
        return True


def broker_state_dict(pos: Any) -> dict[str, Any]:
    """Формат позиции бандла = формату сохранения PaperBroker (str Decimal)."""
    from dataclasses import asdict

    d = asdict(pos)
    d["entry_price"] = str(pos.entry_price)
    d["quantity"] = str(pos.quantity)
    d["stop_loss"] = str(pos.stop_loss)
    d["take_profits"] = [str(x) for x in pos.take_profits]
    d["initial_quantity"] = str(pos.initial_quantity)
    d["highest_price"] = str(pos.highest_price) if pos.highest_price else None
    d["lowest_price"] = str(pos.lowest_price) if pos.lowest_price else None
    d["fill_price"] = str(pos.fill_price) if pos.fill_price is not None else None
    d["entry_fee_per_unit"] = str(pos.entry_fee_per_unit)
    d["risk_distance"] = str(pos.risk_distance)
    d["regime"] = pos.regime
    d["timeframe"] = pos.timeframe
    return d
