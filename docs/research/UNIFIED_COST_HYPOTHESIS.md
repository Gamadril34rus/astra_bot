# Единая гипотеза издержек (unified cost hypothesis)

**Источник:** PR #138 (merged 2026-09-24) — ZEUS vs MAIN gap decomposition.  
**Статус:** зафиксировано до среза. Не «ещё один фактор», а **одна строка теории проекта**.

---

## Одна строка

> **Круг издержек ≈ 0.3% против ширины стопа решает всё:**  
> `costs_r ≈ 0.003 / stop_pct`  
> Узкий стоп → издержки съедают большую долю 1R. Широкий стоп → costs_r мал.  
> Объясняет **MAIN**, **zeus_channel_boundary_4h** и **zeus_wedge_retest_4h** одной формулой.

---

## Доказательство на снимке 24.09 (#138)

| Relationship (ZEUS closed) | Value |
|----------------------------|------:|
| corr(costs_r, stop_pct) | **−0.61** |
| corr(costs_r, **1/stop_pct**) | **+0.99** |

| Strategy | n | costs_r med | stop_pct med |
|----------|--:|------------:|-------------:|
| zeus_channel_boundary_4h | 15 | **0.37** | **0.71%** |
| zeus_wedge_retest_4h | 3 | **0.05** | **5.93%** |

MAIN (n=108 с обоими полями): stop_pct med **1.38%**, costs_r med **0.20**, тот же обратный паттерн (corr −0.79).

Примеры: AVAX stop_pct **0.17%** → costs_r **1.11**; ETH/XRP с stop_pct **6–12%** → costs_r **0.025–0.05**.

Модель издержек (`docs/COST_MODEL.md`): taker 0.05%×2 + slippage 0.1%×2 ≈ **0.3%** round-trip — совпадает с «кругом 0.3%».

---

## Следствия (не в бой до среза)

1. **Не лечить «4h не трендит»** — сначала смотреть stop width vs cost floor.  
2. **Channel boundary** с буфером у границы → часто stop_pct ≪ 1% → costs_r доминирует.  
3. **Wedge retest** с широким стопом (структурный экстремум) → costs_r низкий по той же формуле.  
4. MAIN 5m с stop ~1.4% — посередине шкалы.

---

## Связанные артефакты

- `reports/zeus_vs_main_decomposition_2026-09-24.md` (#138)  
- `reports/zeus_vs_main_2026-09-24.md`  
- `docs/research/DECISION_MEMO_2026-09-26.md` (пререгистрация среза)  
- `docs/COST_MODEL.md`

*Research-only. Live не трогать.*
