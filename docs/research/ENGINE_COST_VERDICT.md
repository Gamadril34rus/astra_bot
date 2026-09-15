# Backtester / cost-model status (honest)

## Direct answer

Under **production-like** numbers (BingX ≈0.30% RT, RR 2.0–2.3, vol≥1.2), the **same research signals** show:

| Variant | OOS PF | mean R | P(PF≤1) bootstrap |
|---------|-------:|-------:|------------------:|
| BingX 0.30% RR1.5 vol1.3 | 1.13 | +0.07 | 0.30 |
| BingX 0.30% RR2.0 vol1.2 | 1.07 | +0.05 | 0.39 |
| BingX 0.30% RR2.3 vol1.2 | 1.10 | +0.07 | 0.34 |

**This is a soft / candidate edge, not a proven one.** At n≈80–90, CI still includes PF=1 with high probability. Saying “the config dies” is too strong; saying “it is **not** strong enough to justify raising risk or skipping probation” is accurate.

## Full `astra_bot/backtester/engine.py` run

**Not completed** with production strategy objects in this package.

Reasons:

1. Research signal ≠ `RangeBreakoutRetestStrategy` / `BookBreakoutStrategy` / `VolatilityBreakoutStrategy` (different range definition, ADX gate, book lookbacks).
2. Pattern strategies live under `decision/strategies/` via `StrategyAdapter` (`preferred_timeframe`), not the simple `BaseStrategy` registration path in `BacktestEngine.add_strategy`.
3. Correct acceptance test = wire **new 4h registry keys** → adapter → engine **or** paper-shadow, with cost_model BingX, clamp_take_rr 2.0–2.3, EV-gate, probation — after the 26–27.09 window.

**Valid result for decision-making today:** do not onboard as “proven edge”; onboard as **shadow candidates** only.

## What *was* verified

- Research bar-loop under 0.18% and 0.30% RT (tables above).
- OOS R distribution (bimodal, sd≈1.25).
- Multiple-testing inventory + bootstrap (PR #87 answers).
