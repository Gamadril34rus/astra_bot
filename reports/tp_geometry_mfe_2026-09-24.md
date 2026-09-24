# TP geometry from MFE — research for slice ~06–07.10

**Research-only.** Live contour not changed. Decision at owner slice.

Generated: 2026-09-24. Source: `models/paper_trades.jsonl` (n=240 closed).
Zeus paper closed (separate): n=18 — appendix only.

## 0. Diagnosis (owner brief)

- Clamp tail **2.0–2.3R** is rarely reachable (historical MFE≥1.8R ≈ 0–2%).
- Profile: small winners, larger losers; costs 0.14–0.27R on thin 5m stops.
- TRX 22.09 LOW_VOL + costs_r 0.273 would pass both entry gates if live — argument for enable ~30.09 **if** shadow criterion holds.

## 1. MFE / MAE by epoch

Epoch cuts (UTC): pre-#70 <11.09; #70→#76 11–13.09; #76→sprint 13–23.09; post-sprint ≥23.09.

| Epoch | n | MFE mean | p50 | p75 | p90 | max | ≥1R | ≥1.8R | ≥2R | MAE p50 | MAE p90 |
|-------|---|----------|-----|-----|-----|-----|-----|-------|-----|---------|---------|
| pre_#70 | 122 | 0.3701 | 0.2814 | 0.5599 | 0.8805 | 1.5911 | 5% | 0% | 0% | 0.2252 | 0.8295 |
| #70_to_#76 | 10 | 0.4181 | 0.2926 | 0.7067 | 0.9067 | 0.9368 | 0% | 0% | 0% | 0.2051 | 0.3475 |
| #76_to_sprint | 108 | 0.5234 | 0.2831 | 0.8529 | 1.3287 | 3.186 | 23% | 6% | 5% | 0.2817 | 0.8153 |
| post_sprint | 0 | — | — | — | — | — | — | — | — | — | — |
| **ALL** | 240 | 0.4411 | 0.2814 | 0.6959 | 1.0434 | 3.186 | 13% | 2% | 2% | 0.2488 | 0.8147 |

ALL MFE≥1.8R count: **6 / 240** (owner note 0/123 was early window; full ledger still ≈2.5%).

## 2. Pre-registered TP plans (fixed BEFORE sim)

Derived from **full-ledger MFE** percentiles only (no peek at plan PFs):
- Full-sample MFE p50=**0.28**, p75=**0.7**, p90=**1.04**

| Plan ID | Geometry | Rationale |
|---------|----------|-----------|
| CURRENT_clamp_2.0_2.3 | actual `r_multiple` under live single take ~2.0–2.3R | baseline |
| A_split_0.70_1.40 | 50% @ **0.70R** + 50% @ **1.40R** | owner example; near + stretch |
| B_split_p50_p75 | 50% @ **0.28R** + 50% @ **0.7R** | match empirical center/upper quartile |
| C_single_p75 | 100% @ **0.7R** | single reachable take (no 2R fantasy) |
| D_split_p50_p90 | 50% @ **0.28R** + 50% @ **1.04R** | keep a tail without requiring 2R |

Simulation rule (same family as `scripts/measure_rr_reach.py`): if MFE touched a leg, credit that leg’s R×fraction; **unfilled residual keeps actual realized R** (stops/safety as lived). Path order unknown → not a claim of fill certainty, comparative only.

## 3. Retrospective: plans vs CURRENT

### 3.1 Full ledger

| Plan | n | meanR | medianR | PF | WR | sumR |
|------|---|-------|---------|----|----|------|
| CURRENT_clamp_2.0_2.3 | 240 | -0.2699 | -0.2822 | 0.277 | 23.8% | -64.78 |
| A_split_0.70_1.40 | 240 | -0.2258 | -0.2786 | 0.387 | 28.8% | -54.20 |
| B_split_p50_p75 | 240 | -0.1640 | -0.1316 | 0.448 | 39.2% | -39.36 |
| C_single_p75 | 240 | -0.1945 | -0.2786 | 0.472 | 28.8% | -46.68 |
| D_split_p50_p90 | 240 | -0.1813 | -0.1329 | 0.392 | 38.3% | -43.52 |

### 3.2 By epoch (meanR / PF)

| Epoch | n | CURRENT | A_0.70_1.40 | B_p50_p75 | C_single_p75 | D_p50_p90 |
|-------|---|---------|-------------|-----------|--------------|-----------|
| pre_#70 | 122 | -0.323/0.152 | -0.266/0.264 | -0.156/0.408 | -0.213/0.394 | -0.196/0.284 |
| #70_to_#76 | 10 | -0.275/0.185 | -0.239/0.291 | -0.178/0.361 | -0.233/0.336 | -0.195/0.312 |
| #76_to_sprint | 108 | -0.210/0.463 | -0.180/0.535 | -0.172/0.478 | -0.171/0.557 | -0.163/0.505 |
| post_sprint | 0 | — | — | — | — | — |

### 3.3 After costs note

Paper `r_multiple` is already **net of fees/funding** in broker close. Costs_r at entry (0.14–0.27R on thin stops) is embedded in realized R, not subtracted again. Plans that bank 0.5–0.7R more often reduce the share of paths where costs dominate the whole R budget.

### 3.4 Realized profile (CURRENT)

- Winners: n=57, mean=+0.436R, median=+0.306R
- Losers: n=183, mean=-0.490R, median=-0.359R

### 3.5 Zeus paper closed (appendix)

n=18, MFE p50=0.205 p75=1.050 p90=4.885

| Plan | meanR | PF | WR |
|------|-------|----|----|
| CURRENT_clamp_2.0_2.3 | -0.214 | 0.587 | 39% |
| A_split_0.70_1.40 | -0.144 | 0.696 | 50% |
| B_split_p50_p75 | -0.261 | 0.431 | 50% |
| C_single_p75 | -0.195 | 0.589 | 50% |
| D_split_p50_p90 | -0.236 | 0.485 | 50% |

## 4. entry_gates shadow status (as of 2026-09-24)

- Observations with `rejection_stage=entry_gate` / `ENTRY_GATE_*`: **n=0**
- hypothetic_r sample size: **n=0**
- Criterion: **NOT YET** — no hypothetic_r rows in current NO_TRADE file.
- Note: `entry_gates_shadow_enabled=True` but gate only runs when a **candidate exists** past pipeline; most NO_TRADE are `NO_VALID_SETUP` / `LOW_EV` before gate. Need candidates that reach `process_symbol` entry-gate hook to accumulate hypothetic_r.
- Action: keep shadow on; re-check **2026-09-30** (n + median hypothetic_r). TRX-style LOW_VOL + high costs_r is exactly gate A/B payload once candidates flow.

Config still: `entry_gates_enabled=False`, block_regimes includes `LOW_VOLATILITY`, `entry_gate_max_costs_r=0.25`.

## 5. Slice recommendations (not enabled)

1. **Do not** put 2.0–2.3R single take as the only success metric — MFE distribution does not support it.
2. Candidate geometry for slice discussion: **A_split_0.70_1.40** or **C_single_p75** — compare PF/meanR in §3; pick only if post-sprint live window still agrees.
3. **entry_gates live** — only if 30.09 shadow report PASSes; TRX 22.09 is qualitative support, not a substitute for n≥30 median<0.
4. Entry quality still dominates TP geometry: LOW_VOL + weak strategy keys (see prior analysis). TP reshape without entry filter = smaller losses, not edge.

## 6. Repro

```bash
python scripts/measure_rr_reach.py models/paper_trades.jsonl
# this report: research one-shot; logic mirrored above
```

---
*No production toggle changed. Owner decision at slice ~06–07.10.*
