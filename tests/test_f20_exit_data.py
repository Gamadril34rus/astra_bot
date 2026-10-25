"""F20: exit-engine mark/bars/regime_tick data path."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astra_bot.decision.live_exit_engine import (
    _closed_bars_for_plan,
    classify_current_regime,
    run_exit_pass,
)


def test_classify_bars_lt_60():
    reg, reason = classify_current_regime([SimpleNamespace()] * 10)
    assert reg == ""
    assert reason == "bars_lt_60"


def test_classify_empty():
    reg, reason = classify_current_regime([])
    assert reason == "bars_lt_60"


def test_closed_bars_uses_timeframe_not_interval():
    calls = []

    class FakeClient:
        async def get_candles(self, symbol, timeframe=None, since=None, limit=1000, **kw):
            assert "interval" not in kw
            calls.append((symbol, timeframe, limit))
            return [
                SimpleNamespace(
                    open_time=1_700_000_000_000 + i * 14_400_000,
                    open=100.0, high=101.0, low=99.0, close=100.5, volume=1.0,
                )
                for i in range(80)
            ]

    out = asyncio.new_event_loop().run_until_complete(
        _closed_bars_for_plan(FakeClient(), "DOGE-USDT", "4h")
    )
    assert len(out) >= 60
    assert calls and calls[0][1] == "4h"
    assert calls[0][2] >= 120


def test_run_exit_mark_from_current_price(tmp_path, monkeypatch):
    from astra_bot.decision import live_positions as lp

    plan = {
        "symbol": "DOGE-USDT",
        "side": "short",
        "entry": "0.07964",
        "stop": "0.079560360",
        "initial_stop": "0.08150229996341458",
        "qty": "100",
        "be_moved": True,
        "ts": 1_700_000_000,
        "opened_at_ms": 1_700_000_000_000,
    }
    pos_path = tmp_path / "positions.json"
    lp.save_positions({"DOGE-USDT|short": plan}, pos_path)

    pos = SimpleNamespace(
        symbol="DOGE-USDT",
        quantity=Decimal("100"),
        current_price=Decimal("0.07744"),
        mark_price=None,
        markPrice=None,
        price=None,
    )
    broker = MagicMock()
    broker.client = MagicMock()
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.move_stop = AsyncMock()

    fake_bars = [
        SimpleNamespace(
            open_time=1_700_000_000_000 + i * 14_400_000,
            open=0.08, high=0.081, low=0.077, close=0.078, volume=1.0,
        )
        for i in range(80)
    ]
    journal: list = []

    def _capture(row, path=None):
        journal.append(row)

    monkeypatch.setenv("ZEUS_LIVE_EXIT_ENGINE", "shadow")
    # Patch only append_journal where run_exit_pass imports it (lazy import inside fn).
    # Do NOT replace sys.modules — that poisons later tests (f9 journal writes).
    with patch(
        "astra_bot.decision.live_arm_wiring.append_journal",
        side_effect=_capture,
    ):
        with patch(
            "astra_bot.decision.live_exit_engine._closed_bars_for_plan",
            new=AsyncMock(return_value=fake_bars[:-1]),
        ):
            with patch(
                "astra_bot.decision.live_exit_engine.classify_current_regime",
                return_value=("RANGE", "ok"),
            ):
                with patch(
                    "astra_bot.decision.live_stop_telemetry.record_stop_change",
                    return_value=None,
                ):
                    out = asyncio.new_event_loop().run_until_complete(
                        run_exit_pass(broker, positions_path=pos_path)
                    )

    assert out.get("mode") == "shadow"
    assert not any(j.get("action") == "mark_fallback" for j in journal)
    rt = [j for j in journal if j.get("event") == "regime_tick"]
    assert rt, f"expected regime_tick in {journal}"
    assert rt[0].get("reason") == "ok"
    assert rt[0].get("regime") == "RANGE"
    plans = lp.load_positions(pos_path)
    p = plans.get("DOGE-USDT|short") or list(plans.values())[0]
    assert p.get("peak_low") is not None or p.get("peak_high") is not None
    assert p.get("current_regime") == "RANGE"


def test_regime_tick_fail_open_reason(tmp_path, monkeypatch):
    from astra_bot.decision import live_positions as lp

    plan = {
        "symbol": "SOL-USDT",
        "side": "short",
        "entry": "100.705",
        "stop": "105.13",
        "initial_stop": "105.13",
        "qty": "10",
        "be_moved": False,
        "ts": 1_792_843_200,
    }
    pos_path = tmp_path / "positions.json"
    lp.save_positions({"SOL-USDT|short": plan}, pos_path)

    pos = SimpleNamespace(
        symbol="SOL-USDT",
        quantity=Decimal("10"),
        current_price=Decimal("102.0"),
    )
    broker = MagicMock()
    broker.client = MagicMock()
    broker.client.get_positions = AsyncMock(return_value=[pos])
    broker.move_stop = AsyncMock()
    journal: list = []

    def _capture(row, path=None):
        journal.append(row)

    monkeypatch.setenv("ZEUS_LIVE_EXIT_ENGINE", "shadow")
    with patch(
        "astra_bot.decision.live_arm_wiring.append_journal",
        side_effect=_capture,
    ):
        with patch(
            "astra_bot.decision.live_exit_engine._closed_bars_for_plan",
            new=AsyncMock(return_value=[]),
        ):
            with patch(
                "astra_bot.decision.live_stop_telemetry.record_stop_change",
                return_value=None,
            ):
                out = asyncio.new_event_loop().run_until_complete(
                    run_exit_pass(broker, positions_path=pos_path)
                )

    rt = [j for j in journal if j.get("event") == "regime_tick"]
    assert rt
    assert rt[0].get("reason") == "bars_lt_60"
