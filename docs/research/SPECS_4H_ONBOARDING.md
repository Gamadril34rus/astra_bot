# Onboarding package: 4h breakout family (enabled=false)

**Do not enable before slice 26–27.09.**
Path after window: shadow → probation ×0.25 → promote only if n≥5 and live PF≥1.

**Principle:** ADD new trading families (new registry keys). Do NOT raise weights of existing 5m buckets. Do NOT mix 5m and 4h stats.

---

## Registry keys (new names)

| name | preferred_timeframe | enabled | inherits logic from |
|------|---------------------|---------|---------------------|
| `range_breakout_retest_4h` | `4h` | **false** | `range_breakout_retest` |
| `book_breakout_4h` | `4h` | **false** | `book_breakout` |
| `volatility_breakout_4h` | `4h` | **false** | `volatility_breakout` (new key — 5m PF=0 stays dead) |

Adapter: `decision/strategies/adapter.py` already reads `preferred_timeframe`.

---

## Spec YAML (config layer — draft)

```yaml
# config/strategies_4h_onboarding.yaml  (NOT loaded in prod until after window)
range_breakout_retest_4h:
  enabled: false
  preferred_timeframe: "4h"
  weight: 1.0
  range_min_bars: 15
  adx_threshold: 20.0
  retest_tolerance: 0.002
  stop_buffer_pct: 0.002
  min_rr: 1.5
  volume_sma_period: 20
  volume_min_ratio: 1.3
  # pipeline globals still apply: clamp_take_rr 2.0-2.3, EV-gate, probation

book_breakout_4h:
  enabled: false
  preferred_timeframe: "4h"
  weight: 1.0
  level_lookback: 48
  breakout_lookback: 12
  retest_tolerance_atr: 0.75
  max_retest_depth_atr: 1.5
  stop_buffer_atr: 0.5
  min_rr: 1.0
  volume_min_ratio: 1.2

volatility_breakout_4h:
  enabled: false
  preferred_timeframe: "4h"
  weight: 1.0
  # copy defaults from volatility_breakout; ONLY change name + TF binding
  volume_min_ratio: 1.3
  # kill-switch state of 5m volatility_breakout does NOT apply to this key
```

---

## Implementation checklist (post 26–27.09)

1. Add three config entries + strategy factory registration under **new names**
2. Bind candle feed to 4h only via `preferred_timeframe`
3. Shadow logger: write would-be signals without orders
4. Separate stats buckets (position-unique ANY) per name
5. First fills → automatic probation ×0.25
6. Promotion gate: n≥5 and PF≥1 on that bucket only
7. No automatic weight change of any 5m strategy

## Cost verdict (research signals, not engine.py)

Under BingX 0.30% RT the simplified breakout-retest is **soft** (PF≈1.07–1.13, P(PF≤1)≈0.30–0.39).
Valid outcome: **shadow-only onboarding**, not immediate promotion.
