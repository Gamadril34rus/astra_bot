# TP geometry from MFE — research for slice ~06–07.10

**Research-only.** Live contour not changed. Decision at owner slice.

Generated: 2026-09-24 (rev2 — path-order + ranking). Source: `models/paper_trades.jsonl` (n=240 closed).
Zeus paper closed (appendix): n=18.

## 0. Diagnosis (owner brief)

- Clamp tail **2.0–2.3R** is rarely reachable (MFE≥1.8R = **6/240 ≈ 2.5%**).
- Profile CURRENT: winners mean **+0.44R** (med +0.31), losers mean **−0.49R** (med −0.36).
- Costs 0.14–0.27R on thin 5m stops; TRX 22.09 LOW_VOL + costs_r 0.273 is qualitative support for entry_gates, not a substitute for n≥30.

## 1. MFE / MAE by epoch

Epoch cuts (UTC): pre-#70 <11.09; #70→#76 11–13.09; #76→sprint 13–23.09; post-sprint ≥23.09.

| Epoch | n | MFE mean | p50 | p75 | p90 | max | ≥1R | ≥1.8R | ≥2R | MAE p50 | MAE p90 |
|-------|---|----------|-----|-----|-----|-----|-----|-------|-----|---------|---------|
| pre_#70 | 122 | 0.3701 | 0.2814 | 0.5599 | 0.8805 | 1.5911 | 5% | 0% | 0% | 0.2252 | 0.8295 |
| #70_to_#76 | 10 | 0.4181 | 0.2926 | 0.7067 | 0.9067 | 0.9368 | 0% | 0% | 0% | 0.2051 | 0.3475 |
| #76_to_sprint | 108 | 0.5234 | 0.2831 | 0.8529 | 1.3287 | 3.186 | 23% | 6% | 5% | 0.2817 | 0.8153 |
| post_sprint | **0** | — | — | — | — | — | — | — | — | — | — |
| **ALL** | 240 | 0.4411 | 0.2814 | 0.6959 | 1.0434 | 3.186 | 13% | 2% | 2% | 0.2488 | 0.8147 |

## 2. Pre-registered TP plans (fixed BEFORE sim)

Full-sample MFE: p50=**0.28**, p75=**0.70**, p90=**1.04**

| Plan ID | Geometry | Status for slice |
|---------|----------|------------------|
| CURRENT_clamp_2.0_2.3 | actual `r_multiple` | baseline |
| A_split_0.70_1.40 | 50% @ 0.70R + 50% @ 1.40R | **BACKUP** |
| B_split_p50_p75 | 50% @ 0.28R + 50% @ 0.70R | **DROPPED** — 0.28R leg ≈ round-trip costs, not robust |
| C_single_p75 | 100% @ **0.70R** | **PRIMARY** |
| D_split_p50_p90 | 50% @ 0.28R + 50% @ 1.04R | tail undervalued by truncation; not primary |

### 2.1 Path-order assumption (optimistic vs pessimistic)

Only MFE/MAE/final R are observed — **intrabar path order is unknown**.

| Assumption | Rule |
|------------|------|
| **Optimistic (rev1)** | if `mfe ≥ TP_leg` → credit that leg; residual keeps actual R |
| **Pessimistic (mirror)** | if `mae_r ≥ 0.99` or `r ≤ −0.95` → treat as **stop-first**, **no TP rewrite** (keep actual R) |

Result on full ledger (n=240):

| Plan | OPT meanR / PF | PESS meanR / PF |
|------|----------------|-----------------|
| CURRENT | −0.270 / 0.277 | −0.270 / 0.277 |
| A 0.70/1.40 | −0.226 / 0.387 | −0.226 / 0.387 |
| B p50/p75 | −0.164 / 0.448 | −0.180 / 0.426 |
| **C single 0.70** | **−0.195 / 0.472** | **−0.195 / 0.472** |
| D p50/p90 | −0.181 / 0.392 | −0.197 / 0.372 |

**Ranking unchanged under both assumptions:** C ≥ B ≥ A/D ≥ CURRENT by PF. Question closed.

### 2.2 Truncation bias (MFE censored by current exits)

MFE is measured under the **live** exit plan (early stop / safety / current take). Tail legs (A’s 1.40R, D’s 1.04R) are **underestimated**: paths that would have continued after a nearer bank are cut. Therefore:

- Prefer **C** (single 0.70R) — less sensitive to missing tail path.
- Keep **A** as backup only if post-sprint window still favors a stretch leg.
- Drop **B**: near-field 0.28R sits on the cost floor.

## 3. Retrospective summary

| Plan | n | meanR | PF | WR | sumR |
|------|---|-------|----|----|------|
| CURRENT | 240 | −0.270 | 0.28 | 24% | −65 |
| A | 240 | −0.226 | 0.39 | 29% | −54 |
| B (dropped) | 240 | −0.164 | 0.45 | 39% | −39 |
| **C PRIMARY** | 240 | −0.195 | **0.47** | 29% | −47 |
| D | 240 | −0.181 | 0.39 | 38% | −44 |

All plans still **negative expectancy** on this sample. TP reshape without entry filter compresses the loss; it does not create edge.

### 3.1 True slice candidate = BUNDLE

> **PRIMARY bundle = `entry_gates` ON (only if 30.09 shadow PASS) + plan C (single 0.70R).**  
> Either piece alone does not fix the system: gates without reachable take still leave cost-dominated R; nearer take without gates still enters LOW_VOL / dead keys.

## 4. Bot health 23–24.09 (not silent breakage)

| Check | Result |
|-------|--------|
| State commits on master | yes (chore(ci) every ~5m through 24.09 04:05Z) |
| Paper closed trades after 23.09 | **n=0** |
| NO_TRADE observations 23–24.09 | **n=5031** (bot ticking) |
| Breakdown | `no_strategy_signal` ~3985; `LOW_EV` ~841; `rr_too_low` ~444 (min_rr=2.0 filter); `LOW_LIQUIDITY` ~153 |
| Open paper positions | 0 |
| Zeus journal | rejects / structure_state / ticks active; 3 open zeus paper positions |
| halt_alerts | weekly loss mark 15.09 only — not a full halt |

**Conclusion:** no silent crash. Zero post-sprint closes = **filters working** (min_rr, denylist, EV, no signal) + sparse setups, not a dead process.

## 5. entry_gates shadow (24.09) — re-check **30.09**

| Metric | Value |
|--------|-------|
| `rejection_stage=entry_gate` / hypothetic_r | **n=0** |
| Criterion n≥30 & median(hypothetic_r)<0 | **NOT YET** |
| Why empty | gate runs only when a **candidate** reaches `process_symbol`; most NO_TRADE die earlier (`NO_VALID_SETUP` / `LOW_EV`) |

Owner enables live only after 30.09 report PASSes.

## 6. P3 — HTF shadow (package)

| Item | Status |
|------|--------|
| `htf_shadow_enabled` | True |
| `htf_hard_gate_enabled` | **False** |
| Journal | `models/htf_shadow_bans.jsonl` ≈ **5000** rows |
| Mix | mostly `scalp5m` counter-HTF (~4840); also ob_swing, funding_rate_contrarian, … |
| Slice ask | compare «would have banned» vs actual PnL of those candidates; enable hard only if window 23.09–06.10 does not zero remaining edge |

## 7. P5 — slice package checklist

1. This report + DECISION_MEMO sync (windows, TP geometry rank, no third mode flip).  
2. Decisions: entry_gates (30.09) / TP plan C vs A / HTF hard / cooldown-A2 / 5m leg / denylist stay / stats / risk.  
3. `scripts/measure_two_week_review.py` on 13–23.09 vs 23.09–slice.  
4. post-sprint n rule (below).

## 8. Post-sprint n=0 — pre-registered window rule

**Fixed in advance (2026-09-24):**

- If by **26–27.09** closed trades in mode-23.09 still **n<10** → **extend measurement window to ~06–07.10 without changing rules**.  
- **Third mode change in one month = nullifies conclusions** — do not retune min_rr / denylist / gates mid-window just to force n.

## 9. Repro

```bash
python scripts/measure_rr_reach.py models/paper_trades.jsonl
```

---
*No production toggle changed. Owner decision at slice ~06–07.10; entry_gates call at 30.09 shadow re-measure.*
