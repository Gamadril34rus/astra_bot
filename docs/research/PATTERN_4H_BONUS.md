# Bonus: pattern family on 4h (inventory + screening notes)

## Inventory in `decision/strategies/pattern_strategies.py`

**16 strategy classes** in `ALL_PATTERN_STRATEGIES`:

1. FallingWedgeStrategy
2. RisingWedgeStrategy
3. AscendingTriangleStrategy
4. DescendingTriangleStrategy
5. SymmetricalTriangleStrategy
6. HeadShouldersStrategy
7. DoubleTopBottomStrategy
8. TripleTopBottomStrategy
9. RectangleStrategy
10. ExpandingTriangleStrategy
11. FlagStrategy
12. PennantStrategy
13. DiamondStrategy
14. CupAndHandleStrategy
15. RoundedTopStrategy
16. RoundedBottomStrategy

**21 PatternType values** (excluding NONE), including inverted H&S, triple top/bottom, diamond top/bottom, inverted cup.

(User “18 patterns” ≈ this set; tree currently has 16 strategy wrappers / 21 types.)

## Do they “live” on 4h?

Formal end-to-end detector+trade backtest on 4h **not run** in this package (detectors are heavy, need StrategyContext + full candle model).

**Proxy screen (range compression → breakout)** on BTC/ETH/SOL 4h 2021–2026:

| Symbol | Compress bars | Breakout events | ~per year |
|--------|--------------:|----------------:|----------:|
| BTC | 1443 | 215 | ~38 |
| ETH | 1435 | 217 | ~38 |
| SOL | 1365 | 228 | ~40 |

Structure-breakout *events* are frequent enough on 4h to feed **new** pattern/breakout buckets without starving the book.

## Practical recommendation

Fastest path to more *quality* fills after the fix window:

1. Onboard **`range_breakout_retest_4h` + `book_breakout_4h`** first (closest to research + lessons).
2. Keep **`volatility_breakout_4h`** as separate key in shadow only.
3. Optionally add **one** pattern wrapper with `preferred_timeframe="4h"` for less noisy types (rectangle / flag / double top-bottom) — still new names, still probation.

Do **not** promote any pattern that was kill-switched on 5m by reusing the same registry name on 4h.
