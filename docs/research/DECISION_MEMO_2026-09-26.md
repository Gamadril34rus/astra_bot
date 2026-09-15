# Decision memo — owner slice 26–27.09.2026

**Purpose:** one-page map of hypotheses, toggles, and what NOT to turn on.
**Live contour:** unchanged by research PRs #87/#88 until owner explicitly acts.

---

## 0) CI / merge status

| PR | Scope | CI | Action |
|----|-------|-----|--------|
| #87 | research walk-forward 4h breakout | research-only | merge when green (owner) |
| #88 | 4h onboarding package + Claude ingest + panel | **Quality gates green** | **готов к мержу** (owner merges) |

No live flags in either PR.

---

## 1) Hypotheses and status

| ID | Hypothesis | Status | Evidence |
|----|------------|--------|----------|
| H1 | 4h breakout+retest (research loop) has OOS edge | **ждёт данных** / soft | OOS n=81 PF 1.24 @0.18% RT; under BingX 0.30% PF~1.07–1.13, P(PF≤1)~0.3; CI includes 1.0 |
| H2 | Same edge under production exits (30/70+BE+mae_cut) + engine.py | **ждёт данных** | not run yet — acceptance of next *code* PR |
| H3 | New registry keys `*_4h` (range/book/vol) improve fill quality | **ждёт данных** | specs ready, enabled=false; path shadow→×0.25→n≥5 & live PF≥1 |
| H4 | Claude Group A (trend/cup/flag/rectangle) “revives” on 4h | **опровергнута (proxy)** | BingX 0.30% RT fixed RR=2 proxies PF 0.84–0.91 |
| H5 | Claude Group B (zscore/sweep/expanding) has directional edge on 4h | **опровергнута (proxy)** | PF 0.73–0.80, P(PF≤1)≈1 |
| H6 | tsm45_ls OOS PF 1.91 is general | **опровергнута как общий край** | Spec n_OOS=18 = anecdote; **panel 32 pairs** AGG OOS PF **0.85**, mean −0.006; OOS mean>0 only **16/32** (not majority) |
| H7 | Raising weights of existing 5m families fixes DD | **опровергнута (policy)** | kill-switch/probation stay; add families, don’t raise dead weights |
| H8 | entry_gates + htf_gate live would cut ~70% range losses | **ждёт решения владельца** | Claude diagnosis; **not** flipped in research PRs |

**Main finding for the slice (p.4):** old families do **not** revive on 4h under our costs (proxy map). Map compresses to: **new 4h keys + tsm45 (narrow) + wait**.

---

## 2) tsm45 panel (frozen spec, 32/35 pairs)

Params locked: lookback 270 bars 4h, band ±2%, flip exit, 6×ATR stop, fee RT 0.30%, no TP.

| Aggregate | n | PF | mean net |
|-----------|--:|---:|---------:|
| IS 2024-08-31→2025-08-31 | 738 | 1.25 | +0.013 |
| **OOS 2025-08-31→2026-08-31** | **826** | **0.85** | **−0.006** |
| Full 2021–2026 | 3480 | 1.12 | +0.006 |

- OOS mean>0 on **16/32** symbols — **not** a majority.
- Year 2025–2026 drag the panel negative; 2021/23/24 positive.
- Missing data (binance.us): TON, CRO, XMR.

**Verdict:** 18-trade OOS on BTC-centric lab was **luck relative to panel**. Do **not** grant 35-pair paper-shadow on tsm45 as a “more trades” fix. Optional: paper-shadow **only** on symbols with OOS mean>0 *and* n≥5 — still probation ×0.25, not portfolio-wide enable.

Detail: `docs/research/TSM45_PANEL_32.md` + `reports/tsm45_panel_by_symbol.csv`.

---

## 3) Toggles (what each switch does)

| Toggle | Current (as of research) | If ON | Recommend at slice |
|--------|--------------------------|-------|--------------------|
| `entry_gates_enabled` | False (shadow only) | Blocks high costs_r / bad regime entries | **Owner decision** (Claude: ON) — not research |
| `htf_gate_enabled` / shadow | mixed shadow | Filters candidates by HTF | Owner decision |
| `range_breakout_retest_4h` etc. | **false** | New 4h buckets | After slice: shadow only |
| `volatility_breakout` 5m | kill-switched | — | **Do not revive** |
| tsm45 multi-pair | lab only | Paper on many alts | **No** full 35; maybe majors subset |
| Stats reset | — | — | **Never** |
| Weight raise on dead 5m | — | — | **No** |

---

## 4) Inclusion criteria (post-slice)

1. Shadow → first fills → **probation ×0.25**
2. Promote only if **n≥5** and **live PF≥1** on that registry key
3. Separate 4h vs 5m stats (no mixing)
4. EV-gate + cost model BingX stay on
5. Full `engine.py` production-exit test before any weight > probation

---

## 5) Explicitly do NOT enable at slice

- Group B strategies (zscore, liquidity_sweep, expanding_triangle, …)
- Broad tsm45 on all 35 pairs
- Weight bumps / kill-switch bypass on 5m breakout
- min_rr jump alone without WR improvement (math still poor at WR 20%)
- Stats zeroing

---

## 6) One-line owner brief

> Research says: **don’t revive old 4h proxies under 0.3% costs**; **tsm45 is not a 35-pair edge** (OOS panel PF 0.85); keep path **new 4h keys in shadow + discipline on gates**; merge research PRs when CI green; live toggles only by explicit owner action after 26–27.09.
