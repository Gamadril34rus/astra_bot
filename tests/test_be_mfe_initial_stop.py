"""BE MFE from initial_stop; trigger default 0.5R (owner 30.10)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from astra_bot.decision.live_positions import load_positions, save_positions


def test_be_fires_at_0_6r_with_initial_stop(tmp_path, monkeypatch):
    """Mark +0.6R vs initial risk → BE (old trigger 0.8 would skip)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    monkeypatch.setenv("ZEUS_BE_TRIGGER_R", "0.5")
    # entry 100, initial_stop 90, risk 10; mark 106 = 0.6R long
    save_positions(
        {
            "BTC-USDT|long": {
                "symbol": "BTC-USDT",
                "side": "long",
                "entry": "100",
                "stop": "90",
                "initial_stop": "90",
                "tp": "120",
                "qty": "1",
                "be_moved": False,
            }
        }
    )
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    broker = MagicMock()
    pos = SimpleNamespace(
        symbol="BTC-USDT",
        quantity=Decimal("1"),
        mark_price=Decimal("106"),
        current_price=Decimal("106"),
    )
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.move_stop = AsyncMock()
    broker.net_breakeven_price = MagicMock(return_value=Decimal("100.1"))

    from scripts.zeus_live_clock import sync_partial_breakeven

    out = asyncio.run(sync_partial_breakeven(broker, positions_path=tmp_path / "models" / "zeus_live_positions.json"))
    # load_positions uses DEFAULT_PATH relative to cwd models/
    out = asyncio.run(sync_partial_breakeven(broker))
    assert "BTC-USDT" in (out.get("moved") or [])
    broker.move_stop.assert_called()
    assert any(j.get("event") == "price_breakeven" for j in journals)


def test_mfe_uses_initial_not_tight_stop(tmp_path, monkeypatch):
    """After stop tightened near entry, MFE still vs initial risk — no phantom R."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    monkeypatch.setenv("ZEUS_BE_TRIGGER_R", "0.5")
    # initial risk 10; current stop almost at entry (r_abs=0.5 if used would inflate mfe)
    # mark 103 = only 0.3R on initial → should NOT BE
    save_positions(
        {
            "ETH-USDT|long": {
                "symbol": "ETH-USDT",
                "side": "long",
                "entry": "100",
                "stop": "99.5",  # tightened
                "initial_stop": "90",
                "tp": "120",
                "qty": "1",
                "be_moved": False,
            }
        }
    )
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    broker = MagicMock()
    pos = SimpleNamespace(
        symbol="ETH-USDT",
        quantity=Decimal("1"),
        mark_price=Decimal("103"),
        current_price=Decimal("103"),
    )
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.move_stop = AsyncMock()

    from scripts.zeus_live_clock import sync_partial_breakeven

    out = asyncio.run(sync_partial_breakeven(broker))
    assert "ETH-USDT" not in (out.get("moved") or [])
    broker.move_stop.assert_not_called()
    ticks = [j for j in journals if j.get("event") == "be_mark_tick"]
    assert ticks and abs(ticks[0]["mfe_r"] - 0.3) < 0.05
