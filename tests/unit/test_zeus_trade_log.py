"""Unit tests for ZeusTradeLog (paper-clock journal)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from astra_bot.core import models
from astra_bot.decision.zeus_trade_log import ZeusTradeLog
from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)


def test_zeus_trade_log_writes_events(tmp_path: Path):
    path = tmp_path / "zeus.jsonl"
    log = ZeusTradeLog(str(path))
    log.clock_start(symbol="BTC-USDT", strategy="zeus_wedge_retest_4h")
    log.reject(symbol="BTC-USDT", reason="width_out_of_band", stage="structure")
    log.structure_state(symbol="BTC-USDT", snapshot={"has_wedge": False})
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 3
    rows = [json.loads(x) for x in lines]
    events = {r.get("event") for r in rows}
    assert "clock_start" in events
    assert "reject" in events
    assert "structure_state" in events


def test_zeus_trade_log_entry_stop_exit(tmp_path: Path):
    path = tmp_path / "zeus2.jsonl"
    log = ZeusTradeLog(str(path))
    log.entry(
        symbol="ETH-USDT",
        direction="long",
        entry_price="2000",
        stop_loss="1950",
        take_profit="2100",
        reason="test",
    )
    log.stop_adjust(
        symbol="ETH-USDT",
        direction="long",
        old_stop="1950",
        new_stop="1980",
        why="trail",
    )
    log.exit(
        symbol="ETH-USDT",
        direction="long",
        exit_price="2090",
        reason="tp",
    )
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    kinds = [r.get("event") for r in rows]
    assert "entry" in kinds
    assert "stop_adjust" in kinds
    assert "exit" in kinds


def _c(i: int, o: float, h: float, l: float, c: float) -> models.Candle:
    from datetime import datetime, timedelta

    ts = datetime(2024, 1, 1) + timedelta(hours=4 * i)
    return models.Candle(
        open_time=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


def _rising_wedge_fixture() -> list[models.Candle]:
    candles = []
    base = 100.0
    for i in range(24):
        lo = base + i * 0.15
        hi = base + 6.0 - i * 0.05
        mid = (lo + hi) / 2
        candles.append(_c(i, mid, hi, lo, mid + 0.05))
    last_hi = float(candles[-1].high)
    candles.append(_c(24, last_hi + 0.2, last_hi + 1.5, last_hi + 0.1, last_hi + 1.2))
    candles.append(_c(25, last_hi + 1.0, last_hi + 1.8, last_hi + 0.5, last_hi + 0.9))
    upper_approx = float(candles[23].high)
    candles.append(
        _c(26, upper_approx + 0.2, upper_approx + 0.4, upper_approx - 0.8, upper_approx - 0.3)
    )
    return candles


def test_zeus_signal_features_contain_reason():
    async def _run():
        strat = ZeusWedgeRetestStrategy(ZeusWedgeRetestConfig(enabled=True))
        return await strat.evaluate("BTC-USDT", _rising_wedge_fixture())

    res = asyncio.run(_run())
    assert res is not None
    assert res.features.get("zeus_pattern") == "false_break_up_retest_inside"
    # reason or zeus_reason (compat after restore)
    assert ("zeus_reason" in res.features) or ("reason" in res.features)
    assert res.features.get("stop_structure") == "beyond_false_break_extreme"
    assert ZeusWedgeRetestStrategy(ZeusWedgeRetestConfig(enabled=True)).preferred_timeframe == "4h"


def test_zeus_diagnose_would_signal_on_fixture():
    strat = ZeusWedgeRetestStrategy(ZeusWedgeRetestConfig(enabled=True))
    diag = strat.diagnose(_rising_wedge_fixture())
    assert diag.get("would_signal") is True
    assert diag.get("direction") == "short"
    assert diag.get("has_wedge") is True


def test_zeus_diagnose_reject_on_flat():
    strat = ZeusWedgeRetestStrategy(ZeusWedgeRetestConfig(enabled=True))
    flat = [_c(i, 100, 101, 99, 100) for i in range(30)]
    diag = strat.diagnose(flat)
    assert diag.get("would_signal") is not True
