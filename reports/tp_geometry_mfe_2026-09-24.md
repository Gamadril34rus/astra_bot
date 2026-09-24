# TP geometry from MFE — research for slice ~06–07.10

**Research-only.** Live contour not changed. Decision at owner slice.

Generated: 2026-09-24 (rev3 — funnel + Zeus P1). Source: `models/paper_trades.jsonl` (n=240 closed).

## 0. Diagnosis

- Clamp 2.0–2.3R rarely reachable (MFE≥1.8R = 6/240 ≈ 2.5%).
- CURRENT: winners +0.44R, losers −0.49R.
- Method closed: path-order opt/pess ranking unchanged; PRIMARY=C, BACKUP=A, DROP=B.

## 1–3. (see rev2) Epochs, pre-reg plans, retrospective

PRIMARY **C single 0.70R**; BACKUP **A 0.70/1.40**; true candidate = **bundle entry_gates (if PASS) + C**.

## 4. Bot health 23–24.09

State commits live; paper opens after 23.09 = 0; NO_TRADE ≈5031 (filters, not crash).

## 5. entry_gates shadow

hypothetic_r n=0 as of 24.09. Re-check 30.09.

## 6–9. P3 / P5 / post-sprint n rule / repro

(see rev2 / DECISION_MEMO)

## 10. Funnel / ARRIVAL RATE (24.09)

Main paper, 23.09–24.09 (~1.5d):

| Stage | Count |
|-------|------:|
| NO_TRADE observations | ~5031 |
| with `candidate` (mostly LOW_EV, **pre-gate**) | 258 (23.09) / 0 (24.09) |
| `ENTRY_GATE_*` / hypothetic_r | **0** |
| Paper opens | **0** |
| **Arrival at entry_gate stage** | **≈0 / day** |

Gate is at the end of the pipe; upstream filters starve it.  
Pre-registered fate (slice 06–07.10): if still n<30 **and** arrival <1/day → **gate redundant under current filters, keep shadow as insurance** (not infinite wait). Criterion n≥30 & median<0 is not cancelled — its honest outcome under hunger.

## 11. Zeus P1 acceptance

`zeus_trade_journal.jsonl`: **entry=47**, **stop_adjust=21**, exit=15; 3 open paper (LINK/BNB/ENA).  
Full cycle signal→entry→(stop_adjust)→journal — **P1 closed**.

---
*No production toggle changed.*
