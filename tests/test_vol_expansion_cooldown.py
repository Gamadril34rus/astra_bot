"""VOL_EXPANSION anti-chatter re-entry guard (owner 15.10)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from astra_bot.decision.reentry_guard import (
    VOL_EXPANSION_COOLDOWN_SEC,
    check,
    is_vol_expansion_reason,
    note_skip,
    set_cooldown,
)


@pytest.fixture()
def cd_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "zeus_vol_cooldowns.json"
    ev = tmp_path / "zeus_vol_cooldown_events.jsonl"
    monkeypatch.setattr(
        "astra_bot.decision.reentry_guard.DEFAULT_PATH", p
    )
    monkeypatch.setattr(
        "astra_bot.decision.reentry_guard.EVENTS_PATH", ev
    )
    return p


def test_vol_exit_blocks_same_side_immediate(cd_path: Path, tmp_path: Path) -> None:
    now = time.time()
    set_cooldown("WIF", "long", trade_id="t1", exit_reason="VOL_EXPANSION", closed_ts=now, now=now)
    blocked, until = check("WIF", "long", now=now + 1)
    assert blocked is True
    assert until == now + VOL_EXPANSION_COOLDOWN_SEC
    note_skip("WIF", "long", until)
    events = (tmp_path / "zeus_vol_cooldown_events.jsonl").read_text(encoding="utf-8")
    assert "vol_cooldown_set" in events
    assert "entry_skipped_vol_cooldown" in events
    assert "WIF" in events


def test_opposite_side_and_other_symbol_free(cd_path: Path) -> None:
    now = time.time()
    set_cooldown("WIF", "long", trade_id="t1", closed_ts=now, now=now)
    assert check("WIF", "short", now=now + 1)[0] is False
    assert check("ADA", "long", now=now + 1)[0] is False


def test_non_vol_exit_no_cooldown() -> None:
    assert is_vol_expansion_reason("stop_loss") is False
    assert is_vol_expansion_reason("tp1") is False
    assert is_vol_expansion_reason("MAX_HOLD") is False
    assert is_vol_expansion_reason("VOL_EXPANSION") is True
    assert is_vol_expansion_reason("VOL_EXPANSION_spike") is True


def test_after_30min_allowed(cd_path: Path) -> None:
    now = time.time()
    set_cooldown("ADA", "short", trade_id="t2", closed_ts=now, now=now)
    assert check("ADA", "short", now=now + VOL_EXPANSION_COOLDOWN_SEC - 1)[0] is True
    assert check("ADA", "short", now=now + VOL_EXPANSION_COOLDOWN_SEC + 1)[0] is False


def test_restart_persists_from_file(cd_path: Path) -> None:
    now = time.time()
    set_cooldown("ATOM", "long", trade_id="t3", closed_ts=now, now=now)
    assert cd_path.exists()
    data = json.loads(cd_path.read_text(encoding="utf-8"))
    assert "ATOM|long" in data
    assert data["ATOM|long"]["until"] == now + VOL_EXPANSION_COOLDOWN_SEC
    blocked, until = check("ATOM", "long", now=now + 60)
    assert blocked is True
    assert until == now + VOL_EXPANSION_COOLDOWN_SEC


def test_expired_purged_on_save(cd_path: Path) -> None:
    now = time.time()
    set_cooldown("X", "long", trade_id="old", closed_ts=now - VOL_EXPANSION_COOLDOWN_SEC - 10, now=now)
    set_cooldown("Y", "short", trade_id="new", closed_ts=now, now=now)
    data = json.loads(cd_path.read_text(encoding="utf-8"))
    assert "X|long" not in data
    assert "Y|short" in data


def test_repeated_set_same_trade_id_does_not_extend(cd_path: Path) -> None:
    """P0 arena: sync_closed re-sees same exit every tick — until must not slide."""
    closed = time.time() - 60  # closed 1 min ago
    until1 = set_cooldown(
        "WIF", "long", trade_id="sig-42", closed_ts=closed, now=closed + 1
    )
    assert until1 == closed + VOL_EXPANSION_COOLDOWN_SEC
    # Later tick: wall clock advanced, same trade_id
    later = closed + 300
    until2 = set_cooldown(
        "WIF", "long", trade_id="sig-42", closed_ts=closed, now=later
    )
    assert until2 == until1  # must NOT become later + 1800
    data = json.loads(cd_path.read_text(encoding="utf-8"))
    assert data["WIF|long"]["until"] == until1
    assert data["WIF|long"]["trade_id"] == "sig-42"
