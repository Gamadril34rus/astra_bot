# Decision memo — owner slice 26–27.09.2026

**One document = one truth.** Live contour not changed by research PRs.

---

## Hypotheses

| ID | Hypothesis | Status | Why |
|----|------------|--------|-----|
| H-tsm45 | TSM45 is a general multi-pair edge | **ОПРОВЕРГНУТА** | Panel 32 pairs OOS PF **0.85**, mean −0.006; 16/32 coin-flip. Narrow shadow on OOS-winners **forbidden** (OOS selection = curve-fit). Full 35-pair shadow expected ~0 — **do not spend risk budget**. |
| H-family-A | trend/cup/flag/rectangle revive on 4h | **ОПРОВЕРГНУТА (proxy)** | Under BingX 0.30% RT + fixed RR≈2: PF 0.84–0.91 |
| H-family-B | zscore/sweep/expanding have directional edge on 4h | **ОПРОВЕРГНУТА (proxy)** | PF 0.73–0.80, P(PF≤1)≈1 |
| H-breakout-4h | research breakout+retest OOS edge | **шум с намёком** | Under our costs P(PF≤1) **0.30–0.39**; risk **not** raised; only shadow `*_4h` keys after slice |
| H-entry-gates | shadow rejects have median hypothetic_r < 0 | **ждёт данных** | Criterion **pre-registered**: `median(hypothetic_r)<0` and **n≥30** → candidate enable. Tool: `scripts/research/entry_gate_shadow_report.py` |
| H-track-C Z3/Z4/Z6/Z7 | Zeus lessons edge on deep history | **ждёт данных** | Self-test 10/10; full OOS table = lab host run (see `reports/track_c_deep_history_2026-09-16.md`) |
| H-cooldown-A2 / 5m leg | — | **только живые данные среза** | Not decided by research backtests |

**Main map compression:** old 4h families dead under our costs; tsm45 closed; path = **discipline (gates) + new 4h keys in shadow + Track C if lab confirms**.

---

## Toggles

| Toggle | Research recommendation at slice |
|--------|----------------------------------|
| `entry_gates_enabled` | Enable **only if** shadow report PASS (median&lt;0, n≥30) |
| `htf_gate` live | Owner call; Claude argues ON |
| `*_4h` breakout keys | `enabled=false` → shadow after slice |
| tsm45 multi-pair | **OFF / closed** |
| 5m volatility_breakout | stay kill-switched |
| Stats reset | **never** |
| Weight raise on dead names | **no** |

---

## Inclusion criteria (post-slice)

1. Shadow → fills → probation ×0.25  
2. Promote iff **n≥5** and **live PF≥1** on that key  
3. No 5m/4h stat mixing  
4. No OOS symbol cherry-pick  
5. engine.py production-exit sweep = acceptance of **next code-PR** (not required to hold the slice if time runs out)

---

## Do NOT enable

- Group B strategies  
- tsm45 on 35 pairs (or any OOS-selected subset)  
- Weight bumps / kill-switch bypass  
- Stats zeroing  

---

## One-liner

> tsm45 dead as general edge; old 4h families dead under 0.3% costs; breakout = noise-with-hint only in shadow; entry_gates only if pre-registered shadow criterion holds; Track C and 5m/cooldown = live slice data + lab Track C JSON.
