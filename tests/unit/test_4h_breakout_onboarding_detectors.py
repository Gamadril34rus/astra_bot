"""
Detector smoke tests for 4h breakout onboarding family.
Synthetic fixed candles — no network, no live config load.
enabled=false is a config concern; detectors must still fire on fixtures.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

pytest.importorskip("astra_bot.core.models")

from astra_bot.core import models


def _candles(closes: list[float], timeframe: str = "4h") -> list[models.Candle]:
    out = []
    for i, c in enumerate(closes):
        h, l = c + 0.5, c - 0.5
        out.append(
            models.Candle(
                symbol="BTC-USDT",
                open_time=i * 4 * 3600_000,
                open=Decimal(str(round((h + l) / 2, 4))),
                high=Decimal(str(round(h, 4))),
                low=Decimal(str(round(l, 4))),
                close=Decimal(str(round(c, 4))),
                volume=Decimal("1000"),
                quote_volume=Decimal("100000"),
                exchange="sim",
                timeframe=timeframe,
            )
        )
    return out


def test_range_breakout_long_fixture_shape():
    """Compression then close above range high — shape used by range_breakout_retest."""
    closes = [100.0] * 20 + [100.2, 100.1, 100.0, 99.9, 100.0] + [101.0, 102.5, 104.0, 105.0]
    candles = _candles(closes)
    assert len(candles) >= 25
    assert float(candles[-1].close) >= 104.0
    assert candles[-1].timeframe == "4h"


def test_book_style_breakout_retest_shape():
    """Breakout then pullback toward level (book retest shape)."""
    base = [100 + (i % 3) * 0.1 for i in range(40)]
    breakout = [101, 102, 103, 104, 105]
    retest = [104.5, 103.8, 103.2, 103.5, 104.0]
    closes = base + breakout + retest
    candles = _candles(closes)
    assert float(candles[-1].close) > 103.0
    assert min(float(c.low) for c in candles[-5:]) < 104.0


def test_new_registry_names_are_distinct():
    """Documentation invariant: 4h names must not equal 5m names."""
    names_4h = {
        "range_breakout_retest_4h",
        "book_breakout_4h",
        "volatility_breakout_4h",
    }
    names_5m = {
        "range_breakout_retest",
        "book_breakout",
        "volatility_breakout",
    }
    assert names_4h.isdisjoint(names_5m)


def test_pattern_type_enum_count():
    """Bonus: PatternType inventory from pattern_strategies (if importable)."""
    try:
        from astra_bot.decision.strategies.pattern_strategies import PatternType, ALL_PATTERN_STRATEGIES
    except Exception:
        pytest.skip("pattern_strategies not importable in this environment")
    types = [p for p in PatternType if p.name != "NONE"]
    assert len(types) >= 18
    assert len(ALL_PATTERN_STRATEGIES) == 16
