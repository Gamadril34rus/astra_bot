"""Unit tests for the 8 new pattern strategies and detection functions."""

import math
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.decision.context import StrategyContext
from astra_bot.decision.strategies.pattern_strategies import (
    CupAndHandleStrategy,
    DiamondStrategy,
    DoubleTopBottomStrategy,
    ExpandingTriangleStrategy,
    FlagStrategy,
    PatternType,
    PennantStrategy,
    RectangleStrategy,
    TripleTopBottomStrategy,
    detect_cup_and_handle,
    detect_diamond,
    detect_double_top_bottom,
    detect_expanding_triangle,
    detect_flag,
    detect_pennant,
    detect_rectangle,
    detect_triple_top_bottom,
)


def _make_candles(highs: list[float], lows: list[float], closes: list[float]) -> list[models.Candle]:
    candles = []
    for i in range(len(closes)):
        h = highs[i]
        l = lows[i]
        c = closes[i]
        if math.isnan(h) or math.isinf(h) or math.isnan(l) or math.isinf(l) or math.isnan(c) or math.isinf(c):
            h, l, c = 100.0, 95.0, 98.0
        o = (h + l) / 2
        vol = 300.0 if i > len(closes) - 5 else 100.0
        candles.append(
            models.Candle(
                symbol="BTC-USDT",
                open_time=i * 300_000,
                open=Decimal(str(round(o, 4))),
                high=Decimal(str(round(h, 4))),
                low=Decimal(str(round(l, 4))),
                close=Decimal(str(round(c, 4))),
                volume=Decimal(str(vol)),
                quote_volume=Decimal("10000"),
                exchange="sim",
                timeframe="5m",
            )
        )
    return candles


# ----------------------------------------------------------------------
# 1. Double Top / Bottom
# ----------------------------------------------------------------------
def make_double_bottom(n=60):
    highs, lows, closes = [], [], []
    for i in range(n):
        if i < 12:
            c = 108.0 - (i / 12) * 8.0
        elif i < 24:
            c = 100.0 + ((i - 12) / 12) * 8.0
        elif i < 36:
            c = 108.0 - ((i - 24) / 12) * 7.9
        elif i < 58:
            c = 100.1 + ((i - 36) / 22) * 7.9
        else:
            c = 109.5
        closes.append(c)
        highs.append(c + 0.3)
        lows.append(c - 0.3)
    return highs, lows, closes


def make_double_top(n=60):
    highs, lows, closes = [], [], []
    for i in range(n):
        if i < 12:
            c = 100.0 + (i / 12) * 8.0
        elif i < 24:
            c = 108.0 - ((i - 12) / 12) * 8.0
        elif i < 36:
            c = 100.0 + ((i - 24) / 12) * 7.9
        elif i < 58:
            c = 107.9 - ((i - 36) / 22) * 7.9
        else:
            c = 98.5
        closes.append(c)
        highs.append(c + 0.3)
        lows.append(c - 0.3)
    return highs, lows, closes


class TestDoubleTopBottom:
    def test_double_bottom_detection_and_strategy(self):
        highs, lows, closes = make_double_bottom()
        res = detect_double_top_bottom(highs, lows, closes)
        assert res.pattern == PatternType.DOUBLE_BOTTOM
        assert res.breakout_direction == "up"
        assert res.confidence > 0.5

        strat = DoubleTopBottomStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(highs, lows, closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is not None
        assert sig.direction == "long"
        assert sig.take_profit > sig.entry_price
        risk = float(sig.entry_price - sig.stop_loss)
        reward = float(sig.take_profit - sig.entry_price)
        assert reward / risk >= 1.5

    def test_double_top_detection(self):
        highs, lows, closes = make_double_top()
        res = detect_double_top_bottom(highs, lows, closes)
        assert res.pattern == PatternType.DOUBLE_TOP
        assert res.breakout_direction == "down"


# ----------------------------------------------------------------------
# 2. Triple Top / Bottom
# ----------------------------------------------------------------------
def make_triple_bottom(n=70):
    highs, lows, closes = [], [], []
    for i in range(n):
        if i >= 65:
            c = 109.5
        elif i <= 15:
            c = 108.0 - (i / 15.0) * 8.0
        elif i <= 25:
            c = 100.0 + ((i - 15) / 10.0) * 8.0
        elif i <= 35:
            c = 108.0 - ((i - 25) / 10.0) * 8.0
        elif i <= 45:
            c = 100.0 + ((i - 35) / 10.0) * 8.0
        elif i <= 55:
            c = 108.0 - ((i - 45) / 10.0) * 8.0
        else:
            c = 100.0 + ((i - 55) / 10.0) * 8.0
        closes.append(c)
        highs.append(c + 0.3)
        lows.append(c - 0.3)
    return highs, lows, closes


class TestTripleTopBottom:
    def test_triple_bottom_detection_and_strategy(self):
        highs, lows, closes = make_triple_bottom()
        res = detect_triple_top_bottom(highs, lows, closes)
        assert res.pattern == PatternType.TRIPLE_BOTTOM
        assert res.breakout_direction == "up"

        strat = TripleTopBottomStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(highs, lows, closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is not None
        assert sig.direction == "long"


# ----------------------------------------------------------------------
# 3. Rectangle
# ----------------------------------------------------------------------
def make_rectangle(n=60):
    highs, lows, closes = [], [], []
    for i in range(n):
        if i >= 58:
            c = 109.0
        else:
            pos = i % 10
            going_up = (i // 10) % 2 == 0
            c = 100.0 + (pos / 9) * 8.0 if going_up else 108.0 - (pos / 9) * 8.0
        closes.append(c)
        highs.append(min(108.2, c + 0.4))
        lows.append(max(99.8, c - 0.4))
    return highs, lows, closes


class TestRectangle:
    def test_rectangle_detection_and_strategy(self):
        highs, lows, closes = make_rectangle()
        res = detect_rectangle(highs, lows, closes)
        assert res.pattern == PatternType.RECTANGLE
        assert res.breakout_direction == "up"

        strat = RectangleStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(highs, lows, closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is not None
        assert sig.direction == "long"


# ----------------------------------------------------------------------
# 4. Expanding Triangle
# ----------------------------------------------------------------------
def make_expanding_triangle(n=60):
    highs, lows, closes = [], [], []
    for i in range(n):
        t = i / (n - 1)
        up = 102.0 + t * 10.0
        lo = 98.0 - t * 10.0
        pos = i % 10
        going_up = (i // 10) % 2 == 0
        c = lo + (up - lo) * pos / 9 if going_up else up - (up - lo) * pos / 9
        if i == n - 1:
            c = 114.0
        closes.append(c)
        highs.append(c + 0.3)
        lows.append(c - 0.3)
    return highs, lows, closes


class TestExpandingTriangle:
    def test_expanding_triangle_detection_and_strategy(self):
        highs, lows, closes = make_expanding_triangle()
        res = detect_expanding_triangle(highs, lows, closes)
        assert res.pattern == PatternType.EXPANDING_TRIANGLE
        assert res.breakout_direction == "up"

        strat = ExpandingTriangleStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(highs, lows, closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is not None


# ----------------------------------------------------------------------
# 5. Flag
# ----------------------------------------------------------------------
def make_flag(n=50):
    highs, lows, closes = [], [], []
    for i in range(n):
        if i < 10:
            c = 100.0
        elif i <= 14:
            c = 100.0 + (i - 10) * 2.0
        elif i < 48:
            c = 108.0 - (i - 14) * 0.12 + (0.4 if i % 2 == 0 else -0.4)
        else:
            c = 109.5
        closes.append(c)
        highs.append(c + 0.3)
        lows.append(c - 0.3)
    return highs, lows, closes


class TestFlag:
    def test_bull_flag_detection_and_strategy(self):
        highs, lows, closes = make_flag()
        res = detect_flag(highs, lows, closes)
        assert res.pattern == PatternType.FLAG
        assert res.breakout_direction == "up"

        strat = FlagStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(highs, lows, closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is not None
        assert sig.direction == "long"


# ----------------------------------------------------------------------
# 6. Pennant
# ----------------------------------------------------------------------
def make_pennant(n=50):
    highs, lows, closes = [], [], []
    for i in range(n):
        if i < 10:
            c = 100.0
        elif i <= 14:
            c = 100.0 + (i - 10) * 2.0
        elif i < 48:
            progress = (i - 14) / 34.0
            upper = 108.0 - progress * 3.0
            lower = 102.0 + progress * 2.0
            c = upper if i % 2 == 0 else lower
        else:
            c = 109.5
        closes.append(c)
        highs.append(c + 0.2)
        lows.append(c - 0.2)
    return highs, lows, closes


class TestPennant:
    def test_bull_pennant_detection_and_strategy(self):
        highs, lows, closes = make_pennant()
        res = detect_pennant(highs, lows, closes)
        assert res.pattern == PatternType.PENNANT
        assert res.breakout_direction == "up"

        strat = PennantStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(highs, lows, closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is not None


# ----------------------------------------------------------------------
# 7. Diamond
# ----------------------------------------------------------------------
def make_diamond(n=60):
    highs, lows, closes = [], [], []
    for i in range(n):
        if i < 30:
            t = i / 30.0
            up = 100.0 + t * 8.0
            lo = 100.0 - t * 8.0
        elif i < 58:
            t = (i - 30) / 28.0
            up = 108.0 - t * 7.0
            lo = 92.0 + t * 7.0
        else:
            up = 108.0
            lo = 92.0
        pos = i % 6
        going_up = (i // 6) % 2 == 0
        c = lo + (up - lo) * pos / 5 if going_up else up - (up - lo) * pos / 5
        if i == n - 1:
            c = 109.0
        closes.append(c)
        highs.append(c + 0.3)
        lows.append(c - 0.3)
    return highs, lows, closes


class TestDiamond:
    def test_diamond_detection_and_strategy(self):
        highs, lows, closes = make_diamond()
        res = detect_diamond(highs, lows, closes)
        assert res.pattern in (PatternType.DIAMOND_BOTTOM, PatternType.DIAMOND_TOP)

        strat = DiamondStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(highs, lows, closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is not None


# ----------------------------------------------------------------------
# 8. Cup and Handle
# ----------------------------------------------------------------------
def make_cup_and_handle(n=55):
    highs, lows, closes = [], [], []
    for i in range(n):
        if i < 40:
            t = (i - 20) / 20.0
            c = 108.0 - 10.0 * (1 - t * t)
        elif i < 50:
            c = 107.5 - (i - 40) * 0.15 + (0.2 if i % 2 == 0 else -0.2)
        else:
            c = 109.5
        closes.append(c)
        highs.append(c + 0.3)
        lows.append(c - 0.3)
    return highs, lows, closes


class TestCupAndHandle:
    def test_cup_and_handle_detection_and_strategy(self):
        highs, lows, closes = make_cup_and_handle()
        res = detect_cup_and_handle(highs, lows, closes)
        assert res.pattern == PatternType.CUP_AND_HANDLE
        assert res.breakout_direction == "up"

        strat = CupAndHandleStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(highs, lows, closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is not None
        assert sig.direction == "long"


# ----------------------------------------------------------------------
# Robustness & Exception Safety
# ----------------------------------------------------------------------
class TestPatternRobustness:
    def test_detection_and_strategies_handle_exceptions(self):
        bad_highs = [100.0, float("nan"), 105.0]
        bad_lows = [95.0, 90.0, float("inf")]
        bad_closes = [98.0, 92.0, 100.0]

        res = detect_double_top_bottom(bad_highs, bad_lows, bad_closes)
        assert res.pattern == PatternType.NONE

        res = detect_cup_and_handle(bad_highs, bad_lows, bad_closes)
        assert res.pattern == PatternType.NONE

        strat = DoubleTopBottomStrategy()
        ctx = StrategyContext(symbol="BTC-USDT", timeframe="5m", candles=_make_candles(bad_highs, bad_lows, bad_closes))
        sig = pytest.importorskip("asyncio").run(strat.evaluate(ctx))
        assert sig is None
