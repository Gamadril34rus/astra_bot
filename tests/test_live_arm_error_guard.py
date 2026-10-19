"""Arm entry error guard — margin reject must not kill the tick (owner №25)."""
from __future__ import annotations

from pathlib import Path

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astra_bot.decision.live_arm_wiring import (
    MARGIN_FAIL_LIMIT,
    _is_margin_or_exchange_reject,
    bump_margin_fail,
    clear_margin_fail,
    load_margin_fails,
    run_arm_entries,
)


class ExchangeError(Exception):
    pass


def test_is_margin_detect():
    assert _is_margin_or_exchange_reject(ExchangeError("Insufficient margin"))
    assert _is_margin_or_exchange_reject(TimeoutError("timeout"))
    assert not _is_margin_or_exchange_reject(ValueError("bad"))


def test_margin_fail_counter(tmp_path, monkeypatch):
    path = tmp_path / "fails.json"
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.MARGIN_FAIL_PATH", path
    )
    assert bump_margin_fail("AVAX-USDT", "sid1", path=path) == 1
    assert bump_margin_fail("AVAX-USDT", "sid1", path=path) == 2
    assert bump_margin_fail("AVAX-USDT", "sid1", path=path) == 3
    assert load_margin_fails(path)["AVAX-USDT|sid1"] == 3
    clear_margin_fail("AVAX-USDT", "sid1", path=path)
    assert "AVAX-USDT|sid1" not in load_margin_fails(path)


def test_margin_reject_swallowed_tick_alive(tmp_path, monkeypatch):
    """ExchangeError on entry → plan rejected, no raise, exit path ok."""
    from astra_bot.decision import live_arm_wiring as w

    monkeypatch.setattr(w, "MARGIN_FAIL_PATH", tmp_path / "mf.json")
    monkeypatch.setattr(w, "ENTRIES_PATH", tmp_path / "entries.jsonl")
    monkeypatch.setattr(w, "_seen_signal_ids", lambda path=None: set())
    monkeypatch.setattr(
        w, "exchange_open_symbols", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(
        w, "exchange_side_stats", AsyncMock(return_value={"long": {"count": 0, "upnl": 0.0}, "short": {"count": 0, "upnl": 0.0}})
    )
    monkeypatch.setattr(w, "is_fresh", lambda row, max_sec=0: True)
    monkeypatch.setattr(
        w,
        "candidate_from_signal_dict",
        lambda row: SimpleNamespace(
            symbol=row["symbol"],
            side=row["side"],
            entry=Decimal(str(row["entry"])),
            stop=Decimal(str(row["stop"])),
            take_profit=Decimal(str(row.get("tp") or row["entry"])),
            confidence=0.9,
            strategy="zeus_channel_boundary",
            step_size=Decimal("0.1"),
        ),
    )
    plan = SimpleNamespace(
        symbol="AVAX-USDT",
        side="short",
        quantity=Decimal("100"),
        stop_price=Decimal("11"),
        take_profit_price=Decimal("9"),
        leverage=3,
        entry_price=Decimal("10"),
    )
    monkeypatch.setattr(w, "plan_from_candidate", AsyncMock(return_value=plan))
    monkeypatch.setattr(w, "resolve_step_size", AsyncMock(return_value=Decimal("0.1")))
    monkeypatch.setattr(w, "mark_cooldown", lambda *a, **k: None)
    monkeypatch.setattr(w, "append_journal", lambda *a, **k: None)
    monkeypatch.setattr(w, "append_live_trade", lambda *a, **k: None)
    monkeypatch.setattr(w, "upsert_plan", lambda *a, **k: None)
    monkeypatch.setattr(w, "append_entry_record", lambda *a, **k: None)
    monkeypatch.setattr(w, "same_side_cluster_blocks", lambda *a, **k: False)

    async def boom(*a, **k):
        raise ExchangeError("BingX API error: Insufficient margin")

    broker = SimpleNamespace(
        open_with_protection=boom,
        entries_allowed=True,
    )
    rows = [
        {
            "signal_id": "zeus_channel_boundary|AVAX-USDT|short|1",
            "symbol": "AVAX-USDT",
            "side": "short",
            "entry": 10.0,
            "stop": 11.0,
            "tp": 9.0,
            "confidence": 0.9,
            "strategy": "zeus_channel_boundary",
            "retest_ts": 1e12,
        }
    ]
    sent = []

    async def tg(msg):
        sent.append(msg)

    entered, skipped = asyncio.run(
        run_arm_entries(broker, rows, max_entries=3, send_tg=tg, entries_path=tmp_path / "e.jsonl")
    )
    assert entered == []
    assert skipped.get("margin", 0) >= 1
    assert any("AVAX" in s for s in sent)
    assert load_margin_fails(tmp_path / "mf.json").get("AVAX-USDT|zeus_channel_boundary|AVAX-USDT|short|1") == 1


def test_three_fails_then_skip(tmp_path, monkeypatch):
    from astra_bot.decision import live_arm_wiring as w

    path = tmp_path / "mf.json"
    monkeypatch.setattr(w, "MARGIN_FAIL_PATH", path)
    sid = "sid-avax"
    for _ in range(MARGIN_FAIL_LIMIT):
        bump_margin_fail("AVAX-USDT", sid, path=path)
    assert load_margin_fails(path)[f"AVAX-USDT|{sid}"] >= MARGIN_FAIL_LIMIT

    monkeypatch.setattr(w, "ENTRIES_PATH", tmp_path / "entries.jsonl")
    monkeypatch.setattr(w, "_seen_signal_ids", lambda path=None: set())
    monkeypatch.setattr(w, "exchange_open_symbols", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        w, "exchange_side_stats", AsyncMock(return_value={"short": {"count": 0, "upnl": 0.0}, "long": {"count": 0, "upnl": 0.0}})
    )
    monkeypatch.setattr(w, "is_fresh", lambda row, max_sec=0: True)
    monkeypatch.setattr(
        w,
        "candidate_from_signal_dict",
        lambda row: SimpleNamespace(
            symbol="AVAX-USDT",
            side="short",
            entry=Decimal("10"),
            stop=Decimal("11"),
            take_profit=Decimal("9"),
            confidence=0.9,
            strategy="x",
            step_size=Decimal("1"),
        ),
    )
    plan_called = []

    async def no_plan(*a, **k):
        plan_called.append(1)
        return None

    monkeypatch.setattr(w, "plan_from_candidate", no_plan)
    monkeypatch.setattr(w, "resolve_step_size", AsyncMock(return_value=Decimal("1")))
    monkeypatch.setattr(w, "same_side_cluster_blocks", lambda *a, **k: False)
    monkeypatch.setattr(w, "append_entry_record", lambda *a, **k: None)
    monkeypatch.setattr(w, "append_journal", lambda *a, **k: None)

    broker = SimpleNamespace(open_with_protection=AsyncMock())
    rows = [
        {
            "signal_id": sid,
            "symbol": "AVAX-USDT",
            "side": "short",
            "entry": 10,
            "stop": 11,
            "tp": 9,
            "confidence": 0.9,
            "strategy": "x",
            "retest_ts": 1e12,
        }
    ]
    entered, skipped = asyncio.run(
        run_arm_entries(broker, rows, max_entries=3, entries_path=tmp_path / "e2.jsonl")
    )
    assert entered == []
    assert skipped.get("margin", 0) >= 1
    # limit skip may run before plan; either no plan call or plan returned None
    assert plan_called == [] or skipped["margin"] >= 1


def test_timeout_writes_pending_verify_tick_alive(tmp_path, monkeypatch):
    """TimeoutError → pending_verify ghost, tick continues (owner F1)."""
    from astra_bot.decision import live_arm_wiring as w

    monkeypatch.setattr(w, "MARGIN_FAIL_PATH", tmp_path / "mf.json")
    monkeypatch.setattr(w, "ENTRIES_PATH", tmp_path / "entries.jsonl")
    monkeypatch.setattr(w, "_seen_signal_ids", lambda path=None: set())
    monkeypatch.setattr(w, "exchange_open_symbols", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        w,
        "exchange_side_stats",
        AsyncMock(
            return_value={
                "long": {"count": 0, "upnl": 0.0},
                "short": {"count": 0, "upnl": 0.0},
            }
        ),
    )
    monkeypatch.setattr(w, "is_fresh", lambda row, max_sec=0: True)
    monkeypatch.setattr(
        w,
        "candidate_from_signal_dict",
        lambda row: SimpleNamespace(
            symbol=row["symbol"],
            side=row["side"],
            entry=Decimal(str(row["entry"])),
            stop=Decimal(str(row["stop"])),
            take_profit=Decimal(str(row.get("tp") or row["entry"])),
            confidence=0.9,
            strategy="zeus_channel_boundary",
            step_size=Decimal("0.1"),
        ),
    )
    plan = SimpleNamespace(
        symbol="AVAX-USDT",
        side="short",
        quantity=Decimal("100"),
        stop_price=Decimal("11"),
        take_profit_price=Decimal("9"),
        leverage=3,
        entry_price=Decimal("10"),
    )
    monkeypatch.setattr(w, "plan_from_candidate", AsyncMock(return_value=plan))
    monkeypatch.setattr(w, "resolve_step_size", AsyncMock(return_value=Decimal("0.1")))
    monkeypatch.setattr(w, "mark_cooldown", lambda *a, **k: None)
    monkeypatch.setattr(w, "append_journal", lambda *a, **k: None)
    monkeypatch.setattr(w, "append_live_trade", lambda *a, **k: None)
    monkeypatch.setattr(w, "append_entry_record", lambda *a, **k: None)
    monkeypatch.setattr(w, "same_side_cluster_blocks", lambda *a, **k: False)

    ghosts = []

    def capture_upsert(*a, **k):
        ghosts.append(dict(k))
        if a:
            ghosts[-1]["symbol"] = a[0]

    monkeypatch.setattr(w, "upsert_plan", capture_upsert)

    async def boom(*a, **k):
        raise TimeoutError("read timeout")

    broker = SimpleNamespace(open_with_protection=boom, entries_allowed=True)
    rows = [
        {
            "signal_id": "sid-timeout",
            "symbol": "AVAX-USDT",
            "side": "short",
            "entry": 10.0,
            "stop": 11.0,
            "tp": 9.0,
            "confidence": 0.9,
            "strategy": "zeus_channel_boundary",
            "retest_ts": 1e12,
        }
    ]
    sent = []

    async def tg(msg):
        sent.append(msg)

    entered, skipped = asyncio.run(
        run_arm_entries(
            broker, rows, max_entries=3, send_tg=tg, entries_path=tmp_path / "e.jsonl"
        )
    )
    assert entered == []
    assert any(g.get("pending_verify") is True for g in ghosts)
    assert any("требует проверки" in s for s in sent)
    assert not any("отклонён" in s for s in sent)


def test_yml_saves_margin_fails():
    candidates = [
        Path("/home/workdir/artifacts/zeus_live_clock_f2.yml"),
        Path(".github/workflows/zeus-live-clock.yml"),
    ]
    body = ""
    for p in candidates:
        if p.exists():
            body = p.read_text(encoding="utf-8")
            break
    assert "zeus_arm_margin_fails.json" in body
