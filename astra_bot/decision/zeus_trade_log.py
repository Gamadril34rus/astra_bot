"""Журнал сделок Зевса: почему вход, где стоп, почему выход, почему НЕ вошли.

Append-only JSONL (paper/research). Не влияет на исполнение.
Путь по умолчанию: models/zeus_trade_journal.jsonl

События:
  clock_start / tick / entry / stop_adjust / exit
  reject          — нет входа + reason + snapshot структуры
  structure_state — снимок клина на тике (есть/нет, границы)
  ltf_impulse     — сильный 15m-импульс (память, не ордер)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PATH = "models/zeus_trade_journal.jsonl"


class ZeusTradeLog:
    """Best-effort журнал. Ошибки записи не ломают пайплайн."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)

    def _write(self, row: dict[str, Any]) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            row.setdefault("ts", int(time.time() * 1000))
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            return True
        except Exception as exc:
            logger.debug("zeus_trade_log write skipped: %s", exc)
            return False

    def entry(
        self,
        *,
        symbol: str,
        direction: str,
        entry_price: float | str,
        stop_loss: float | str,
        take_profit: float | str,
        reason: str,
        features: dict[str, Any] | None = None,
        strategy: str = "zeus_wedge_retest_4h",
    ) -> bool:
        """Запись входа: причина + уровни."""
        return self._write(
            {
                "event": "entry",
                "symbol": symbol,
                "strategy": strategy,
                "direction": direction,
                "entry_price": str(entry_price),
                "stop_loss": str(stop_loss),
                "take_profit": str(take_profit),
                "reason": reason,
                "features": features or {},
            }
        )

    def stop_adjust(
        self,
        *,
        symbol: str,
        direction: str,
        old_stop: float | str,
        new_stop: float | str,
        why: str,
        mfe_r: float | None = None,
    ) -> bool:
        """Подтяжка стопа (БУ / структура / трейл)."""
        return self._write(
            {
                "event": "stop_adjust",
                "symbol": symbol,
                "direction": direction,
                "old_stop": str(old_stop),
                "new_stop": str(new_stop),
                "why": why,
                "mfe_r": mfe_r,
            }
        )

    def exit(
        self,
        *,
        symbol: str,
        direction: str,
        exit_price: float | str,
        reason: str,
        r_multiple: float | None = None,
        bars_held: int | None = None,
    ) -> bool:
        """Выход: причина и R."""
        return self._write(
            {
                "event": "exit",
                "symbol": symbol,
                "direction": direction,
                "exit_price": str(exit_price),
                "reason": reason,
                "r_multiple": r_multiple,
                "bars_held": bars_held,
            }
        )

    def reject(
        self,
        *,
        symbol: str,
        reason: str,
        stage: str = "pattern",
        snapshot: dict[str, Any] | None = None,
        strategy: str = "zeus_wedge_retest_4h",
    ) -> bool:
        """Почему НЕ вошли — память для разбора (не ордер)."""
        return self._write(
            {
                "event": "reject",
                "symbol": symbol,
                "strategy": strategy,
                "reason": reason,
                "stage": stage,
                "snapshot": snapshot or {},
            }
        )

    def structure_state(
        self,
        *,
        symbol: str,
        snapshot: dict[str, Any],
        strategy: str = "zeus_wedge_retest_4h",
    ) -> bool:
        """Снимок 4h-структуры на тике (клин есть/нет, границы)."""
        return self._write(
            {
                "event": "structure_state",
                "symbol": symbol,
                "strategy": strategy,
                "snapshot": snapshot,
            }
        )

    def ltf_impulse(
        self,
        *,
        symbol: str,
        timeframe: str,
        range_pct: float,
        volume_ratio: float,
        direction: str,
        near_structure: bool = False,
        note: str = "",
        strategy: str = "zeus_wedge_retest_4h",
    ) -> bool:
        """Сильный импульс на младшем ТФ — только память, не вход."""
        return self._write(
            {
                "event": "ltf_impulse",
                "symbol": symbol,
                "strategy": strategy,
                "timeframe": timeframe,
                "range_pct": round(range_pct, 6),
                "volume_ratio": round(volume_ratio, 4),
                "direction": direction,
                "near_structure": near_structure,
                "note": note or "observed; not an entry signal",
            }
        )
