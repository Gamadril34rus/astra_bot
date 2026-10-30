"""Multi-top breakdown: 2–3 flat highs → close under neckline → short."""
from __future__ import annotations

from astra_bot.strategies.zeus_multi_top_breakdown import (
    detect_multi_bottom_breakout,
    detect_multi_top_breakdown,
)


def _series_flat_tops_then_break():
    # Build synthetic: 3 tops ~100, neck ~95, then close 94
    highs, lows, closes = [], [], []
    # base grind
    for i in range(20):
        highs.append(96.0 + i * 0.01)
        lows.append(94.0 + i * 0.01)
        closes.append(95.0 + i * 0.01)
    # top1 at idx 22
    for _ in range(2):
        highs.append(99.5); lows.append(96.0); closes.append(98.0)
    highs.append(100.0); lows.append(96.5); closes.append(99.0)  # swing high
    for _ in range(2):
        highs.append(99.2); lows.append(96.0); closes.append(97.5)
    # dip
    for _ in range(3):
        highs.append(97.0); lows.append(95.2); closes.append(95.8)
    # top2
    for _ in range(2):
        highs.append(99.6); lows.append(96.0); closes.append(98.2)
    highs.append(100.2); lows.append(96.5); closes.append(99.1)
    for _ in range(2):
        highs.append(99.0); lows.append(95.5); closes.append(97.0)
    # dip neck area
    for _ in range(3):
        highs.append(96.5); lows.append(95.0); closes.append(95.5)
    # top3
    for _ in range(2):
        highs.append(99.8); lows.append(96.0); closes.append(98.5)
    highs.append(100.1); lows.append(96.2); closes.append(99.0)
    for _ in range(2):
        highs.append(98.5); lows.append(95.0); closes.append(96.0)
    # break bar: close under neck (~95)
    highs.append(95.5); lows.append(93.5); closes.append(94.0)
    return highs, lows, closes


def test_multi_top_breakdown_short():
    h, l, c = _series_flat_tops_then_break()
    sig = detect_multi_top_breakdown(h, l, c, min_tops=2, max_tops=3, radius=2)
    assert sig is not None, "expected short breakdown"
    assert sig.side == "short"
    assert sig.entry == 94.0
    assert sig.stop > sig.entry
    assert sig.take_profit < sig.entry
    assert sig.n_tops >= 2


def test_no_signal_without_break():
    h, l, c = _series_flat_tops_then_break()
    # remove break — last close still above neck
    c[-1] = 96.0
    h[-1] = 97.0
    l[-1] = 95.5
    sig = detect_multi_top_breakdown(h, l, c, min_tops=2, max_tops=3, radius=2)
    assert sig is None


def test_multi_bottom_breakout_long():
    # invert: flat lows ~100, neck resistance, close above
    lows, highs, closes = [], [], []
    for i in range(20):
        lows.append(104.0 - i * 0.01)
        highs.append(106.0 - i * 0.01)
        closes.append(105.0 - i * 0.01)
    # bottom1
    for _ in range(2):
        lows.append(100.5); highs.append(104.0); closes.append(102.0)
    lows.append(100.0); highs.append(103.5); closes.append(101.0)
    for _ in range(2):
        lows.append(100.8); highs.append(104.0); closes.append(102.5)
    for _ in range(3):
        lows.append(102.0); highs.append(105.0); closes.append(103.5)
    # bottom2
    for _ in range(2):
        lows.append(100.4); highs.append(104.0); closes.append(102.0)
    lows.append(99.9); highs.append(103.5); closes.append(101.2)
    for _ in range(2):
        lows.append(101.0); highs.append(105.0); closes.append(103.0)
    for _ in range(3):
        lows.append(102.5); highs.append(105.5); closes.append(104.0)
    # break up
    lows.append(104.0); highs.append(107.0); closes.append(106.5)
    sig = detect_multi_bottom_breakout(highs, lows, closes, min_bottoms=2, max_bottoms=3, radius=2)
    assert sig is not None
    assert sig.side == "long"
    assert sig.entry > sig.stop
