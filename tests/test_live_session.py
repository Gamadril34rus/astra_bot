import os
os.environ["ZEUS_SESSION_PROFILE"] = "1"

"""MSK session buckets: fat/normal/thin/off."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from astra_bot.decision.live_session import (
    get_session_profile,
    arm_allowed_by_session,
    apply_session_to_environ,
)

MSK = timezone(timedelta(hours=3))


def _dt(weekday: int, hour: int) -> datetime:
    # 2026-10-26 is Monday
    base = datetime(2026, 10, 26, hour, 0, 0, tzinfo=MSK)  # Monday
    return base + timedelta(days=weekday)


def test_weekday_fat_london_and_us():
    assert get_session_profile(_dt(0, 11)).name == "fat"
    assert get_session_profile(_dt(0, 17)).name == "fat"
    assert get_session_profile(_dt(0, 17)).be_trigger_r == 0.50
    assert get_session_profile(_dt(0, 17)).tp1_rr == 0.70


def test_weekday_off_morning():
    p = get_session_profile(_dt(0, 8))
    assert p.name == "off"
    assert p.arm is False
    assert arm_allowed_by_session(_dt(0, 8)) is False


def test_weekday_thin_late():
    p = get_session_profile(_dt(0, 1))
    assert p.name == "thin"
    assert p.be_trigger_r == 0.30
    assert p.tp1_rr == 0.45


def test_weekend_more_off():
    # Saturday = weekday 5
    assert get_session_profile(_dt(5, 8)).name == "off"
    assert get_session_profile(_dt(5, 17)).name == "normal"
    assert get_session_profile(_dt(5, 17)).arm is True


def test_apply_sets_env(monkeypatch):
    monkeypatch.delenv("ZEUS_SESSION_FORCE", raising=False)
    p = apply_session_to_environ(_dt(0, 17))
    import os
    assert os.environ["ZEUS_SESSION_NAME"] == "fat"
    assert float(os.environ["ZEUS_BE_TRIGGER_R"]) == 0.50
