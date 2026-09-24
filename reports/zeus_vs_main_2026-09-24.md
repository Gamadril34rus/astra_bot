# ZEUS vs MAIN — one template (collect for slice 06–07.10)

**Research-only.** No judgment until slice. Live contour not changed.
Snapshot: 2026-09-24. MAIN `paper_trades` n=240; ZEUS `zeus_paper_trades` n=18 (ledger now **18**, was ~15 earlier).

## 1. Shared template

| Metric | MAIN | ZEUS (all) | **Zeus wedge-only** | Zeus channel-only |
|--------|-----:|----------:|--------------------:|------------------:|
| n closed | 240 | 18 | **3** | 15 |
| meanR net | -0.2699 | -0.2137 | **≈ −0.22** | ≈ −0.21 |
| medianR | -0.2822 | -0.1647 | — | — |
| PF | 0.277 | 0.587 | **(n мал — знак)** | (n мал — знак) |
| WR | 23.8% | 38.9% | — | — |
| sumR | -64.78 | -3.85 | — | — |
| hold time median (h) | 0.35 | 3.91 | — | — |
| hold time mean (h) | 0.70 | 8.13 | — | — |
| costs_r median (n with field) | 0.203 (n=108) | 0.334 (n=18) | **0.05** | **0.37** |
| costs_r mean | 0.250 | 0.366 | — | — |
| stop_pct median | 1.38% | — | **5.93%** | **0.71%** |

**Slice rule (pre-reg 24.09):** судить Zeus **постратегийно**; строка **Zeus wedge-only** — чистый урок Зевса без примеси channel. n мал → важен знак, не p-value.

### exit_reason

| Reason | MAIN | ZEUS |
|--------|-----:|-----:|
| stop_loss | 183 | 10 |
| VOL_EXPANSION | 29 | 0 |
| tp1 | 11 | 3 |
| tp2 | 3 | 3 |
| mae_cut | 9 | 2 |
| regime_exit | 5 | 0 |

Notes: `r_multiple` is broker net (fees/funding inside). ZEUS holds longer (4h structure vs MAIN mostly 5m). **Do not rank systems until slice** — ZEUS n still small.

## 2. Zeus hygiene

### 2.1 Entry uniqueness (47 raw journal entries)

| View | n |
|------|--:|
| Raw `event=entry` | 47 |
| Unique after collapse same symbol/dir/strategy within **5 min** | **23** |

Near-duplicates on adjacent ticks are real. **Use unique ≈ 23 for setup counts**, not raw 47.

### 2.2 Stops & costs

- Closed ZEUS `stop_loss`: present; several fills at `initial_stop` mark
- `costs_r` on **18/18** closed (median 0.334)
- `stop_adjust` in journal: **21** (stop_sync / sanitize_tight_stop) — stops are managed, not labels only
- **Единая гипотеза издержек:** costs_r ≈ 0.3%/stop_pct (corr vs 1/stop_pct = +0.99). См. `docs/research/UNIFIED_COST_HYPOTHESIS.md`.

### 2.3 Equity consistency

| Check | Value |
|-------|------:|
| sum(closed pnl) | -4.370128 |
| `zeus_paper_positions.realized_pnl` | -4.370128 |
| Δ | ~0 |
| Open positions | 3 |

Realized matches sum of closed pnls. No ledger drift on these fields.

## 3. Pre-reg: channel narrow-stop hypothesis

Channel stops = граница + сжатый буфер → med stop_pct **0.71%** → costs_r med **0.37**.  
Лечение (research only): opposite boundary / structural extreme + buffer, width **≥ 2%**.  
Ретро-сим закрытых channel с широким стопом: **отложен** (нет opposite boundary в ledger). Тест после среза.

## 4. Calendar (unchanged)

| Date | Action |
|------|--------|
| 26–27.09 | n<10 check → extend window, no rule changes |
| 30.09 | entry_gates shadow: n, median, arrival/day |
| 06–07.10 | Slice: C+gates, TP C/A, HTF, **Zeus vs Main (incl. wedge-only)**, measure_two_week_review |

Open risk ~3% — в норме; сделки не форсировать.

---
*Collect-only. Live not touched.*
