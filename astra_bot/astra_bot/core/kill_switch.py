"""
Kill-switch убыточного контура (TZ P2-3).

Правило уровня системы:
- N дней подряд отрицательного PnL → auto-HALT
- Недельный убыток > X% equity → auto-HALT

Пороги задаются конфигом. HALT переживает рестарт (сохраняется в state).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("models/kill_switch_state.json")


@dataclass
class KillSwitchConfig:
    """Пороги kill-switch."""
    # Максимальное количество дней подряд с убытком до HALT.
    max_consecutive_loss_days: int = 5
    # Максимальный недельный убыток (% equity) до HALT.
    max_weekly_loss_pct: float = 3.0
    # Включён ли kill-switch.
    enabled: bool = True


@dataclass
class KillSwitchState:
    """Состояние kill-switch (переживает рестарт)."""
    is_halted: bool = False
    halt_reason: str = ""
    halted_at: str = ""
    consecutive_loss_days: int = 0
    last_pnl_day: str = ""  # ISO date
    daily_pnl_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KillSwitchState:
        return cls(
            is_halted=data.get("is_halted", False),
            halt_reason=data.get("halt_reason", ""),
            halted_at=data.get("halted_at", ""),
            consecutive_loss_days=data.get("consecutive_loss_days", 0),
            last_pnl_day=data.get("last_pnl_day", ""),
            daily_pnl_history=data.get("daily_pnl_history", []),
        )


class KillSwitch:
    """Kill-switch: авто-HALT при серии убыточных дней.

    Используется в trading_engine для проверки перед открытием новых позиций.
    """

    def __init__(
        self,
        config: KillSwitchConfig | None = None,
        state_path: Path = DEFAULT_STATE_PATH,
    ):
        self.config = config or KillSwitchConfig()
        self.state_path = state_path
        self.state = self._load_state()

    def _load_state(self) -> KillSwitchState:
        if not self.state_path.exists():
            return KillSwitchState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return KillSwitchState.from_dict(data)
        except Exception as exc:
            logger.warning("kill_switch: failed to load state: %s", exc)
            return KillSwitchState()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.state_path)

    def record_daily_pnl(self, day_pnl: float, equity: float, day: str | None = None) -> None:
        """Записать дневной PnL и проверить пороги.

        Вызывается в конце каждого торгового дня (или в morning report).
        ``day`` — ISO-дата (для тестов); по умолчанию = сегодня.
        """
        if not self.config.enabled:
            return

        today = day or date.today().isoformat()
        if self.state.last_pnl_day == today:
            # Уже записано за сегодня — обновляем.
            self.state.daily_pnl_history = [
                d for d in self.state.daily_pnl_history if d["date"] != today
            ]

        self.state.daily_pnl_history.append({
            "date": today,
            "pnl": day_pnl,
            "equity": equity,
        })
        self.state.last_pnl_day = today

        # Keep only last 30 days
        self.state.daily_pnl_history = self.state.daily_pnl_history[-30:]

        # Check consecutive loss days
        self._check_consecutive_losses()
        # Check weekly loss
        self._check_weekly_loss(equity)

        self._save_state()

    def _check_consecutive_losses(self) -> None:
        """Проверить N дней подряд убытка."""
        if self.state.is_halted:
            return

        consecutive = 0
        for day in reversed(self.state.daily_pnl_history):
            if day["pnl"] < 0:
                consecutive += 1
            else:
                break

        self.state.consecutive_loss_days = consecutive

        if consecutive >= self.config.max_consecutive_loss_days:
            self.state.is_halted = True
            self.state.halt_reason = (
                f"kill_switch: {consecutive} consecutive loss days "
                f"(threshold: {self.config.max_consecutive_loss_days})"
            )
            self.state.halted_at = datetime.now(UTC).isoformat()
            logger.critical("KILL-SWITCH HALT: %s", self.state.halt_reason)

    def _check_weekly_loss(self, equity: float) -> None:
        """Проверить недельный убыток > X% equity."""
        if self.state.is_halted or equity <= 0:
            return

        # Sum last 7 days
        last_7 = self.state.daily_pnl_history[-7:]
        weekly_pnl = sum(d["pnl"] for d in last_7)
        weekly_loss_pct = abs(weekly_pnl) / equity * 100 if weekly_pnl < 0 else 0

        if weekly_loss_pct >= self.config.max_weekly_loss_pct:
            self.state.is_halted = True
            self.state.halt_reason = (
                f"kill_switch: weekly loss {weekly_loss_pct:.1f}% "
                f"(threshold: {self.config.max_weekly_loss_pct}%)"
            )
            self.state.halted_at = datetime.now(UTC).isoformat()
            logger.critical("KILL-SWITCH HALT: %s", self.state.halt_reason)

    def is_halted(self) -> bool:
        """True если система на HALT."""
        return self.state.is_halted

    def reset(self) -> None:
        """Сбросить HALT (только ручное действие оператора)."""
        self.state.is_halted = False
        self.state.halt_reason = ""
        self.state.halted_at = ""
        self.state.consecutive_loss_days = 0
        self._save_state()
        logger.info("Kill-switch reset by operator")

    def status(self) -> dict[str, Any]:
        """Текущий статус kill-switch."""
        return {
            "is_halted": self.state.is_halted,
            "halt_reason": self.state.halt_reason,
            "consecutive_loss_days": self.state.consecutive_loss_days,
            "max_consecutive_loss_days": self.config.max_consecutive_loss_days,
            "max_weekly_loss_pct": self.config.max_weekly_loss_pct,
            "enabled": self.config.enabled,
        }
