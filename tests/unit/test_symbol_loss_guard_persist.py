"""A5: SymbolLossGuard counters/pauses survive restart; wired on close."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.core.kill_switch import SymbolLossGuard


def test_symbol_loss_guard_survives_reload(tmp_path: Path) -> None:
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


def test_state_path_none_is_in_memory_only(tmp_path: Path) -> None:
    g = SymbolLossGuard(max_consecutive=2, pause_hours=1.0, state_path=None)
    g.record("XRP-USDT", -1.0)
    g.record("XRP-USDT", -1.0)
    assert g.is_paused("XRP-USDT")
    assert list(tmp_path.iterdir()) == []


def test_corrupt_file_fail_open(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    g = SymbolLossGuard(state_path=path)
    assert g._losses == {}
    assert g._pause_until == {}


def test_record_closed_wires_guard_and_persists(tmp_path: Path) -> None:
    """Закрытие убыточной сделки увеличивает счётчик и пишет файл."""
    import pytest

    pytest.importorskip("aiohttp")
    pytest.importorskip("prometheus_client")

    from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig

    path = tmp_path / "symbol_loss_guard.json"
    cfg = TradingEngineConfig(
        symbols=("BTC-USDT",),
        symbol_loss_cooldown_enabled=True,
        symbol_loss_guard_path=str(path),
    )
    engine = TradingEngine.__new__(TradingEngine)
    engine.config = cfg
    engine.exchange = MagicMock()
    engine.symbol_guard = SymbolLossGuard(
        max_consecutive=3, pause_hours=4.0, state_path=path
    )
    engine.broker = MagicMock()
    engine.broker.positions = []
    engine.broker.equity = 10000
    engine.broker.realized_pnl = 0
    engine.broker.register_cooldown = MagicMock()
    engine.broker.save = MagicMock()
    engine.risk = MagicMock()
    engine.stats_store = MagicMock()
    engine.obs_log = MagicMock()
    engine.hypotheses = MagicMock()
    engine._cooldown_key = lambda s, sym, side: f"{s}|{sym}|{side}"
    engine._cooldown_ttl_ms = lambda: 20 * 60 * 1000

    closed = [
        SimpleNamespace(
            id="t1",
            symbol="BTC-USDT",
            direction="long",
            entry_price=100.0,
            exit_price=99.0,
            quantity=1.0,
            pnl=-10.0,
            pnl_pct=-1.0,
            fees=0.1,
            r_multiple=-1.0,
            mfe_r=0.0,
            mae_r=-1.0,
            regime="",
            regime_axes="",
            timeframe="4h",
            exit_reason="stop_loss",
            strategy="zeus_wedge_retest_4h",
            opened_at=0,
            closed_at=1_700_000_000_000,
        )
    ]
    engine._record_closed(closed)
    assert engine.symbol_guard._losses.get("BTC-USDT") == 1
    assert path.exists()
    engine._record_closed(closed)
    assert engine.symbol_guard._losses.get("BTC-USDT") == 2
    g2 = SymbolLossGuard(max_consecutive=3, pause_hours=4.0, state_path=path)
    assert g2._losses.get("BTC-USDT") == 2


def test_trading_engine_source_wires_record() -> None:
    """Статический якорь: _record_closed вызывает symbol_guard.record."""
    src = Path("astra_bot/decision/trading_engine.py").read_text(encoding="utf-8")
    assert "symbol_loss_guard_path" in src
    assert "self.symbol_guard.record(sym," in src
    assert "SymbolLossGuard(state_path=Path(_sg_path))" in src
