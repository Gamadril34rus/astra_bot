# Spec stubs: 4h breakout family (enabled=False)

**Status:** documentation only. Do **not** enable before the 26–27.09 rule-fix window ends.
**Path:** enabled=False → paper-shadow → probation ×0.25 → promote iff n≥5 and live PF≥1.

These are **separate strategy names / buckets** from any 5m instances (especially `volatility_breakout` which is kill-switched on 5m with PF 0.00). Stats must not be mixed.

---

## 1. `range_breakout_retest_4h`

```yaml
name: range_breakout_retest_4h
enabled: false
preferred_timeframe: "4h"
# Inherit logic from range_breakout_retest with:
range_min_bars: 15
adx_threshold: 20.0
retest_tolerance: 0.002
stop_buffer_pct: 0.002
min_rr: 1.5
# Research-inspired gates (optional until engine confirms):
volume_sma_period: 20
volume_min_ratio: 1.3   # signal bar volume / SMA20
# Risk / pipeline (unchanged from global):
# - clamp_take_rr stays 2.0–2.3 until paper proves 1.5 better under BingX costs
# - probation ×0.25 until n>=5
# - EV-gate with both-side costs ON
```

## 2. `book_breakout_4h`

```yaml
name: book_breakout_4h
enabled: false
preferred_timeframe: "4h"
# Existing BookBreakoutConfig defaults, forced 4h feed only:
level_lookback: 48
breakout_lookback: 12
retest_tolerance_atr: 0.75
max_retest_depth_atr: 1.5
stop_buffer_atr: 0.5
min_rr: 1.0
rsi_overbought: 70.0
rsi_oversold: 30.0
min_bars: 80
volume_min_ratio: 1.2   # start at pipeline-like; can raise to 1.3 after shadow
```

## 3. `volatility_breakout_4h`

```yaml
name: volatility_breakout_4h
enabled: false
preferred_timeframe: "4h"
# MUST be a new registry key — do not reuse 5m volatility_breakout stats
# Parameters: copy current volatility_breakout defaults, then only change TF binding
# Kill-switch state of 5m instance does NOT apply to this name
volume_min_ratio: 1.3
```

---

## Onboarding checklist (standard)

1. Spec landed with `enabled: false` (this doc / future PR).
2. Wire into pipeline with **shadow-only** logging (no orders).
3. After fix window: enable shadow → collect NO_TRADE + would-be outcomes if available.
4. First real paper fills: **probation ×0.25**.
5. Promotion gate: **n ≥ 5** and **live/paper PF ≥ 1** on position-unique ANY bucket.
6. No automatic weight increase of legacy 5m breakout names.

## Explicit non-goals

- Do not “raise weight” of dead 5m `volatility_breakout`.
- Do not merge 5m and 4h statistics into one PF.
- Do not disable EV-gate or probation for these specs.
