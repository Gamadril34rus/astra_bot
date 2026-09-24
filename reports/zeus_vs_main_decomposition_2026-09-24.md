# ZEUS vs MAIN — gap decomposition (collect, no judgment)

**Research-only.** Snapshot 2026-09-24. Live not touched.

Companion to `reports/zeus_vs_main_2026-09-24.md`.

## 1. Where the gap is *not*

| Factor | Finding |
|--------|---------|
| **TF mix** | All **18** ZEUS closed are **`timeframe=4h`**. No junior-TF ZEUS closes in ledger → gap is not “4h vs 15m mix inside Zeus”. |
| **Filters (MAIN)** | MAIN post-sprint opens = 0 (min_rr / LOW_EV / denylist). ZEUS runs a **separate** paper path — different funnel, not the same entry_gates arrival problem. |
| **Take reachability** | ZEUS **higher** MFE reach than MAIN: med MFE 0.21 vs 0.28, but **≥0.7R 39% vs 24%**, **≥1R 33% vs 13%**, **≥2R 22% vs 2%**. So ZEUS is *not* worse on “can’t reach take”; if anything the opposite on this small n. |

## 2. Where the gap *is* (structural)

### 2.1 Hold time

| | MAIN | ZEUS |
|--|-----:|-----:|
| hold median (h) | 0.35 | **3.91** |
| hold mean (h) | 0.70 | **8.13** |

ZEUS holds on 4h structure; MAIN is dominated by short 5m holds. That alone changes MAE/MFE path and exit mix — not a pure skill ranking.

### 2.2 costs_r vs stop width (ZEUS) — main vulnerability signal

All ZEUS rows have `costs_r`. Correlation:

| Relationship | Value |
|--------------|------:|
| corr(**costs_r**, stop_pct) | **−0.61** |
| corr(**costs_r**, 1/stop_pct) | **+0.99** |

Interpretation: **narrower structural stop → costs eat a larger share of 1R**. Median costs_r **0.33** is not a mysterious 4h fee bug; it tracks **stop_pct often <1%** on `zeus_channel_boundary_4h`.

| Strategy | n | meanR | costs_r med | stop_pct med |
|----------|--:|------:|------------:|-------------:|
| zeus_channel_boundary_4h | 15 | −0.21 | **0.37** | **0.71%** |
| zeus_wedge_retest_4h | 3 | −0.22 | **0.05** | **5.93%** |

Examples: AVAX stop_pct **0.17%** → costs_r **1.11**; ETH/XRP/ENA with stop_pct **6–12%** → costs_r **0.025–0.05**.

MAIN (n=108 with both fields): stop_pct med **1.38%**, costs_r med **0.20**, same inverse pattern (corr −0.79).

**Hypothesis for slice (not enabled):** ZEUS channel-boundary stops that are structurally tight make round-trip costs a large fraction of R — primary fragility on this sample, not “4h can’t trend.”

### 2.3 Exit mix (context)

ZEUS: stop_loss 10 / tp1+tp2 6 / mae_cut 2.  
MAIN: stop_loss 183 / safety heavy / few TPs.  
Different engines; compare with hold + costs, not exit labels alone.

## 3. Open ZEUS risk (LINK / BNB / ENA) — bounded profile

| Symbol | Dir | risk_budget / equity_before | binding | lev | margin_used |
|--------|-----|----------------------------:|---------|----:|------------:|
| LINK-USDT | short | **1.00%** | probation | 3 | ~10 |
| BNB-USDT | short | **1.00%** | probation | 3 | ~17 |
| ENA-USDT | long | **1.00%** | probation | 1 | ~50 |

- Sum risk_budget ≈ **3.0%** of equity (~1995–1996) if all three stopped at full budget.  
- `binding_constraint=probation` on all three — size capped.  
- realized_pnl = sum(closed) ≈ −4.37; no ledger drift.  

Paper-only, but **profile is risk-limited per name and in aggregate**, not unconstrained stack.

## 4. Pace toward 30–40 closed by 06–07.10

| | |
|--|--:|
| Closed now | 18 |
| Rate (span to 24.09) | ~**7.6–8.2 / day** |
| Days to 06.10 | ~12 |
| Projection at same rate | **well above 30–40** |

Target 30–40 is plausible if clock keeps running; **refresh this table in the last day before slice**. If rate collapses, report actual n without forcing trades.

## 5. Decomposition one-liner (collect)

> Gap MAIN vs ZEUS on this snapshot is mostly **different product** (5m MAIN vs 4h ZEUS, hold×10) plus ZEUS **tight channel stops → high costs_r/R**; not “ZEUS can’t reach takes.” Filters starve MAIN post-sprint; ZEUS path is separate. Recompute at slice with n≥30.

---
*Calendar unchanged: 26–27.09 n-check; 30.09 gates shadow; 06–07.10 slice. Live not touched.*
