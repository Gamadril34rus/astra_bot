"""Тень сигналов Зевса: evaluate пишет в JSONL, исполнение не трогает."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from astra_bot.core import models
from astra_bot.decision.zeus_signal_shadow import ZeusSignalShadow


def _c(i: int, o: float, h: float, l: float, cl: float) -> models.Candle:
    return models.Candle(
        exchange="bingx",
        symbol="BTC-USDT",
        timeframe="4h",
        open_time=1_700_000_000_000 + i * 4 * 3600 * 1000,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(cl)),
        volume=Decimal("1000"),
        quote_volume=Decimal("100000"),
    )


def _fixture() -> list[models.Candle]:
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
    # + forming bar (will be stripped by shadow)
    candles.append(_c(27, upper_approx, upper_approx + 0.1, upper_approx - 0.1, upper_approx))
    return candles


@pytest.mark.asyncio
async def test_zeus_shadow_writes_signal(tmp_path):
    path = tmp_path / "zeus_shadow.jsonl"
    shadow = ZeusSignalShadow(path)
    ok = await shadow.observe(
        symbol="BTC-USDT",
        candles_4h=_fixture(),
        current_price=104.0,
        market_regime="RANGE",
    )
    assert ok is True
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "zeus_shadow_signal"
    assert rows[0]["direction"] == "short"
    assert rows[0]["would_execute"] is False
    assert rows[0]["strategy"] == "zeus_wedge_retest_4h"


@pytest.mark.asyncio
async def test_zeus_shadow_dedup(tmp_path):
    path = tmp_path / "zeus_shadow.jsonl"
    shadow = ZeusSignalShadow(path)
    fx = _fixture()
    assert await shadow.observe(symbol="BTC-USDT", candles_4h=fx) is True
    assert await shadow.observe(symbol="BTC-USDT", candles_4h=fx) is False
    assert len(path.read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_zeus_shadow_fail_open_few_bars(tmp_path):
    path = tmp_path / "zeus_shadow.jsonl"
    shadow = ZeusSignalShadow(path)
    ok = await shadow.observe(symbol="BTC-USDT", candles_4h=_fixture()[:5])
    assert ok is False
    assert not path.exists()
