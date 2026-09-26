"""A5: SymbolLossGuard counters/pauses survive engine recreation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from astra_bot.core.kill_switch import SymbolLossGuard


def test_symbol_loss_guard_survives_reload(tmp_path: Path) -> None:
    """Счётчики и пауза не сгорают при пересоздании guard с тем же path."""
    path = tmp_path / "symbol_loss_guard.json"
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    g1 = SymbolLossGuard(max_consecutive=3, pause_hours=4.0, state_path=path)
    assert not g1.is_paused("BTC-USDT", now)
    g1.record("BTC-USDT", -1.0, now)
    g1.record("BTC-USDT", -1.0, now)
    g1.record("BTC-USDT", -1.0, now)
    assert g1.is_paused("BTC-USDT", now)
    assert path.exists()

    g2 = SymbolLossGuard(max_consecutive=3, pause_hours=4.0, state_path=path)
    assert g2.is_paused("BTC-USDT", now)
    assert g2._losses.get("BTC-USDT") == 3
    until = g2._pause_until.get("BTC-USDT")
    assert until is not None
    assert until == now + timedelta(hours=4.0)


def test_symbol_loss_guard_pause_expires_and_persists_reset(tmp_path: Path) -> None:
    path = tmp_path / "sg.json"
    t0 = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
    g1 = SymbolLossGuard(max_consecutive=2, pause_hours=1.0, state_path=path)
    g1.record("ETH-USDT", -1.0, t0)
    g1.record("ETH-USDT", -1.0, t0)
    assert g1.is_paused("ETH-USDT", t0)

    later = t0 + timedelta(hours=2)
    g2 = SymbolLossGuard(max_consecutive=2, pause_hours=1.0, state_path=path)
    assert not g2.is_paused("ETH-USDT", later)
    g3 = SymbolLossGuard(max_consecutive=2, pause_hours=1.0, state_path=path)
    assert g3._losses.get("ETH-USDT", 0) == 0
    assert "ETH-USDT" not in g3._pause_until


def test_symbol_loss_guard_win_resets_and_saves(tmp_path: Path) -> None:
    path = tmp_path / "sg.json"
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    g1 = SymbolLossGuard(max_consecutive=3, pause_hours=4.0, state_path=path)
    g1.record("SOL-USDT", -1.0, now)
    g1.record("SOL-USDT", -1.0, now)
    g1.record("SOL-USDT", 5.0, now)
    g2 = SymbolLossGuard(state_path=path)
    assert g2._losses.get("SOL-USDT", 0) == 0
    assert not g2.is_paused("SOL-USDT", now)


def test_from_dict_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sg.json"
    now = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
    g1 = SymbolLossGuard(max_consecutive=3, pause_hours=4.0, state_path=path)
    g1.record("BNB-USDT", -1.0, now)
    data = g1.to_dict()
    g2 = SymbolLossGuard.from_dict(data, state_path=tmp_path / "other.json")
    assert g2._losses.get("BNB-USDT") == 1
    assert g2.max_consecutive == 3
