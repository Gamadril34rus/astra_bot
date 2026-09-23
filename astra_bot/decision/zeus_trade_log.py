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

    def clock_start(
        self,
        *,
        symbol: str = "",
        strategy: str = "zeus_wedge_retest_4h",
        note: str = "",
    ) -> bool:
        """Старт цикла paper-clock (публичный API журнала)."""
        row: dict[str, Any] = {
            "event": "clock_start",
            "symbol": symbol,
            "strategy": strategy,
        }
        if note:
            row["note"] = note
        return self._write(row)

    def tick(
        self,
        *,
        symbol: str = "",
        strategy: str = "zeus_wedge_retest_4h",
        note: str = "",
        open_positions: int | None = None,
    ) -> bool:
        row: dict[str, Any] = {
            "event": "tick",
            "symbol": symbol,
            "strategy": strategy,
        }
        if note:
            row["note"] = note
        if open_positions is not None:
            row["open_positions"] = open_positions
        return self._write(row)

    def entry(
        self,
        *,
        symbol: str,
        direction: str,
        entry_price: float | str,
        stop_loss: float | str,
        take_profit: float | str,
        reason: str = "",
        features: dict[str, Any] | None = None,
        strategy: str = "zeus_wedge_retest_4h",
    ) -> bool:
        return self._write(
            {
                "event": "entry",
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reason": reason,
                "features": features or {},
                "strategy": strategy,
            }
        )

    def stop_adjust(
        self,
        *,
        symbol: str,
        direction: str,
        old_stop: float | str,
        new_stop: float | str,
        why: str = "",
    ) -> bool:
        return self._write(
            {
                "event": "stop_adjust",
                "symbol": symbol,
                "direction": direction,
                "old_stop": old_stop,
                "new_stop": new_stop,
                "why": why,
            }
        )

    def exit(
        self,
        *,
        symbol: str,
        direction: str,
        exit_price: float | str,
        reason: str = "",
    ) -> bool:
        return self._write(
            {
                "event": "exit",
                "symbol": symbol,
                "direction": direction,
                "exit_price": exit_price,
                "reason": reason,
            }
        )

    def reject(
        self,
        *,
        symbol: str,
        reason: str,
        stage: str = "",
        snapshot: dict[str, Any] | None = None,
    ) -> bool:
        return self._write(
            {
                "event": "reject",
                "symbol": symbol,
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
    ) -> bool:
        return self._write(
            {
                "event": "structure_state",
                "symbol": symbol,
                "snapshot": snapshot,
            }
        )

    def ltf_impulse(
        self,
        *,
        symbol: str,
        timeframe: str = "15m",
        note: str = "",
        range_mult: float | None = None,
        vol_mult: float | None = None,
    ) -> bool:
        row: dict[str, Any] = {
            "event": "ltf_impulse",
            "symbol": symbol,
            "timeframe": timeframe,
            "note": note,
        }
        if range_mult is not None:
            row["range_mult"] = range_mult
        if vol_mult is not None:
            row["vol_mult"] = vol_mult
        return self._write(row)
