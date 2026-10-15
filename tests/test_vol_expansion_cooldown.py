"""VOL_EXPANSION anti-chatter re-entry guard + paper hooks (owner 15.10 / v3)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from astra_bot.decision.reentry_guard import (
    VOL_EXPANSION_COOLDOWN_SEC,
    check,
    is_vol_expansion_reason,
    note_skip,
    set_cooldown,
)
from astra_bot.decision.vol_cd_hooks import apply_paper_hooks


@pytest.fixture()
def cd_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "zeus_vol_cooldowns.json"
    ev = tmp_path / "zeus_vol_cooldown_events.jsonl"
    monkeypatch.setattr("astra_bot.decision.reentry_guard.DEFAULT_PATH", p)
    monkeypatch.setattr("astra_bot.decision.reentry_guard.EVENTS_PATH", ev)
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
    data = json.loads(cd_path.read_text(encoding="utf-8"))
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
    closed = time.time() - 60
    until1 = set_cooldown("WIF", "long", trade_id="sig-42", closed_ts=closed, now=closed + 1)
    until2 = set_cooldown("WIF", "long", trade_id="sig-42", closed_ts=closed, now=closed + 300)
    assert until2 == until1 == closed + VOL_EXPANSION_COOLDOWN_SEC
    data = json.loads(cd_path.read_text(encoding="utf-8"))
    assert data["WIF|long"]["until"] == until1


def test_paper_hook_blocks_open_and_logs_skip(cd_path: Path, tmp_path: Path) -> None:
    now = time.time()
    set_cooldown("WIF", "long", trade_id="t9", closed_ts=now, now=now)
    broker = MagicMock()
    broker.open_position = MagicMock(return_value="POS")
    broker._log_trade = MagicMock()
    broker.positions = []
    apply_paper_hooks(broker)
    out = broker.open_position(
        symbol="WIF", direction="long", entry_price=1, stop_loss=0.9,
        take_profit=1.2, quantity=1, strategy="zeus",
    )
    # Stub (not None): engine touches pos.id / strategy / quantity without guard
    assert out is not None
    assert out.id.startswith("vol_cd_skip|")
    assert out.symbol == "WIF"
    assert out.direction == "long"
    assert out.strategy == "zeus"
    assert float(out.quantity) == 0
    assert len(broker.positions) == 0  # not appended
    events = (tmp_path / "zeus_vol_cooldown_events.jsonl").read_text(encoding="utf-8")
    assert "entry_skipped_vol_cooldown" in events


def test_paper_hook_stub_engine_style_attrs(cd_path: Path) -> None:
    """Mirror trading_engine post-open access pattern on cooldown stub."""
    now = time.time()
    set_cooldown("ADA", "short", trade_id="t-eng", closed_ts=now, now=now)
    broker = MagicMock()
    broker.open_position = MagicMock(return_value="REAL")
    broker._log_trade = MagicMock()
    broker.positions = []
    apply_paper_hooks(broker)
    pos = broker.open_position(
        symbol="ADA", direction="short", entry_price=0.5, stop_loss=0.55,
        take_profit=0.4, quantity=10, strategy="zeus_wedge",
    )
    _ = pos.id, pos.symbol, pos.direction, pos.entry_price, pos.quantity, pos.strategy
    pos.plan_variant = "x"
    pos.plan_take = 0.4
    assert len(broker.positions) == 0


def test_paper_hook_vol_close_sets_cooldown(cd_path: Path) -> None:
    broker = MagicMock()
    broker.open_position = MagicMock(return_value="POS")
    broker._log_trade = lambda t: None
    apply_paper_hooks(broker)
    closed = time.time()
    trade = SimpleNamespace(
        id="tid-1", symbol="ADA", direction="short",
        exit_reason="VOL_EXPANSION", closed_at=int(closed * 1000), pnl=-1.0,
    )
    broker._log_trade(trade)
    blocked, until = check("ADA", "short", now=closed + 1)
    assert blocked is True
    # closed_at is ms int → float(sec) loses sub-ms; tolerate 1s
    assert abs(until - (closed + VOL_EXPANSION_COOLDOWN_SEC)) < 1.0


def test_paper_hook_non_vol_close_no_cooldown(cd_path: Path) -> None:
    broker = MagicMock()
    broker.open_position = MagicMock()
    broker._log_trade = MagicMock()
    apply_paper_hooks(broker)
    trade = SimpleNamespace(
        id="tid-2", symbol="SOL", direction="long",
        exit_reason="stop_loss", closed_at=int(time.time() * 1000), pnl=-1.0,
    )
    broker._log_trade(trade)
    assert check("SOL", "long")[0] is False


def test_paper_hook_repeated_close_same_trade_no_extend(cd_path: Path) -> None:
    broker = MagicMock()
    broker.open_position = MagicMock()
    broker._log_trade = lambda t: None
    apply_paper_hooks(broker)
    closed = time.time() - 30
    trade = SimpleNamespace(
        id="same-id", symbol="ATOM", direction="long",
        exit_reason="VOL_EXPANSION", closed_at=int(closed * 1000), pnl=-2.0,
    )
    broker._log_trade(trade)
    u1 = check("ATOM", "long", now=closed + 1)[1]
    broker._log_trade(trade)
    u2 = check("ATOM", "long", now=closed + 60)[1]
    assert u1 == u2
    assert abs(u1 - (closed + VOL_EXPANSION_COOLDOWN_SEC)) < 1.0
