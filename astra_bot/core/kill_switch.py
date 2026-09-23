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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("models/kill_switch_state.json")


@dataclass
class KillSwitchConfig:
    """Пороги kill-switch."""
    max_consecutive_loss_days: int = 5
    max_weekly_loss_pct: float = 3.0
    enabled: bool = True


@dataclass
class KillSwitchState:
    """Состояние kill-switch (переживает рестарт)."""
    is_halted: bool = False
    halt_reason: str = ""
    halted_at: str = ""
    consecutive_loss_days: int = 0
    last_pnl_day: str = ""
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
    """Kill-switch: авто-HALT при серии убыточных дней."""

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
        if not self.config.enabled:
            return
        today = day or date.today().isoformat()
        if self.state.last_pnl_day == today:
            self.state.daily_pnl_history = [
                d for d in self.state.daily_pnl_history if d["date"] != today
            ]
        self.state.daily_pnl_history.append({
            "date": today,
            "pnl": day_pnl,
            "equity": equity,
        })
        self.state.last_pnl_day = today
        self.state.daily_pnl_history = self.state.daily_pnl_history[-30:]
        self._check_consecutive_losses()
        self._check_weekly_loss(equity)
        self._save_state()

    def _check_consecutive_losses(self) -> None:
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
        if self.state.is_halted or equity <= 0:
            return
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
        return self.state.is_halted

    def reset(self) -> None:
        self.state.is_halted = False
        self.state.halt_reason = ""
        self.state.halted_at = ""
        self.state.consecutive_loss_days = 0
        self._save_state()
        logger.info("Kill-switch reset by operator")

    def status(self) -> dict[str, Any]:
        return {
            "is_halted": self.state.is_halted,
            "halt_reason": self.state.halt_reason,
            "consecutive_loss_days": self.state.consecutive_loss_days,
            "max_consecutive_loss_days": self.config.max_consecutive_loss_days,
            "max_weekly_loss_pct": self.config.max_weekly_loss_pct,
            "enabled": self.config.enabled,
        }


@dataclass
class SymbolLossGuard:
    """Per-symbol consecutive loss cooldown (Sprint 2026-09-23).

    After ``max_consecutive`` losses on a symbol, pause new entries for
    ``pause_hours``. Does not touch global kill-switch / HALT.

    API (tests/unit/test_p_win_calibration.py):
      is_paused(symbol, now=None) -> bool
      record(symbol, pnl, now=None)  # pnl < 0 counts as loss
    """

    max_consecutive: int = 3
    pause_hours: float = 4.0
    _losses: dict[str, int] = field(default_factory=dict)
    _pause_until: dict[str, datetime] = field(default_factory=dict)

    def is_paused(self, symbol: str, now: datetime | None = None) -> bool:
        until = self._pause_until.get(symbol)
        if until is None:
            return False
        ts = now if now is not None else datetime.now(UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        until_aware = until if until.tzinfo is not None else until.replace(tzinfo=UTC)
        if ts >= until_aware:
            self._pause_until.pop(symbol, None)
            self._losses[symbol] = 0
            return False
        return True

    def record(self, symbol: str, pnl: float, now: datetime | None = None) -> None:
        ts = now if now is not None else datetime.now(UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if pnl >= 0:
            self._losses[symbol] = 0
            self._pause_until.pop(symbol, None)
            return
        n = self._losses.get(symbol, 0) + 1
        self._losses[symbol] = n
        if n >= self.max_consecutive:
            self._pause_until[symbol] = ts + timedelta(hours=self.pause_hours)
            logger.warning(
                "SYMBOL_COOLDOWN %s: %d consecutive losses -> pause %.1fh",
                symbol, n, self.pause_hours,
            )

    # Back-compat aliases used by trading_engine sprint patches
    def record_loss(self, symbol: str) -> None:
        self.record(symbol, -1.0)

    def record_win(self, symbol: str) -> None:
        self.record(symbol, 1.0)
