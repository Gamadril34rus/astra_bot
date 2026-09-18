"""Журнал Зевса: entry / stop_adjust / exit пишутся в JSONL."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from astra_bot.core import models
from astra_bot.decision.zeus_trade_log import ZeusTradeLog
from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)


def test_zeus_trade_log_entry_exit(tmp_path):
    path = tmp_path / "zeus.jsonl"
    log = ZeusTradeLog(path)

    assert log.entry(
        symbol="BTC-USDT",
        direction="short",
        entry_price=100.0,
        stop_loss=102.0,
        take_profit=97.0,
        reason="false_break_up_retest_inside",
        features={"wedge_upper": 101.0},
    )
    assert log.stop_adjust(
        symbol="BTC-USDT",
        direction="short",
        old_stop=102.0,
        new_stop=100.0,
        why="breakeven",
        mfe_r=1.0,
    )
    assert log.exit(
        symbol="BTC-USDT",
        direction="short",
        exit_price=98.0,
        reason="take_profit",
        r_multiple=1.0,
        bars_held=4,
    )

    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(rows) == 3
    assert rows[0]["event"] == "entry"
    assert rows[0]["reason"] == "false_break_up_retest_inside"
    assert rows[1]["event"] == "stop_adjust"
    assert rows[1]["why"] == "breakeven"
    assert rows[2]["event"] == "exit"
    assert rows[2]["r_multiple"] == 1.0


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
    """Стратегия кладёт zeus_reason в features (для журнала/пайплайна)."""

    async def _run():
        strat = ZeusWedgeRetestStrategy(ZeusWedgeRetestConfig(enabled=True))
        return await strat.evaluate("BTC-USDT", _rising_wedge_fixture())

    res = asyncio.run(_run())
    assert res is not None
    assert res.features.get("zeus_pattern") == "false_break_up_retest_inside"
    assert "zeus_reason" in res.features
    assert res.features.get("stop_structure") == "beyond_false_break_extreme"
    assert ZeusWedgeRetestStrategy(ZeusWedgeRetestConfig(enabled=True)).preferred_timeframe == "4h"
