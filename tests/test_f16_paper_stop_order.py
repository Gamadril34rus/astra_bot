"""F16: paper stop order + stop_adjust + qty_reconcile journal."""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- pure helpers without heavy imports ---

def test_check_exits_before_adjust_in_source():
    """F16 order: check_exits appears before adjust_stops in source."""
    src = Path("astra_bot/decision/trading_engine.py").read_text(encoding="utf-8")
    assert src.count("closed = self.broker.check_exits(last_bar)") >= 2
    # In _process_exits_only: check_exits then adjust_stops
    idx_fn = src.find("async def _process_exits_only")
    chunk = src[idx_fn:idx_fn + 3500]
    i_check = chunk.find("closed = self.broker.check_exits(last_bar)")
    i_adj = chunk.find("self.plan_engine.adjust_stops(")
    assert 0 < i_check < i_adj, "F16: check_exits must precede adjust_stops"


def test_process_symbol_order():
    src = Path("astra_bot/decision/trading_engine.py").read_text(encoding="utf-8")
    idx = src.find("async def process_symbol")
    chunk = src[idx:idx + 8000]
    # first check_exits in process_symbol body vs adjust after
    i_check = chunk.find("closed = self.broker.check_exits(last_bar)")
    i_adj = chunk.find("self.plan_engine.adjust_stops(")
    assert 0 < i_check < i_adj


def test_emit_stop_adjust_in_exit_plan_source():
    src = Path("astra_bot/decision/exit_plan.py").read_text(encoding="utf-8")
    assert "def _emit_stop_adjust" in src
    assert 'why=source' in src or 'why=source' in src.replace(" ", "")
    for tag in ("d1_structural", "be", "trail"):
        assert tag in src


def test_qty_reconcile_journal_not_silent_in_source():
    src = Path("astra_bot/decision/live_broker.py").read_text(encoding="utf-8")
    assert "qty_reconcile journal failed" in src
    assert "journal_errors" in src
    # silent bare pass removed after append_journal(row)
    assert "F16: journal MUST land" in src


@pytest.mark.asyncio
async def test_qty_reconcile_writes_journal_and_saves(tmp_path, monkeypatch):
    """Integration: reconcile updates plan + journal row (no silent loss)."""
    # Stub heavy deps before importing LiveBroker
    sys.modules.setdefault("aiohttp", MagicMock())
    sys.modules.setdefault("prometheus_client", MagicMock())

    positions_path = tmp_path / "zeus_live_positions.json"
    journal_path = tmp_path / "zeus_live_journal.jsonl"
    plans = {
        "LTC-USDT|short": {
            "symbol": "LTC-USDT",
            "side": "short",
            "qty": 474.7,
            "plan_qty": 474.7,
            "signal_id": "sid-ltc",
        }
    }
    positions_path.write_text(json.dumps({"positions": plans}))

    from astra_bot.decision import live_arm_wiring, live_positions, live_broker

    monkeypatch.setattr(live_arm_wiring, "JOURNAL_PATH", journal_path)
    monkeypatch.setattr(live_positions, "DEFAULT_PATH", positions_path)

    client = MagicMock()
    pos = SimpleNamespace(symbol="LTC-USDT", side="short", quantity=284.8)
    client.get_positions = AsyncMock(return_value=[pos])

    broker = live_broker.LiveBroker.__new__(live_broker.LiveBroker)
    broker.client = client
    monkeypatch.setattr(live_broker, "append_live_trade", lambda *a, **k: None)

    out = await broker.reconcile_plan_qty(positions_path=positions_path)
    assert out.get("reconciled"), out
    assert abs(out["reconciled"][0]["old_qty"] - 474.7) < 0.01
    assert abs(out["reconciled"][0]["new_qty"] - 284.8) < 0.01

    assert journal_path.exists(), "qty_reconcile must write journal"
    jrows = [json.loads(l) for l in journal_path.read_text().splitlines() if l.strip()]
    assert any(r.get("event") == "qty_reconcile" for r in jrows)
    qr = next(r for r in jrows if r.get("event") == "qty_reconcile")
    assert qr["symbol"] == "LTC-USDT"

    saved = json.loads(positions_path.read_text())
    body = saved.get("positions", saved)
    assert abs(float(body["LTC-USDT|short"]["qty"]) - 284.8) < 0.01


def test_apply_tighter_long_only_up():
    # import apply_tighter only if possible; else skip
    try:
        from astra_bot.decision.exit_plan import apply_tighter
    except Exception:
        pytest.skip("exit_plan import requires full deps")
    pos = SimpleNamespace(direction="long", stop_loss=Decimal("100"))
    assert apply_tighter(pos, Decimal("101")) is True
    assert pos.stop_loss == Decimal("101")
    assert apply_tighter(pos, Decimal("100.5")) is False
