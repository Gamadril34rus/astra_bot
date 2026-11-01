# ZEUS Channel Proxy v1 (owner 01.11)

Author (ZEVS) did not publish numeric lookback/ATR/%. Numbers below are **bot assumptions**.

## Canon (from lessons, not invented)
- Parallel channel = **clone** (shared slope)
- Significant point: price traveled to opposite boundary
- ≥3 touches + reaction; mid-channel entry forbidden
- Bounce preferred at 3–4th touch near boundary
- Breakout entry preferred as **first retest** of broken line + reaction
- After first retest worked, setup is dead
- Stop beyond significant extreme
- Naked breakout hold remains allowed (risky)

## bot_proxy v1 numbers
| Param | Value |
|-------|-------|
| lookback 4h | 48 |
| near boundary | ≤20% of **width** |
| parallel_tol | 0.25 + use_parallel_clone |
| retest tol | 0.15 × ATR(14) |
| retest done | ≥0.5 × width or 2 closes (spec; detector uses width) |
| min_stop_pct | 0.01 |
| ZEUS_RETEST_V1 | `shadow` (journal only) until flip to `live` |

## Not in this PR
- Wedge false-break requires div (PR-B)
- Structural TP as exit plan (PR-C)
- Multi-top + RSI gate
