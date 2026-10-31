"""Session profile (MSK): when to arm, BE trigger, early TP — by liquidity.

Owner 31.10: fat hours → let R work; thin → bank faster; dead → no new entries.
Open positions always managed (BE/trail/scale) regardless of arm gate.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class SessionProfile:
    name: str  # fat | normal | thin | off
    arm: bool
    be_trigger_r: float
    tp1_rr: float
    note: str = ""


# Hour (0-23 MSK) → base profile name on weekdays
# Fat: London open + US overlap. Off: post-Asia / pre-London dead zone.
_WEEKDAY_HOUR: dict[int, str] = {}
for h in range(24):
    if h in (10, 11, 12, 16, 17, 18, 19, 20):
        _WEEKDAY_HOUR[h] = "fat"
    elif h in (3, 4, 5, 6, 13, 14, 15, 21, 22):
        _WEEKDAY_HOUR[h] = "normal"
    elif h in (0, 1, 2, 23):
        _WEEKDAY_HOUR[h] = "thin"
    else:
        # 7, 8, 9 — transition, low edge
        _WEEKDAY_HOUR[h] = "off"

# Weekend: only overlap-ish hours tradeable; rest off/thin
_WEEKEND_HOUR: dict[int, str] = {}
for h in range(24):
    if h in (16, 17, 18, 19):
        _WEEKEND_HOUR[h] = "normal"  # not full fat — thinner books
    elif h in (10, 11, 12, 15, 20, 21):
        _WEEKEND_HOUR[h] = "thin"
    else:
        _WEEKEND_HOUR[h] = "off"


_PRESETS: dict[str, SessionProfile] = {
    "fat": SessionProfile(
        name="fat",
        arm=True,
        be_trigger_r=0.50,
        tp1_rr=0.70,
        note="max liquidity — hold for R, later BE, later partial",
    ),
    "normal": SessionProfile(
        name="normal",
        arm=True,
        be_trigger_r=0.40,
        tp1_rr=0.55,
        note="decent flow — balanced",
    ),
    "thin": SessionProfile(
        name="thin",
        arm=True,
        be_trigger_r=0.30,
        tp1_rr=0.45,
        note="thin book — bank early, tight BE",
    ),
    "off": SessionProfile(
        name="off",
        arm=False,
        be_trigger_r=0.30,
        tp1_rr=0.45,
        note="dead/transition — manage open only, no new entries",
    ),
}


def _env_float(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def session_enabled() -> bool:
    """Opt-in: ZEUS_SESSION_PROFILE=1 enables dynamic hours (workflow sets it).

    Default off so unit tests / local runs keep static ZEUS_ARM behavior.
    """
    v = (os.environ.get("ZEUS_SESSION_PROFILE") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def now_msk(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(tz=MSK)
    if now.tzinfo is None:
        return now.replace(tzinfo=MSK)
    return now.astimezone(MSK)


def raw_bucket(now: datetime | None = None) -> str:
    dt = now_msk(now)
    table = _WEEKEND_HOUR if dt.weekday() >= 5 else _WEEKDAY_HOUR
    return table.get(dt.hour, "off")


def get_session_profile(now: datetime | None = None) -> SessionProfile:
    """Effective profile. Static env ZEUS_BE_TRIGGER_R / ZEUS_TP1_RR still work as
    *ceilings* only when session disabled; when enabled, session numbers win
    unless ZEUS_SESSION_LOCK_ENV=1 (then env overrides profile).
    """
    if not session_enabled():
        return SessionProfile(
            name="static",
            arm=True,
            be_trigger_r=_env_float("ZEUS_BE_TRIGGER_R", 0.5),
            tp1_rr=_env_float("ZEUS_TP1_RR", 0.6),
            note="session profile disabled",
        )
    name = raw_bucket(now)
    # optional force: ZEUS_SESSION_FORCE=fat|normal|thin|off
    force = (os.environ.get("ZEUS_SESSION_FORCE") or "").strip().lower()
    if force in _PRESETS:
        name = force
    base = _PRESETS[name]
    if (os.environ.get("ZEUS_SESSION_LOCK_ENV") or "").strip() in ("1", "true", "yes"):
        return SessionProfile(
            name=base.name,
            arm=base.arm,
            be_trigger_r=_env_float("ZEUS_BE_TRIGGER_R", base.be_trigger_r),
            tp1_rr=_env_float("ZEUS_TP1_RR", base.tp1_rr),
            note=base.note + " (env lock)",
        )
    return base


def apply_session_to_environ(now: datetime | None = None) -> SessionProfile:
    """Write effective BE/TP into process env for downstream readers this tick."""
    p = get_session_profile(now)
    os.environ["ZEUS_BE_TRIGGER_R"] = str(p.be_trigger_r)
    os.environ["ZEUS_TP1_RR"] = str(p.tp1_rr)
    os.environ["ZEUS_SESSION_NAME"] = p.name
    return p


def arm_allowed_by_session(now: datetime | None = None) -> bool:
    if not session_enabled():
        return True
    return bool(get_session_profile(now).arm)
