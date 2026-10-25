"""F23: exchange position without plan → ghost_position + TG warn."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def test_ghost_position_journals_and_warns(tmp_path, monkeypatch):
    from scripts import zeus_live_clock as z

    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    # plan only for BTC — SOL is ghost on exchange
    from astra_bot.decision.live_positions import upsert_plan, save_positions

    pos_path = tmp_path / "models" / "zeus_live_positions.json"
    upsert_plan(
        "BTC-USDT", side="long", qty="0.1", stop="100", entry="110", path=pos_path,
    )

    pos_sol = SimpleNamespace(
        symbol="SOL-USDT",
        quantity=Decimal("10.5"),
        side="short",
        entry_price=Decimal("100.7"),
    )
    pos_btc = SimpleNamespace(
        symbol="BTC-USDT",
        quantity=Decimal("0.1"),
        side="long",
        entry_price=Decimal("110"),
    )

    class Client:
        async def get_positions(self):
            return [pos_btc, pos_sol]

    class Broker:
        client = Client()

    journal: list = []
    tg: list = []

    async def fake_tg(msg: str):
        tg.append(msg)

    with patch("astra_bot.decision.live_arm_wiring.append_journal", side_effect=lambda r, path=None: journal.append(r)):
        out = asyncio.new_event_loop().run_until_complete(
            z.detect_ghost_positions(
                Broker(), positions_path=pos_path, send_tg=fake_tg,
            )
        )

    assert any(g["symbol"] == "SOL-USDT" for g in out.get("ghosts", []))
    assert not any(g["symbol"] == "BTC-USDT" for g in out.get("ghosts", []))
    assert any(j.get("event") == "ghost_position" and j.get("symbol") == "SOL-USDT" for j in journal)
    assert tg and "SOL-USDT" in tg[0] and "без плана" in tg[0]
    # flag claimed
    flag = tmp_path / "models" / "zeus_ghost_position_warn.json"
    assert flag.exists()

    # second call within hour — still in ghosts list, no second TG
    with patch("astra_bot.decision.live_arm_wiring.append_journal", side_effect=lambda r, path=None: journal.append(r)):
        out2 = asyncio.new_event_loop().run_until_complete(
            z.detect_ghost_positions(
                Broker(), positions_path=pos_path, send_tg=fake_tg,
            )
        )
    assert any(g["symbol"] == "SOL-USDT" for g in out2.get("ghosts", []))
    assert len(tg) == 1  # dedup


def test_ghost_dedup_no_spam(tmp_path, monkeypatch):
    from scripts import zeus_live_clock as z

    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    pos_path = tmp_path / "models" / "zeus_live_positions.json"
    pos_path.write_text(json.dumps({"positions": {}, "ts": 0}), encoding="utf-8")
    flag = tmp_path / "models" / "zeus_ghost_position_warn.json"
    import time
    flag.write_text(json.dumps({"ETH-USDT": time.time()}), encoding="utf-8")

    pos = SimpleNamespace(
        symbol="ETH-USDT", quantity=Decimal("1"), side="long", entry_price=Decimal("3000"),
    )

    class Client:
        async def get_positions(self):
            return [pos]

    class Broker:
        client = Client()

    tg: list = []
    journal: list = []

    async def fake_tg(msg: str):
        tg.append(msg)

    with patch("astra_bot.decision.live_arm_wiring.append_journal", side_effect=lambda r, path=None: journal.append(r)):
        out = asyncio.new_event_loop().run_until_complete(
            z.detect_ghost_positions(Broker(), positions_path=pos_path, send_tg=fake_tg)
        )
    assert out["ghosts"]
    assert not tg  # within 1h
    assert not journal  # no journal on dedup path


def test_entry_save_failure_surfaces_as_ghost_next_tick(tmp_path, monkeypatch):
    """F23.4: entry filled on exchange but plan not saved → next tick ghost_position.

    Simulates race/save failure: exchange has ADA, plans empty.
    """
    from scripts import zeus_live_clock as z

    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    pos_path = tmp_path / "models" / "zeus_live_positions.json"
    pos_path.write_text(json.dumps({"positions": {}, "ts": 0}), encoding="utf-8")

    ada = SimpleNamespace(
        symbol="ADA-USDT",
        quantity=Decimal("34920.6"),
        side="short",
        entry_price=Decimal("0.18"),
    )

    class Client:
        async def get_positions(self):
            return [ada]

    class Broker:
        client = Client()

    journal: list = []
    tg: list = []

    async def fake_tg(msg: str):
        tg.append(msg)

    with patch("astra_bot.decision.live_arm_wiring.append_journal", side_effect=lambda r, path=None: journal.append(r)):
        out = asyncio.new_event_loop().run_until_complete(
            z.detect_ghost_positions(Broker(), positions_path=pos_path, send_tg=fake_tg)
        )
    assert out["ghosts"][0]["symbol"] == "ADA-USDT"
    assert any(j.get("event") == "ghost_position" for j in journal)
    assert any("ADA-USDT" in m for m in tg)
