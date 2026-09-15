# Answers to merge blockers (PR #87 follow-up)

Research remains research-only. These answers are the gate for any future **config-PR**, not for merging #87.

---

## 1) Multiple testing + bootstrap

### How many configs were explored before “winner”?

Honest inventory (not a single pre-registered hypothesis):

| Stage | What was varied | Count |
|-------|-----------------|------:|
| Primary screen | strategy ∈ {ema, break} × TF ∈ {1h,4h} × RR ∈ {1.5,2.0,2.5} | **12** |
| Volume sensitivity (ema 4h only) | vol ∈ {1.0, 1.2, 1.4, 1.6, 2.0} | 5 |
| ATR sensitivity (ema 4h only) | atr_mult ∈ {1.0, 1.5, 2.0, 2.5} | 4 |
| Walk-forward candidate set | 5 fixed tuples (break/ema × RR × vol) | **5** |

Selection rule on the walk-forward set: among configs with **OOS PF > 1 and OOS meanR > 0**, pick max OOS PF → `break RR=1.5 vol≥1.3 ATR×1.5`.

**Caveat:** the primary 12-cell screen and the vol/ATR grids were looked at *before* the formal walk-forward. This is **not** a single pre-specified test. Treat the OOS number as **hypothesis-generating**, not confirmatory. A conservative view is ~12–26 “looks” depending on whether vol/ATR grids count as independent families.

### Bootstrap 95% CI on OOS trades (n=81)

Method: **20 000** i.i.d. resamples of the OOS R-multiple vector, **seed=42** (same spirit as track_c_replay).

**Research cost model (0.18% RT)** — original winner:

| Metric | Point | 95% CI | P(null) |
|--------|------:|--------|---------|
| PF | 1.238 | **[0.798, 1.931]** | P(PF ≤ 1) = **0.171** |
| mean R | +0.131 | **[−0.136, +0.402]** | P(meanR ≤ 0) = **0.171** |

**Conclusion:** at n=81, PF 1.24 is **not** statistically distinguishable from 1.0 at 95% confidence. CI includes values well below 1. Edge is *suggestive*, not proven. Do **not** raise live risk on this alone.

---

## 2) Compatibility with production pipeline

### Parameter mismatch (explicit)

| Parameter | Research script | Production pipeline |
|-----------|-----------------|---------------------|
| Round-trip costs | 0.18% | BingX preset ≈ **0.30%** (2×(0.05% fee + 0.1% slip)) |
| Take RR | **1.5** | `clamp_take_rr` **2.0–2.3** |
| Volume threshold | **1.3×** SMA20 | often **1.2×** (where used) |
| Probation | none | **×0.25** until n≥5 |
| EV-gate | none | prior with **both-side** costs |
| Partial exits | single full TP | 30/70 + BE after first scale |
| Engine | custom bar loop | `astra_bot/backtester/engine.py` + strategies |

### Sensitivity of the *same* OOS signals under pipeline-like numbers

Same breakout-retest 4h logic; only cost/RR/vol changed; bootstrap seed=42, 20k:

| Variant | n | PF | mean R | PF 95% CI | P(PF≤1) |
|---------|--:|---:|-------:|-----------|--------:|
| research (0.18%, RR1.5, vol1.3) | 81 | 1.24 | +0.13 | [0.80, 1.93] | 0.17 |
| **BingX 0.30%, RR1.5, vol1.3** | 81 | **1.13** | **+0.07** | **[0.72, 1.75]** | **0.30** |
| BingX 0.30%, RR2.0, vol1.2 | 88 | 1.07 | +0.05 | [0.68, 1.63] | 0.39 |
| BingX 0.30%, RR2.3, vol1.2 | 86 | 1.10 | +0.07 | [0.69, 1.69] | 0.34 |
| BingX 0.30%, RR2.0, vol1.3 | 80 | 1.08 | +0.05 | [0.67, 1.68] | 0.38 |

Under production costs, point PF stays >1 but **uncertainty widens** (P(PF≤1) ≈ 0.30–0.39). Research script ≠ `engine.py` (fill/exit semantics, partials, EV-gate).

### Status of “run through OUR backtester”

**Not done in this PR.** Reasons (honest):

1. Full `BacktestEngine` needs strategy objects, RiskEngine, cost_model, candle model — research CSVs must be adapted to loader contract.
2. Existing strategies (`range_breakout_retest`, `book_breakout`, `volatility_breakout`) are **not** identical to the simplified 20-bar breakout+retest used in research.
3. Running them correctly is the job of the **next** PR (enabled=False 4h specs → paper shadow → measured PF), not a silent re-label of research numbers.

**Acceptance criterion for config-PR:** offline `engine.py` and/or paper-shadow of the **new 4h strategy instances** with BingX cost preset, clamp_take_rr 2.0–2.3, EV-gate on, reporting PF / mean R / bootstrap CI the same way as track_c.

---

## 3) Live context — path, not weight bump

Agreed: `volatility_breakout` killed on 5m (PF 0.00) must **not** be “rescued” by raising weight. Correct path:

```
enabled=False 4h-only strategy specs
  → paper-shadow (log only)
  → probation ×0.25 when first fills appear
  → promotion only if n≥5 AND live PF≥1
```

Do **not** apply before the **26–27.09** fix window ends.

### Spec stubs (NOT enabled)

See [SPECS_4H_BREAKOUT_FAMILY.md](SPECS_4H_BREAKOUT_FAMILY.md).

| Spec name | preferred_timeframe | enabled | Notes |
|-----------|---------------------|---------|-------|
| `range_breakout_retest_4h` | `"4h"` | **False** | separate bucket; volume gate ≥1.3; min_rr 1.5 |
| `book_breakout_4h` | `"4h"` | **False** | book rules on 4h only |
| `volatility_breakout_4h` | `"4h"` | **False** | **new name** — no shared stats with 5m corpse |

Onboarding remains standard. No weight changes, no kill-switch bypass.

---

## Bottom line for config-PR

1. Multiple testing is real; OOS CI includes PF=1 → research edge is **candidate**, not proven.
2. Under BingX 0.30% RT the point estimate softens (PF ~1.07–1.13); still needs **engine.py** / paper-shadow confirmation.
3. Implementation path = **new 4h-only strategy instances**, enabled=False, after 26–27.09 window — not a weight raise on dead 5m buckets.

Merge of #87 as research-only remains appropriate once CI is green (no live bot code touched).
