"""Zeus heat governor: consecutive losses → risk budget capped at 1% until UTC day end or win.

Not a pause (trading continues). Does not replace HALT / SymbolLossGuard / kill-switch.
Parameters (threshold=3, until end of UTC day) are pre-registered — change only on owner order.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HEAT_PATH = Path("models/zeus_heat_state.json")
HEAT_LOSS_THRESHOLD = 3  # consecutive closed losses → heat_active


def _utc_today() -> date:
    return datetime.now(tz=UTC).date()


@dataclass
class ZeusHeatGovernor:
    """Consecutive-loss heat: cap risk to base 1% while active.

    State survives process restart via ``state_path`` (Zeus: models/zeus_heat_state.json).
    """

    threshold: int = HEAT_LOSS_THRESHOLD
    state_path: Path | None = None
    consecutive: int = 0
    heat_active: bool = False
    heat_day: str | None = None  # ISO date (UTC) when heat was triggered; expires end of that day

    def __post_init__(self) -> None:
        if self.state_path is not None:
            self.state_path = Path(self.state_path)
            self._load()

    # ---- public API ---------------------------------------------------------

    def is_heat_active(self, now: datetime | None = None) -> bool:
        """True while heat_active and still on the trigger UTC day (or no day set)."""
        self._expire_if_needed(now)
        return bool(self.heat_active)

    def record(self, pnl: float, now: datetime | None = None) -> None:
        """Record a closed-trade result. pnl < 0 → loss streak; pnl > 0 → clear heat."""
        self._expire_if_needed(now)
        if pnl > 0:
            if self.consecutive or self.heat_active:
                logger.info(
                    "zeus heat: win clears consecutive=%s heat_active=%s",
                    self.consecutive,
                    self.heat_active,
                )
            self.consecutive = 0
            self.heat_active = False
            self.heat_day = None
            self._save()
            return
        if pnl < 0:
            self.consecutive += 1
            if self.consecutive >= self.threshold and not self.heat_active:
                today = (now or datetime.now(tz=UTC)).astimezone(UTC).date()
                self.heat_active = True
                self.heat_day = today.isoformat()
                logger.warning(
                    "zeus heat: %s consecutive losses → heat_active until end of UTC %s",
                    self.consecutive,
                    self.heat_day,
                )
            self._save()

    def risk_pct_override(self, now: datetime | None = None) -> float | None:
        """Return 0.01 when heat is active, else None (caller keeps ladder)."""
        if self.is_heat_active(now):
            return 0.01
        return None

    # ---- persistence --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "consecutive": self.consecutive,
            "heat_active": self.heat_active,
            "heat_day": self.heat_day,
        }

    def _expire_if_needed(self, now: datetime | None = None) -> None:
        if not self.heat_active or not self.heat_day:
            return
        today = (now or datetime.now(tz=UTC)).astimezone(UTC).date()
        try:
            trigger = date.fromisoformat(self.heat_day)
        except ValueError:
            self.heat_active = False
            self.heat_day = None
            self._save()
            return
        if today > trigger:
            logger.info(
                "zeus heat: UTC day rolled past %s → clear heat (consecutive was %s)",
                self.heat_day,
                self.consecutive,
            )
            self.heat_active = False
            self.heat_day = None
            # Keep consecutive so a new loss can re-trigger; day roll alone does not reset streak.
            self._save()

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                logger.warning("zeus_heat: non-dict state, ignoring")
                return
            self.threshold = int(raw.get("threshold", HEAT_LOSS_THRESHOLD))
            self.consecutive = int(raw.get("consecutive", 0))
            self.heat_active = bool(raw.get("heat_active", False))
            day = raw.get("heat_day")
            self.heat_day = str(day) if day else None
            self._expire_if_needed()
        except Exception as exc:
            logger.warning("zeus_heat: load failed (fail-open): %s", exc)

    def _save(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("zeus_heat: save failed: %s", exc)
